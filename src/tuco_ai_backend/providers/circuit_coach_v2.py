from __future__ import annotations

import json
from typing import Any

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
    build_level_child_guidance_instruction_for_level,
)
from tuco_ai_backend.tools import (
    CircuitCoachHighlightPortsArgs,
    HighlightEmptySlotArgs,
    available_circuit_coach_v2_tools,
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
    "玩家明确索取下一步时：若存在一条未连接的输出端到不同槽位未连接输入端，"
    "只能调用 highlight_ports；若还不能接线而需要新增积木，只能调用 highlight_empty_slot。"
    "每轮最多调用一次工具，工具调用时正文必须为空。"
    "只能使用 unlocked_gates 中的积木，不能建议未解锁积木。"
)


def _gate_name(value: str | int | None) -> str | None:
    return value.upper() if isinstance(value, str) else None


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
        "此时不要指导具体接线，也不得把信号标签或剧情名词当作积木名称。"
    )


def build_circuit_coach_v2_context(circuit: CircuitCoachV2Snapshot) -> str:
    return json.dumps(circuit.model_dump(by_alias=True), ensure_ascii=False, separators=(",", ":"))


def _normalize_decision(
    request: CircuitCoachDecisionRequest, decision: DecisionResponse
) -> DecisionResponse:
    tool_call = decision.tool_call
    if tool_call is None:
        return decision
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
    async def decide(
        self,
        request: CircuitCoachDecisionRequest,
        history: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
    ) -> DecisionResponse:
        api_key = self._config.api_key()
        if not api_key:
            raise LlmConfigurationError("LLM API key is not configured")
        response = await self._post(self._build_payload(request, history=history), api_key)
        decision = self._parse_response(
            self._decode_response(response), request.circuit_snapshot.board.topology_revision
        )
        return _normalize_decision(request, decision)

    def _build_payload(
        self,
        request: CircuitCoachDecisionRequest,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        circuit = request.circuit_snapshot
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
        unlocked_components = tuple(
            component
            for gate in circuit.unlocked_gates
            if (component := FIRMWARE_GATE_COMPONENTS.get(gate.upper())) is not None
        )
        guidance = build_level_child_guidance_instruction_for_level(
            circuit.level.id,
            request.user_text,
            unlocked_components=unlocked_components,
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
        return {
            "model": self._config.model,
            "stream": False,
            "messages": messages,
            "tools": available_circuit_coach_v2_tools(),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }

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
            if name == "highlight_ports":
                arguments: Any = CircuitCoachHighlightPortsArgs.model_validate(raw_arguments)
            elif name == "highlight_empty_slot":
                arguments = HighlightEmptySlotArgs.model_validate(raw_arguments)
            else:
                raise LlmProtocolError("unsupported tool call")
            return DecisionResponse(
                tool_call=ToolCall(call_id=call["id"], name=name, arguments=arguments),
                topology_revision=topology_revision,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LlmProtocolError("LLM response contains neither text nor a tool call")
        return DecisionResponse(assistant_text=content.strip(), topology_revision=topology_revision)
