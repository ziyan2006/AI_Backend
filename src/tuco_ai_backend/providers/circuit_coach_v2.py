from __future__ import annotations

import json
import logging
import traceback
from dataclasses import asdict
from typing import Any

import httpx

from tuco_ai_backend.circuit_diagnostics import CircuitDiagnosis, diagnose_circuit
from tuco_ai_backend.circuit_planner import (
    CircuitPlan,
    ConnectPortsAction,
    DisconnectPortsAction,
    PlaceGateAction,
    PlannedCandidate,
    plan_circuit_actions,
)
from tuco_ai_backend.config import RuntimeConfigStore
from tuco_ai_backend.evaluation_tracing import DecisionTraceSink, TraceEventName
from tuco_ai_backend.level_logic import get_level_logic_spec
from tuco_ai_backend.models import (
    CircuitCoachDecisionRequest,
    CircuitCoachV2Snapshot,
    DecisionResponse,
    ToolCall,
)
from tuco_ai_backend.providers.openai_compatible import (
    LlmConfigurationError,
    LlmProtocolError,
    OpenAICompatibleClient,
    _level_question_intent,
    build_level_child_guidance_instruction_for_level,
)
from tuco_ai_backend.semantic_action_gateway import (
    SemanticActionGateway,
    map_candidate_to_device_tool,
)
from tuco_ai_backend.tools import (
    ChooseCircuitActionArgs,
    CircuitCoachHighlightPortsArgs,
    HighlightEmptySlotArgs,
    choose_circuit_action_tool,
)

LOGGER = logging.getLogger(__name__)
_TRACE_RESPONSE_HEADERS = (
    "content-type",
    "x-request-id",
    "request-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
)

FIRMWARE_GATE_COMPONENTS = {
    "INPUT": "input",
    "OUTPUT": "output",
    "NOT": "not_gate",
    "AND": "and_gate",
    "OR": "or_gate",
    "NAND": "nand_gate",
    "NOR": "nor_gate",
    "XOR": "xor_gate",
    "XNOR": "xnor_gate",
}

CIRCUIT_COACH_V2_SYSTEM_PROMPT = (
    "你是图灵号电路指导员，陪伴儿童完成当前电路关卡。"
    "只依据 circuit_snapshot 和历史对话回答，不能编造硬件、连线或积木。"
    "关卡中的信号标签和目标不是积木名称，绝不能让孩子放置它们。"
    "普通聊天先自然回答，不要强行讲关卡。"
    "每次只推进一个小台阶，使用自然中文，不要使用 Sum、Carry 等英文术语。"
    "玩家明确索取下一步时，只能从后端给出的安全候选编号中选择一个，"
    "不能自行生成端口号、槽位号或积木类型。"
    "每轮最多调用一次工具；需要亮灯时，工具调用可以同时附带一句简短、自然的语音提示。"
    "工具调用附带的文字必须是孩子能直接听懂的接线或摆放提示，不能是“指令已发送”等传输确认；"
    "朗读内容不得提槽位、端口编号、上下左右或工具调用。"
    "只能使用 unlocked_gates 中的积木，不能建议未解锁积木。"
    "若本轮附有电路进度判断，它由有效连线自动得出，优先级高于关卡的通用搭建步骤；"
    "必须先利用其中指出的已放置积木，不能按某种门的数量猜测下一步。"
)

LEARNING_ACTIVITY_SYSTEM_PROMPT = (
    "你是图灵号的机载 AI 助手，正在陪儿童完成正式电路关卡前的小练习。"
    "只依据 learning_activity、circuit_snapshot 和历史对话回答，不能编造题目、积木或硬件。"
    "这是 0 和 1 的概念练习，不是搭电路环节：绝不能要求放输入、输出或逻辑门积木，"
    "也不能提及端口、连线、亮灯、工具调用或电路快照。"
    "孩子普通聊天时先自然回答；提问练习时每次只推进一个小台阶，使用短句和自然中文。"
    "孩子问这道练习在做什么时，只介绍规则和目标，不提前透露完整答案或某个位置必须是0/1。"
    "孩子没有明确要求直接答案时，只提示一个值得观察的位置、数值或差异，不要一次列出完整 0/1 摆法。"
    "孩子只要提示时，每次只指出一个 slot_roles 标签，并说清当前值、目标值和一个简短原因；"
    "不能只说“观察一下”或只报位置名称。"
    "孩子明确要求答案时，才清楚说出每个位置应为 0 还是 1，并用孩子能懂的话解释。"
    "介绍全加器时，先说 A、B 和进位输入三个 0/1 相加，个位留下结果，超过1的部分进入进位输出。"
    "当 learning_activity.kind 是 three_input_parity 时，这是三路求和前的奇偶练习："
    "三个槽位都是输入，current_decimal 表示当前有几格是1，target_decimal 表示目标个位结果。"
    "先数有几个1：1个或3个时个位是1，0个或2个时个位是0。"
    "孩子问练习在做什么时，先说“三个只会是0或1的小开关”，再说明只看个位；"
    "不要一开始就列出 A、B 或进位输入这些标签。"
    "target_bits 只是一种示例摆法，不是唯一答案；除非孩子明确索要答案，不要直接报出完整摆法。"
    "当 learning_activity.kind 是 three_input_carry 时，这是三路相加前的进位练习："
    "三个槽位都是输入，current_decimal 表示当前有几格是1，target_decimal 表示这一题的进位结果。"
    "先数有几个1：0个或1个时没有进位，2个或3个时有进位。"
    "孩子问练习在做什么时，先说“三个只会是0或1的小开关”，再解释多出来的1要送到下一位；"
    "不要一开始就列出 A、B 或进位输入这些标签。"
    "在这种练习中，target_bits 只表示本题需要放几个1，不限定这些1具体在哪些槽位；"
    "除非孩子明确索要答案，不要直接报出完整摆法。"
    "除非孩子明确要求答案，回复最多两句，控制在55个汉字左右。"
    "只输出可直接朗读的纯中文文本：不要使用 Markdown、星号、井号、反引号、列表符号或装饰性符号；"
    "尤其不要把 0 或 1 写成带星号的强调格式，直接说“0”或“1”。"
    "提到练习位置时，必须直接使用 learning_activity.slot_roles 里的可见标签："
    "二进制练习说“8的位置、4的位置、2的位置、1的位置”，加法练习说“A、B、进位输入、个位、进位输出”。"
    "绝不能说“第一个位置”“第二个位置”“这个位置”或“那个位置”。"
    "不用 Sum、Carry 等英文术语；不要主动发问或主动给出下一题。"
)


def _gate_name(value: str | int | None) -> str | None:
    return value.upper() if isinstance(value, str) else None


def _requires_tool_guidance_regeneration(text: str | None) -> bool:
    if not text or not text.strip():
        return True
    normalized = text.replace(" ", "")
    if not any("\u4e00" <= character <= "\u9fff" for character in normalized):
        return True
    position_terms = (
        "\u4e0a\u65b9",
        "\u4e0b\u65b9",
        "\u4e0a\u9762",
        "\u4e0b\u9762",
        "\u9876\u90e8",
        "\u5e95\u90e8",
        "\u5e95\u4e0b",
        "\u5de6\u8fb9",
        "\u53f3\u8fb9",
        "\u5de6\u4fa7",
        "\u53f3\u4fa7",
    )
    if any(term in normalized for term in position_terms):
        return True
    transport_terms = ("指令", "工具", "调用", "请求", "响应", "命令")
    delivery_terms = ("发送", "执行", "完成", "成功")
    return any(term in normalized for term in transport_terms) and any(
        term in normalized for term in delivery_terms
    )


def _safe_tool_guidance(decision: DecisionResponse) -> str:
    if decision.tool_call is not None and decision.tool_call.name == "highlight_ports":
        return "看一看亮起的两个光点，把它们用导线连起来吧。"
    return "看一看亮起的位置，把需要的积木放进去吧。"


def _valid_connections(
    circuit: CircuitCoachV2Snapshot,
) -> list[tuple[tuple[int, int, str, str], tuple[int, int, str, str]]]:
    ports = {
        port.port_id: (port.port_id, slot.slot_id, _gate_name(slot.gate) or "UNKNOWN", port.role)
        for slot in circuit.board.slots
        if slot.state == "present"
        for port in slot.ports
    }
    connections: list[tuple[tuple[int, int, str, str], tuple[int, int, str, str]]] = []
    for edge in circuit.board.edges:
        if edge.status != "valid":
            continue
        first = ports.get(edge.port_a)
        second = ports.get(edge.port_b)
        if first is None or second is None:
            continue
        if first[3] == "output" and second[3] == "input":
            connections.append((first, second))
        elif second[3] == "output" and first[3] == "input":
            connections.append((second, first))
    return connections


def _format_gate_reference(gate: str, slot_id: int) -> str:
    return f"{gate}@{slot_id}"


def _build_circuit_progress_instruction(
    circuit: CircuitCoachV2Snapshot, question: str
) -> str | None:
    if _level_question_intent(question) != "开始行动":
        return None

    valid_connections = _valid_connections(circuit)
    connected_outputs = {source[0] for source, _ in valid_connections}
    connected_inputs = {target[0] for _, target in valid_connections}
    logic_slots = [
        (slot, gate)
        for slot in circuit.board.slots
        if slot.state == "present"
        if (gate := _gate_name(slot.gate)) not in (None, "INPUT", "OUTPUT")
    ]
    incomplete_gates: list[tuple[int, int, str, list[int]]] = []
    for slot, gate in logic_slots:
        input_ports = [port.port_id for port in slot.ports if port.role == "input"]
        unconnected_inputs = [port for port in input_ports if port not in connected_inputs]
        if unconnected_inputs:
            connected_count = len(input_ports) - len(unconnected_inputs)
            incomplete_gates.append((connected_count, slot.slot_id, gate, unconnected_inputs))

    if incomplete_gates:
        connected_count, target_slot, target_gate, unconnected_inputs = sorted(
            incomplete_gates, key=lambda item: (-item[0], item[1])
        )[0]
        target_reference = _format_gate_reference(target_gate, target_slot)
        input_count = connected_count + len(unconnected_inputs)
        if connected_count == 0:
            state_description = f"{target_reference} 已摆放但还没有接入输入"
        else:
            state_description = (
                f"{target_reference} 已接好 {connected_count}/{input_count} 个输入，"
                f"还剩 {len(unconnected_inputs)} 个空输入端"
            )

        source_slots_already_used = {
            source[1]
            for source, target in valid_connections
            if target[1] == target_slot
        }
        input_sources = sorted(
            (
                (port.port_id, slot.slot_id, gate)
                for slot in circuit.board.slots
                if slot.state == "present"
                if (gate := _gate_name(slot.gate)) == "INPUT"
                if slot.slot_id not in source_slots_already_used
                for port in slot.ports
                if port.role == "output" and port.port_id not in connected_outputs
            ),
            key=lambda item: (item[1], item[0]),
        )
        ready_logic_sources = sorted(
            (
                (output_port.port_id, slot.slot_id, gate)
                for slot, gate in logic_slots
                if all(
                    port.port_id in connected_inputs
                    for port in slot.ports
                    if port.role == "input"
                )
                for output_port in slot.ports
                if output_port.role == "output" and output_port.port_id not in connected_outputs
            ),
            key=lambda item: (item[1], item[0]),
        )
        source_candidates = (
            ready_logic_sources + input_sources
            if target_gate in {"OR", "NOR"}
            else input_sources + ready_logic_sources
        )
        if source_candidates:
            source_port, source_slot, source_gate = source_candidates[0]
            source_reference = _format_gate_reference(source_gate, source_slot)
            target_port = unconnected_inputs[0]
            next_step = (
                f"本轮优先操作：把 {source_reference} 的未连接输出端接到 "
                f"{target_reference} 的空输入端。"
                f"若调用亮灯，只能使用 output_port={source_port} 与 input_port={target_port}。"
            )
        else:
            next_step = (
                f"本轮优先操作：先为 {target_reference} 选择一个尚未占用的输入信号，"
                "再接入一个空输入端。"
            )
        return (
            "电路进度判断（仅依据有效连线）："
            f"{state_description}。{next_step}"
            "先接好已有积木，不要建议新增同类逻辑门，也不要重新介绍关卡任务。"
        )

    ready_logic_outputs = sorted(
        (
            (output_port.port_id, slot.slot_id, gate)
            for slot, gate in logic_slots
            if all(port.port_id in connected_inputs for port in slot.ports if port.role == "input")
            for output_port in slot.ports
            if output_port.role == "output" and output_port.port_id not in connected_outputs
        ),
        key=lambda item: (item[1], item[0]),
    )
    output_targets = sorted(
        (
            (port.port_id, slot.slot_id, gate)
            for slot in circuit.board.slots
            if slot.state == "present"
            if (gate := _gate_name(slot.gate)) == "OUTPUT"
            for port in slot.ports
            if port.role == "input" and port.port_id not in connected_inputs
        ),
        key=lambda item: (item[1], item[0]),
    )
    if ready_logic_outputs and output_targets:
        source_port, source_slot, source_gate = ready_logic_outputs[0]
        target_port, target_slot, target_gate = output_targets[0]
        return (
            "电路进度判断（仅依据有效连线）："
            f"{_format_gate_reference(source_gate, source_slot)} 已收齐输入但输出尚未接出。"
            "本轮优先操作：把它的输出接到 "
            f"{_format_gate_reference(target_gate, target_slot)} 的输入端。"
            f"若调用亮灯，只能使用 output_port={source_port} 与 input_port={target_port}。"
            "先完成已有电路，不要重新介绍关卡任务或建议新增同类逻辑门。"
        )
    return None


def _connected_ports(circuit: CircuitCoachV2Snapshot) -> set[int]:
    return {port for edge in circuit.board.edges for port in (edge.port_a, edge.port_b)}


def _ports_by_id(circuit: CircuitCoachV2Snapshot) -> dict[int, tuple[int, str]]:
    return {
        port.port_id: (slot.slot_id, port.role)
        for slot in circuit.board.slots
        for port in slot.ports
    }


def _has_legal_port_pair(circuit: CircuitCoachV2Snapshot) -> bool:
    connected = _connected_ports(circuit)
    ports = _ports_by_id(circuit)
    return any(
        output_slot != input_slot
        and output_role == "output"
        and input_role == "input"
        and output_port not in connected
        and input_port not in connected
        for output_port, (output_slot, output_role) in ports.items()
        for input_port, (input_slot, input_role) in ports.items()
    )


def _missing_components_instruction(circuit: CircuitCoachV2Snapshot) -> str | None:
    present_gates = [
        _gate_name(slot.gate) for slot in circuit.board.slots if slot.state == "present"
    ]
    missing: list[str] = []
    input_missing = circuit.level.input_count - present_gates.count("INPUT")
    output_missing = circuit.level.output_count - present_gates.count("OUTPUT")
    if input_missing > 0:
        missing.append(f"{input_missing}块输入积木")
    if output_missing > 0:
        missing.append(f"{output_missing}块输出积木")
    if not missing:
        return None
    return (
        "当前电路尚未就绪，还缺" + "和".join(missing) + "。"
        "必须先自然、直接地回答用户刚刚的问题，再按需提醒补齐这些积木；"
        "提醒时必须直接说出缺少的准确数量，不能反问孩子还缺什么；"
        "此时不要指导具体接线，也不得把信号标签或剧情名词当作积木名称。"
    )


def build_circuit_coach_v2_context(circuit: CircuitCoachV2Snapshot) -> str:
    return json.dumps(circuit.model_dump(by_alias=True), ensure_ascii=False, separators=(",", ":"))


def build_learning_activity_context(request: CircuitCoachDecisionRequest) -> str:
    activity = request.learning_activity
    if activity is None:
        raise ValueError("learning activity context is required")
    return json.dumps(activity.model_dump(), ensure_ascii=False, separators=(",", ":"))


def _plan_for_request(request: CircuitCoachDecisionRequest) -> CircuitPlan | None:
    if (
        request.learning_activity is not None
        or _level_question_intent(request.user_text) != "开始行动"
        or request.circuit_snapshot.level.rule_version is None
    ):
        return None
    level = request.circuit_snapshot.level
    try:
        spec = get_level_logic_spec(level.id, level.rule_version)
    except KeyError:
        return None
    diagnosis = diagnose_circuit(request.circuit_snapshot, spec)
    if diagnosis.disconnect_edges:
        return CircuitPlan(
            candidates=tuple(
                PlannedCandidate(
                    candidate_id=(
                        f"rev{request.circuit_snapshot.board.topology_revision}-action-{index}"
                    ),
                    topology_revision=request.circuit_snapshot.board.topology_revision,
                    action=DisconnectPortsAction(
                        output_port=edge.output_port,
                        input_port=edge.input_port,
                    ),
                    score=(0, index),
                    child_facts=diagnosis.facts,
                    invalidated_output_indexes=frozenset(),
                )
                for index, edge in enumerate(diagnosis.disconnect_edges, start=1)
            ),
            preserved_output_indexes=frozenset(),
            search_states=0,
            elapsed_ms=0.0,
        )
    plan = plan_circuit_actions(request.circuit_snapshot, spec)
    return plan if plan.candidates and plan.degraded_reason is None else None


def _grounding_for_request(
    request: CircuitCoachDecisionRequest,
) -> tuple[CircuitPlan | None, CircuitDiagnosis | None]:
    if request.learning_activity is not None or request.circuit_snapshot.level.rule_version is None:
        return None, None
    intent = _level_question_intent(request.user_text)
    if intent not in {"提示求助", "检查诊断", "解释原理"}:
        return None, None
    level = request.circuit_snapshot.level
    try:
        spec = get_level_logic_spec(level.id, level.rule_version)
    except KeyError:
        return None, None
    if intent == "检查诊断":
        return None, diagnose_circuit(request.circuit_snapshot, spec)
    plan = plan_circuit_actions(request.circuit_snapshot, spec)
    usable_plan = plan if plan.candidates and plan.degraded_reason is None else None
    diagnosis = (
        diagnose_circuit(request.circuit_snapshot, spec)
        if intent == "解释原理"
        else None
    )
    return usable_plan, diagnosis


def _grounding_instruction(
    request: CircuitCoachDecisionRequest,
    plan: CircuitPlan | None,
    diagnosis: CircuitDiagnosis | None,
) -> str | None:
    intent = _level_question_intent(request.user_text)
    if intent == "提示求助" and plan is not None:
        candidate = plan.candidates[0]
        if isinstance(candidate.action, PlaceGateAction):
            action_fact = f"后续需要用{_gate_name(candidate.action.gate)}积木继续组合现有结果。"
        elif isinstance(candidate.action, DisconnectPortsAction):
            action_fact = "当前有一条错误连线需要先拆掉。"
        else:
            action_fact = "当前已有积木可以继续补上一条安全连线。"
        facts = "；".join((*candidate.child_facts, action_fact))
        return (
            f"可靠提示依据（来自当前真值表规划）：{facts}"
            "只把它改写成一个轻提示或观察问题，不要直接说完整接法，不要调用工具。"
        )
    if intent == "检查诊断" and diagnosis is not None and diagnosis.facts:
        return (
            "可靠电路诊断（来自当前真值表与有效连线）："
            + "；".join(diagnosis.facts)
            + "必须明确指出这条直连线有问题，再用一句话解释原因；不要调用工具。"
        )
    if intent == "解释原理" and plan is not None:
        candidate = plan.candidates[0]
        if isinstance(candidate.action, PlaceGateAction):
            gate_names = {
                "AND": "与门",
                "OR": "或门",
                "NOT": "非门",
                "NAND": "与非门",
                "NOR": "或非门",
                "XOR": "异或门",
                "XNOR": "同或门",
            }
            gate_name = gate_names.get(candidate.action.gate, candidate.action.gate)
            diagnosis_facts = (
                "；".join(diagnosis.facts)
                if diagnosis is not None and diagnosis.facts
                else "当前直接输出还不能覆盖本关的全部输入情况。"
            )
            return (
                f"原理解释依据：{diagnosis_facts}后续需要用{gate_name}积木继续组合结果。"
                "先解释当前积木为什么只覆盖部分情况，再自然说明这种积木负责汇总；"
                "不要调用工具。"
            )
    return None


def _candidate_instruction(plan: CircuitPlan) -> str:
    lines = ["本轮只能从以下安全候选中选择一个编号："]
    for candidate in plan.candidates:
        if isinstance(candidate.action, PlaceGateAction):
            action_text = f"摆放一块 {candidate.action.gate} 积木"
        elif isinstance(candidate.action, DisconnectPortsAction):
            action_text = "拆掉一条已经确认存在问题的连线"
        else:
            action_text = "连接一对已经验证安全的光点"
        facts = "；".join(candidate.child_facts)
        lines.append(f"- {candidate.candidate_id}：{action_text}。{facts}")
    lines.append("需要执行下一步时调用 choose_circuit_action，只填写 candidate_id。")
    return "\n".join(lines)


def _spoken_gate_name(gate: str | None) -> str | None:
    if gate is None:
        return None
    return {
        "INPUT": "输入积木",
        "OUTPUT": "输出积木",
        "NOT": "非门",
        "AND": "与门",
        "OR": "或门",
        "NAND": "与非门",
        "NOR": "或非门",
        "XOR": "异或门",
        "XNOR": "同或门",
    }.get(gate.upper(), gate)


def _gate_for_port(snapshot: CircuitCoachV2Snapshot, port_id: int) -> str | None:
    for slot in snapshot.board.slots:
        if any(port.port_id == port_id for port in slot.ports):
            return _spoken_gate_name(_gate_name(slot.gate))
    return None


def _candidate_spoken_text(
    candidate: PlannedCandidate,
    snapshot: CircuitCoachV2Snapshot | None = None,
) -> str:
    if isinstance(candidate.action, DisconnectPortsAction):
        return "先拆掉亮红灯的这条线。"
    if isinstance(candidate.action, PlaceGateAction):
        gate_names = {
            "INPUT": "输入",
            "OUTPUT": "输出",
            "NOT": "非门",
            "AND": "与门",
            "OR": "或门",
            "NAND": "与非门",
            "NOR": "或非门",
            "XOR": "异或门",
            "XNOR": "同或门",
        }
        if candidate.action.gate == "AND":
            return "先用与门判断两个条件是否同时成立，放一块与门积木吧。"
        if candidate.action.gate == "OR":
            return "接下来要汇总几路结果，先放一块或门积木吧。"
        return f"先放一块{gate_names.get(candidate.action.gate, candidate.action.gate)}积木吧。"
    if snapshot is not None and isinstance(candidate.action, ConnectPortsAction):
        source_gate = _gate_for_port(snapshot, candidate.action.output_port)
        target_gate = _gate_for_port(snapshot, candidate.action.input_port)
        if source_gate and target_gate:
            return f"把亮起的{source_gate}输出接到{target_gate}输入吧。"
    return "看一看亮起的两个光点，把它们用导线连起来吧。"


def _normalize_grounded_explanation(
    request: CircuitCoachDecisionRequest,
    decision: DecisionResponse,
    grounding_plan: CircuitPlan | None,
) -> DecisionResponse:
    if (
        _level_question_intent(request.user_text) != "解释原理"
        or grounding_plan is None
        or not decision.assistant_text
    ):
        return decision
    candidate = grounding_plan.candidates[0]
    if not isinstance(candidate.action, PlaceGateAction):
        return decision
    gate_name = _spoken_gate_name(candidate.action.gate)
    if gate_name is None or gate_name in decision.assistant_text:
        return decision
    first_line = next(
        (line.strip() for line in decision.assistant_text.splitlines() if line.strip()),
        decision.assistant_text.strip(),
    )
    if candidate.action.gate == "OR":
        second_line = "所以还要用或门把几路结果汇总起来。"
    elif candidate.action.gate == "AND":
        second_line = "所以还要用与门继续判断两个条件是否同时成立。"
    else:
        second_line = f"所以还需要一块{gate_name}积木继续组合这些结果。"
    return DecisionResponse(
        assistant_text=f"{first_line}\n{second_line}",
        topology_revision=decision.topology_revision,
    )


def _map_candidate_to_decision(
    candidate: PlannedCandidate,
    snapshot: CircuitCoachV2Snapshot,
    assistant_text: str | None,
) -> DecisionResponse | None:
    tool_call = map_candidate_to_device_tool(candidate, snapshot)
    if tool_call is None:
        return None
    spoken_text = (
        assistant_text
        if assistant_text and not _requires_tool_guidance_regeneration(assistant_text)
        else _candidate_spoken_text(candidate, snapshot)
    )
    return DecisionResponse(
        assistant_text=spoken_text,
        tool_call=tool_call,
        topology_revision=candidate.topology_revision,
    )


def _normalize_decision(
    request: CircuitCoachDecisionRequest, decision: DecisionResponse
) -> DecisionResponse:
    tool_call = decision.tool_call
    if tool_call is None:
        return decision
    if request.learning_activity is not None:
        return DecisionResponse(
            assistant_text=decision.assistant_text
            or "这一步我们先用 0 和 1 想一想，不需要操作电路。",
            topology_revision=request.circuit_snapshot.board.topology_revision,
        )
    circuit = request.circuit_snapshot
    if tool_call.name == "highlight_ports":
        arguments = tool_call.arguments
        ports = _ports_by_id(circuit)
        connected = _connected_ports(circuit)
        output = ports.get(arguments.output_port)
        input_port = ports.get(arguments.input_port)
        if (
            output is None
            or input_port is None
            or output[0] == input_port[0]
            or output[1] != "output"
            or input_port[1] != "input"
            or arguments.output_port in connected
            or arguments.input_port in connected
        ):
            return DecisionResponse(
                assistant_text="我先确认一下这一对端口，再给你下一步提示。",
                topology_revision=circuit.board.topology_revision,
            )
        return decision

    arguments = tool_call.arguments
    slot = next((item for item in circuit.board.slots if item.slot_id == arguments.slot), None)
    if (
        slot is None
        or slot.state != "empty"
        or arguments.gate.upper() not in {_gate_name(gate) for gate in circuit.unlocked_gates}
    ):
        return DecisionResponse(
            assistant_text="我们先看看现在能直接连接哪一对端口。",
            topology_revision=circuit.board.topology_revision,
        )
    return decision


class CircuitCoachV2Client(OpenAICompatibleClient):
    def __init__(
        self,
        config: RuntimeConfigStore,
        *,
        http_client: httpx.AsyncClient | None = None,
        trace_sink: DecisionTraceSink | None = None,
    ) -> None:
        super().__init__(config, http_client=http_client)
        self._trace_sink = trace_sink

    def _trace(self, trace_id: str | None, event: TraceEventName, payload: Any) -> None:
        if trace_id is None or self._trace_sink is None:
            return
        try:
            self._trace_sink.record(trace_id, event, payload)
        except Exception as exc:
            LOGGER.warning(
                "decision trace sink failed: trace_id=%s event=%s error=%s",
                trace_id,
                event,
                type(exc).__name__,
            )

    async def decide(
        self,
        request: CircuitCoachDecisionRequest,
        history: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
    ) -> DecisionResponse:
        try:
            self._trace(
                trace_id,
                "decision_request",
                request.model_dump(mode="json"),
            )
            self._trace(trace_id, "conversation_history", history or [])
            api_key = self._config.api_key()
            if not api_key:
                raise LlmConfigurationError("LLM API key is not configured")
            plan = _plan_for_request(request)
            grounding_plan, diagnosis = _grounding_for_request(request)
            semantic_trace: Any = None
            if plan is not None:
                semantic_trace = asdict(plan)
            elif grounding_plan is not None and diagnosis is not None:
                semantic_trace = {
                    "plan": asdict(grounding_plan),
                    "diagnosis": asdict(diagnosis),
                }
            elif grounding_plan is not None:
                semantic_trace = asdict(grounding_plan)
            elif diagnosis is not None:
                semantic_trace = {"diagnosis": asdict(diagnosis)}
            self._trace(trace_id, "semantic_plan", semantic_trace)
            payload = self._build_payload(
                request,
                history=history,
                plan=plan,
                grounding_plan=grounding_plan,
                diagnosis=diagnosis,
            )
            self._trace(trace_id, "provider_request", payload)
            response = await self._post(payload, api_key)
            response_payload = self._decode_response(response)
            self._trace(
                trace_id,
                "provider_response",
                {
                    "status_code": response.status_code,
                    "headers": {
                        header: response.headers[header]
                        for header in _TRACE_RESPONSE_HEADERS
                        if header in response.headers
                    },
                    "body": response_payload,
                },
            )
            decision = self._parse_response(
                response_payload,
                request.circuit_snapshot.board.topology_revision,
            )
            if plan is None:
                if decision.tool_call is not None:
                    final_decision = DecisionResponse(
                        assistant_text=decision.assistant_text
                        or "我们先聊聊你的问题，这一轮不操作电路。",
                        topology_revision=request.circuit_snapshot.board.topology_revision,
                    )
                else:
                    final_decision = _normalize_grounded_explanation(
                        request,
                        decision,
                        grounding_plan,
                    )
            else:
                gateway = SemanticActionGateway(plan)
                candidate = None
                selected_by_model = False
                if (
                    decision.tool_call is not None
                    and decision.tool_call.name == "choose_circuit_action"
                ):
                    candidate = gateway.resolve(
                        decision.tool_call.arguments.candidate_id,
                        topology_revision=request.circuit_snapshot.board.topology_revision,
                    )
                    selected_by_model = candidate is not None
                if candidate is None:
                    candidate = plan.candidates[0]
                mapped = _map_candidate_to_decision(
                    candidate,
                    request.circuit_snapshot,
                    decision.assistant_text if selected_by_model else None,
                )
                final_decision = (
                    DecisionResponse(
                        assistant_text="电路刚刚发生了变化，请再问我一次下一步。",
                        topology_revision=request.circuit_snapshot.board.topology_revision,
                    )
                    if mapped is None
                    else _normalize_decision(request, mapped)
                )
            self._trace(
                trace_id,
                "normalized_decision",
                final_decision.model_dump(mode="json"),
            )
            return final_decision
        except Exception as exc:
            self._trace(
                trace_id,
                "exception",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "stack": traceback.format_exc(),
                },
            )
            raise

    async def _generate_tool_guidance(
        self,
        initial_payload: dict[str, Any],
        decision: DecisionResponse,
        api_key: str,
    ) -> str:
        if decision.tool_call is None:
            raise LlmProtocolError("tool guidance requested without a tool call")
        messages = initial_payload.get("messages")
        if not isinstance(messages, list):
            raise LlmProtocolError("initial circuit coach payload has no messages")
        planned_action = json.dumps(
            {
                "name": decision.tool_call.name,
                "arguments": decision.tool_call.arguments.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        guidance_payload = {
            "model": self._config.model,
            "stream": False,
            "messages": [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "设备已经准备执行这个提示动作："
                        f"{planned_action}。现在请只说一句给孩子听的自然中文，"
                        "提醒他观察亮起的光点后完成下一步。"
                        "不要提工具、函数、端口编号、JSON、英文或上下左右，也不要再调用工具。"
                    ),
                },
            ],
        }
        response = await self._post(guidance_payload, api_key)
        guidance = self._parse_response(
            self._decode_response(response), decision.topology_revision
        )
        if guidance.tool_call is not None or _requires_tool_guidance_regeneration(
            guidance.assistant_text
        ):
            return _safe_tool_guidance(decision)
        return guidance.assistant_text

    def _build_payload(
        self,
        request: CircuitCoachDecisionRequest,
        history: list[dict[str, str]] | None = None,
        plan: CircuitPlan | None = None,
        grounding_plan: CircuitPlan | None = None,
        diagnosis: CircuitDiagnosis | None = None,
    ) -> dict[str, Any]:
        circuit = request.circuit_snapshot
        if request.learning_activity is not None:
            messages: list[dict[str, str]] = [
                {"role": "system", "content": LEARNING_ACTIVITY_SYSTEM_PROMPT}
            ]
            if history:
                messages.extend(
                    {"role": item["role"], "content": item["content"]}
                    for item in history[-6:]
                    if item.get("role") in ("user", "assistant") and item.get("content")
                )
            content = (
                f"学习活动状态：{build_learning_activity_context(request)}\n\n"
                f"用户问题：{request.user_text}\n\n"
                "本轮回答要求（必须遵守）：不要操作或指导电路；"
                "不要提空槽、积木、端口、连线或亮灯；"
                "本轮没有端口高亮工具可用。"
            )
            messages.append({"role": "user", "content": content})
            return {
                "model": self._config.model,
                "stream": False,
                "messages": messages,
                "tool_choice": "none",
            }
        messages: list[dict[str, str]] = [
            {"role": "system", "content": CIRCUIT_COACH_V2_SYSTEM_PROMPT}
        ]
        if history:
            messages.extend(
                {"role": item["role"], "content": item["content"]}
                for item in history[-6:]
                if item.get("role") in ("user", "assistant") and item.get("content")
            )
        instructions = [
            instruction for instruction in [_missing_components_instruction(circuit)] if instruction
        ]
        if plan is None and grounding_plan is None and diagnosis is None:
            grounding_plan, diagnosis = _grounding_for_request(request)
        grounding_instruction = _grounding_instruction(
            request,
            grounding_plan,
            diagnosis,
        )
        progress_instruction = (
            _candidate_instruction(plan)
            if plan is not None
            else grounding_instruction
            or _build_circuit_progress_instruction(circuit, request.user_text)
        )
        if progress_instruction:
            instructions.append(progress_instruction)
        unlocked_components = tuple(
            component
            for gate in circuit.unlocked_gates
            if (component := FIRMWARE_GATE_COMPONENTS.get(gate.upper())) is not None
        )
        guidance = build_level_child_guidance_instruction_for_level(
            circuit.level.id,
            request.user_text,
            unlocked_components=unlocked_components,
            has_actionable_circuit_progress=progress_instruction is not None,
        )
        if guidance:
            instructions.append(guidance)
        content = (
            f"circuit_snapshot：{build_circuit_coach_v2_context(circuit)}\n\n"
            f"用户问题：{request.user_text}"
        )
        if instructions:
            content += "\n\n本轮回答要求（必须遵守）：\n" + "\n\n".join(instructions)
        messages.append({"role": "user", "content": content})
        payload = {
            "model": self._config.model,
            "stream": False,
            "messages": messages,
            "tool_choice": "none",
        }
        if plan is not None:
            payload.update(
                {
                    "tools": [choose_circuit_action_tool()],
                    "tool_choice": "auto",
                    "parallel_tool_calls": False,
                }
            )
        return payload

    @staticmethod
    def _parse_response(payload: dict[str, Any], topology_revision: int) -> DecisionResponse:
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmProtocolError("LLM response does not contain a message") from exc
        tool_calls = message.get("tool_calls") or []
        if len(tool_calls) > 1:
            raise LlmProtocolError("parallel tool calls are not supported")
        if tool_calls:
            call = tool_calls[0]
            function = call.get("function") or {}
            name = function.get("name")
            try:
                raw_arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise LlmProtocolError("tool arguments are not valid JSON") from exc
            if name == "choose_circuit_action":
                arguments: Any = ChooseCircuitActionArgs.model_validate(raw_arguments)
            elif name == "highlight_ports":
                arguments: Any = CircuitCoachHighlightPortsArgs.model_validate(raw_arguments)
            elif name == "highlight_empty_slot":
                arguments = HighlightEmptySlotArgs.model_validate(raw_arguments)
            else:
                raise LlmProtocolError("unsupported tool call")
            content = message.get("content")
            assistant_text = (
                content.strip() if isinstance(content, str) and content.strip() else None
            )
            return DecisionResponse(
                assistant_text=assistant_text,
                tool_call=ToolCall(call_id=call["id"], name=name, arguments=arguments),
                topology_revision=topology_revision,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LlmProtocolError("LLM response contains neither text nor a tool call")
        return DecisionResponse(assistant_text=content.strip(), topology_revision=topology_revision)
