from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

import httpx

from tuco_ai_backend.config import RuntimeConfigStore
from tuco_ai_backend.models import CircuitSnapshot, DecisionRequest, DecisionResponse, ToolCall
from tuco_ai_backend.tools import HighlightPortsArgs, available_tools

LOGGER = logging.getLogger(__name__)


class LlmConfigurationError(RuntimeError):
    pass


class LlmProtocolError(RuntimeError):
    pass


GATE_NAMES = {
    0: ("input", "输入积木"),
    1: ("output", "输出积木"),
    2: ("not_gate", "非门积木"),
    3: ("and_gate", "与门积木"),
    4: ("or_gate", "或门积木"),
    5: ("nand_gate", "与非门积木"),
    6: ("nor_gate", "或非门积木"),
    7: ("xor_gate", "异或门积木"),
    8: ("xnor_gate", "同或门积木"),
}

PLACEABLE_COMPONENT_NAMES = "、".join(display_name for _, display_name in GATE_NAMES.values())


class CircuitReadiness(StrEnum):
    READY = "ready"
    MISSING_INPUT = "missing_input"
    MISSING_OUTPUT = "missing_output"
    MISSING_INPUT_OUTPUT = "missing_input_output"


@dataclass(frozen=True)
class MissingComponentsState:
    readiness: CircuitReadiness
    missing_input_count: int = 0
    missing_output_count: int = 0

SYSTEM_PROMPT = (
    "你是图灵号飞船上的电路指导员，正在陪伴儿童修复飞船。"
    "你熟悉输入积木、输出积木、逻辑门和端口连线。"
    f"玩家可放置积木只有：{PLACEABLE_COMPONENT_NAMES}。"
    "关卡中的输入信号标签、输出信号标签和剧情目标都不是积木名称，"
    "绝不能要求玩家放置它们。"
    "只根据提供的当前关卡和实际检测电路指导，不要编造不存在的积木、连接或结果。"
    "每次只回答玩家刚才的问题，不要开场寒暄、复述问题、罗列完整电路或重复已知信息。"
    "普通回复控制在45到60个汉字，使用一到两句自然中文；可用“好呀”“我们先试试”等亲切语气。"
    "二进制加法教学关在解释关卡目标或原理时可使用两到三句、80到120个汉字。"
    "不要直接说完整答案，只给一个明确的方向性小提示。"
    "描述连线时，只说哪个积木的输出端连接到哪个积木的输入端；"
    "不要提及API、网络、扫描、槽位号、端口编号或上下左右等开发与硬件术语。"
    "语气要活泼、耐心，像一起冒险的队友。"
    "玩家询问亮灯、位置、怎么接、从哪里接到哪里，或你的回答提到从一个积木接到另一个积木时，"
    "只有能确定一条具体接线时才调用highlight_ports。调用时必须同时提供一段可直接朗读的接线提示，"
    "不能只调用工具或留空回复；系统会先完整朗读，再亮灯。"
    "工具端口映射仅供调用工具使用，绝不能在朗读内容中说出端口编号。"
    "每次只能指导一条接线：ports必须只包含一个输出端和一个对应输入端，"
    "顺序为[输出端, 输入端]，共两个端口。"
    "绝不能把整块积木的四个端口或两块积木的八个端口一起点亮。"
    "duration_ms默认20000；仅当玩家明确说明亮灯时长时才按其要求调整，范围500到60000。"
)

BINARY_ADDER_LESSON_FOCUS = {
    401: (
        "这关先做一个二进制小计算器的个位部分，让两个 0 或 1 相加后告诉我们个位结果 Sum。"
        "十进制 8+5=13 时，3 留在个位；二进制 1+1=10 时，0 留在个位。"
    ),
    402: (
        "这关继续做二进制小计算器的进位部分，看看两个 1 相加时要不要送出 Carry。"
        "十进制 8+5=13 时，1 要进到十位；二进制 1+1=10 时，多出来的 1 就是进位。"
    ),
    403: (
        "这关要做一个小计算器，不过它只计算 0 和 1；"
        "把两个输入相加后，同时给出个位 Sum 和进位 Carry。"
        "十进制 8+5=13 时，3 留在个位、1 进到十位；二进制 1+1=10 时，0 给 Sum、1 给 Carry。"
    ),
    501: (
        "这关把小计算器升级成能算三个 0 或 1，先让它给出个位结果 Sum。"
        "十进制 8+5+1=14 时，4 留在个位；二进制 1+1+1=11 时，个位是 1。"
    ),
    502: (
        "这关让三输入小计算器找出什么时候要送出进位 Carry。"
        "十进制 8+5=13 会进 1；二进制 1+1+0=10 也会进 1。"
    ),
    503: (
        "这关让小计算器把不同地方送来的进位合在一起，得到最后的 Carry。"
        "十进制 8+5=13 会送出一个十位的 1；二进制 1+1=10 也会送出一个进位 1。"
    ),
    504: (
        "这关要完成一个能算三个 0 或 1 的小计算器，同时给出 Sum 和 Carry。"
        "十进制 8+5+1=14 时，4 留在个位、1 进到十位；二进制 1+1+1=11 时，1 给 Sum、1 给 Carry。"
    ),
}


def _slot_component(slot: dict[str, Any]) -> tuple[str, str]:
    component = slot.get("component")
    display_name = slot.get("display_name")
    if isinstance(component, str) and component:
        if isinstance(display_name, str) and display_name:
            return component, display_name
        return component, component
    gate = slot.get("gate")
    if isinstance(gate, int) and gate in GATE_NAMES:
        return GATE_NAMES[gate]
    return "unknown", "未知积木"


def _is_recognized_slot(slot: dict[str, Any]) -> bool:
    return (
        slot.get("present") is not False
        and slot.get("id_valid") is not False
        and isinstance(slot.get("slot"), int)
    )


def _component_counts(slots: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for slot in slots:
        if not _is_recognized_slot(slot):
            continue
        component, _ = _slot_component(slot)
        if component != "unknown":
            counts[component] = counts.get(component, 0) + 1
    return counts


def _missing_components_state(circuit: CircuitSnapshot) -> MissingComponentsState:
    if circuit.level is None:
        return MissingComponentsState(CircuitReadiness.READY)

    counts = _component_counts(circuit.slots)
    missing_input_count = max(circuit.level.input_count - counts.get("input", 0), 0)
    missing_output_count = max(circuit.level.output_count - counts.get("output", 0), 0)
    if missing_input_count and missing_output_count:
        readiness = CircuitReadiness.MISSING_INPUT_OUTPUT
    elif missing_input_count:
        readiness = CircuitReadiness.MISSING_INPUT
    elif missing_output_count:
        readiness = CircuitReadiness.MISSING_OUTPUT
    else:
        readiness = CircuitReadiness.READY
    return MissingComponentsState(readiness, missing_input_count, missing_output_count)


def build_missing_components_instruction(circuit: CircuitSnapshot) -> str | None:
    state = _missing_components_state(circuit)
    if state.readiness is CircuitReadiness.READY:
        return None

    missing: list[str] = []
    if state.missing_input_count:
        missing.append(f"{state.missing_input_count}块输入积木")
    if state.missing_output_count:
        missing.append(f"{state.missing_output_count}块输出积木")
    return (
        "当前电路尚未就绪。"
        f"还缺{'和'.join(missing)}。"
        "必须先自然、直接地回答用户刚刚的问题，然后在同一回复中用你自己的儿童友好措辞"
        "提醒玩家补齐这些积木；此时不要指导具体接线。"
        "不得把输入或输出信号标签、关卡目标中的名词当作积木名称；"
        "本轮缺积木提醒只能提及输入积木、输出积木及其缺少数量。"
    )


def build_binary_adder_instruction(circuit: CircuitSnapshot) -> str | None:
    if circuit.level is None:
        return None
    focus = BINARY_ADDER_LESSON_FOCUS.get(circuit.level.level_id)
    if focus is None:
        return None
    return (
        "二进制加法教学规则：关卡技术描述仅供理解电路，不能直接复述给孩子。"
        "当用户询问本关要做什么、原理是什么或如何开始时，先告诉孩子本关要做什么，再用十进制类比解释，"
        "最后说明对应的二进制算式和当前要完成的部分。不要一开始用十进制算式开头。"
        "不要使用“奇偶”“多数信号”“两两相加”“局部溢出”这些只给结论的说法。"
        "不要只让孩子猜答案；要解释 0、1、个位和进位各表示什么。"
        f"本关教学重点：{focus}"
    )


def _slot_labels(slots: list[dict[str, Any]]) -> dict[int, str]:
    counts: dict[str, int] = {}
    records: list[tuple[int, str, str]] = []
    for slot in slots:
        if not _is_recognized_slot(slot):
            continue
        component, display_name = _slot_component(slot)
        if component == "unknown":
            continue
        counts[component] = counts.get(component, 0) + 1
        records.append((slot["slot"], component, display_name))

    seen: dict[str, int] = {}
    labels: dict[int, str] = {}
    for slot_id, component, display_name in records:
        seen[component] = seen.get(component, 0) + 1
        labels[slot_id] = (
            f"第{seen[component]}块{display_name}"
            if counts[component] > 1
            else display_name
        )
    return labels


def _slot_ports(slot: int) -> list[int]:
    return list(range(slot * 4, slot * 4 + 4))


def _uses_firmware_links(circuit: CircuitSnapshot) -> bool:
    return "links" in circuit.model_fields_set


def _valid_link_slots(circuit: CircuitSnapshot) -> list[tuple[int, int]]:
    if _uses_firmware_links(circuit):
        links: list[tuple[int, int]] = []
        for link in circuit.links:
            first_port = link.get("first_port")
            second_port = link.get("second_port")
            if (
                link.get("valid") is not True
                or link.get("error") != 0
                or not isinstance(first_port, int)
                or not isinstance(second_port, int)
                or not 0 <= first_port < 64
                or not 0 <= second_port < 64
            ):
                continue
            first_slot = first_port // 4
            second_slot = second_port // 4
            if len(circuit.port_roles) == 64:
                first_role = circuit.port_roles[first_port]
                second_role = circuit.port_roles[second_port]
                if first_role == 1 and second_role == 2:
                    first_slot, second_slot = second_slot, first_slot
            links.append((first_slot, second_slot))
        return links

    links = []
    for link in circuit.valid_links:
        source = link.get("from_slot")
        target = link.get("to_slot")
        if isinstance(source, int) and isinstance(target, int):
            links.append((source, target))
    return links


def _valid_link_port_pair(circuit: CircuitSnapshot) -> list[int] | None:
    if not _uses_firmware_links(circuit):
        return None
    for link in circuit.links:
        first_port = link.get("first_port")
        second_port = link.get("second_port")
        if (
            link.get("valid") is not True
            or link.get("error") != 0
            or not isinstance(first_port, int)
            or not isinstance(second_port, int)
            or not 0 <= first_port < 64
            or not 0 <= second_port < 64
        ):
            continue
        return _single_connection_port_pair(circuit, [first_port, second_port])
    return None


def _single_connection_port_pair(
    circuit: CircuitSnapshot, ports: list[int]
) -> list[int] | None:
    unique_ports = list(dict.fromkeys(port for port in ports if 0 <= port < 64))
    if len(circuit.port_roles) == 64:
        output_ports = [port for port in unique_ports if circuit.port_roles[port] == 2]
        input_ports = [port for port in unique_ports if circuit.port_roles[port] == 1]
        if output_ports and input_ports:
            return [output_ports[0], input_ports[0]]
        return None
    if len(unique_ports) == 2:
        return unique_ports
    return None


def _invalid_link_count(circuit: CircuitSnapshot) -> int:
    if _uses_firmware_links(circuit):
        return circuit.invalid_link_count
    return len(circuit.invalid_links)


def _highlight_slots_from_text(circuit: CircuitSnapshot, text: str) -> list[int]:
    labels = _slot_labels(circuit.slots)
    mentioned = [slot for slot, label in labels.items() if label in text]
    if len(mentioned) >= 2:
        return mentioned[:2]
    if any(word in text for word in ("亮灯", "怎么接", "接到", "连接")):
        for source, target in _valid_link_slots(circuit):
            return [source, target]
    return []


def _needs_highlight(question: str, answer: str) -> bool:
    text = question + answer
    return any(word in text for word in ("亮灯", "怎么接", "接到", "连接", "哪里接", "位置"))


def _fallback_highlight(request: DecisionRequest, answer: str) -> ToolCall | None:
    if not _needs_highlight(request.question, answer):
        return None
    ports = _valid_link_port_pair(request.circuit)
    if ports is None:
        return None
    return ToolCall(
        call_id=f"fallback-{uuid4().hex}",
        name="highlight_ports",
        arguments=HighlightPortsArgs(
            ports=ports,
            duration_ms=20000,
            pattern="pulse",
            reason="标记一条已识别连接的两个端口",
        ),
    )


def _highlight_guidance_text(circuit: CircuitSnapshot, ports: list[int]) -> str:
    if len(ports) != 2:
        return "请把亮起的这一对端口连起来。"
    labels = _slot_labels(circuit.slots)
    source_label = labels.get(ports[0] // 4)
    target_label = labels.get(ports[1] // 4)
    if source_label and target_label:
        return f"请把亮起的{source_label}输出端和{target_label}输入端连起来。"
    return "请把亮起的输出端和输入端连起来。"


def _invalid_highlight_guidance_text(circuit: CircuitSnapshot, ports: list[int]) -> str:
    labels = _slot_labels(circuit.slots)
    slots = list(dict.fromkeys(port // 4 for port in ports if 0 <= port < 64))
    if len(slots) >= 2:
        source_label = labels.get(slots[0])
        target_label = labels.get(slots[1])
        if source_label and target_label:
            return f"请从{source_label}的输出端，接到{target_label}的一个输入端。"
    return "请确认输出端和输入端后，再连接这一条线。"


def _normalize_highlight_decision(
    request: DecisionRequest, decision: DecisionResponse
) -> DecisionResponse:
    if decision.tool_call is None:
        return decision
    ports = _single_connection_port_pair(request.circuit, decision.tool_call.arguments.ports)
    if ports is None:
        ports = _valid_link_port_pair(request.circuit)
    if ports is None:
        LOGGER.warning(
            "highlight_ports ignored because it does not identify one output-to-input pair: %s",
            decision.tool_call.arguments.ports,
        )
        return DecisionResponse(
            assistant_text=decision.assistant_text
            or _invalid_highlight_guidance_text(
                request.circuit, decision.tool_call.arguments.ports
            ),
            topology_revision=decision.topology_revision,
        )
    tool_call = ToolCall(
        call_id=decision.tool_call.call_id,
        name=decision.tool_call.name,
        arguments=decision.tool_call.arguments.model_copy(update={"ports": ports}),
    )
    return DecisionResponse(
        assistant_text=decision.assistant_text
        or _highlight_guidance_text(request.circuit, ports),
        tool_call=tool_call,
        topology_revision=decision.topology_revision,
    )


def build_circuit_context(circuit: CircuitSnapshot) -> str:
    parts: list[str] = []
    if circuit.level is not None:
        level = circuit.level
        parts.append(f"当前关卡：第{level.level_id}关。目标：{level.short_goal}。")
        if level.input_names:
            parts.append(f"输入信号标签（不是积木）：{level.input_names}。")
        if level.output_names:
            parts.append(f"输出信号标签（不是积木）：{level.output_names}。")
    else:
        parts.append("当前关卡信息未上传，只能依据电路状态给提示。")

    labels = _slot_labels(circuit.slots)
    component_counts = _component_counts(circuit.slots)
    if labels:
        counts: dict[str, int] = {}
        for label in labels.values():
            base = label.split("块", 1)[-1] if "块" in label else label
            counts[base] = counts.get(base, 0) + 1
        modules = "；".join(f"{name}{count}块" for name, count in counts.items())
        parts.append(f"已识别积木：{modules}。")
    else:
        parts.append("暂未识别到积木。")

    connections: list[str] = []
    for from_slot, to_slot in _valid_link_slots(circuit):
        source = labels.get(from_slot)
        target = labels.get(to_slot)
        if source and target:
            connections.append(f"{source}的输出端连接到{target}的输入端")
    if connections:
        parts.append("当前有效连接：" + "；".join(connections) + "。")
    else:
        parts.append("暂未确认可描述的有效连接。")
    invalid_link_count = _invalid_link_count(circuit)
    if invalid_link_count:
        parts.append(f"另有{invalid_link_count}条连接不完整或方向不合适，不能当作有效电路。")
    if labels:
        ports = "；".join(
            f"{label}={','.join(str(port) for port in _slot_ports(slot))}"
            for slot, label in labels.items()
        )
        parts.append(f"工具端口映射（不可朗读）：{ports}。")
        if len(circuit.port_roles) == 64:
            directional_ports: list[str] = []
            for slot, label in labels.items():
                slot_ports = _slot_ports(slot)
                outputs = [port for port in slot_ports if circuit.port_roles[port] == 2]
                inputs = [port for port in slot_ports if circuit.port_roles[port] == 1]
                directions: list[str] = []
                if outputs:
                    directions.append(f"输出端={outputs}")
                if inputs:
                    directions.append(f"输入端={inputs}")
                if directions:
                    directional_ports.append(f"{label}: {', '.join(directions)}")
            if directional_ports:
                parts.append(
                    "工具端口方向映射（不可朗读）：" + "；".join(directional_ports) + "。"
                )
    if circuit.level is not None:
        missing: list[str] = []
        for component, display, required in (
            ("input", "输入积木", circuit.level.input_count),
            ("output", "输出积木", circuit.level.output_count),
        ):
            actual = component_counts.get(component, 0)
            if actual < required:
                missing.append(f"还差{required - actual}块{display}")
        if missing:
            parts.append("优先提示：" + "，".join(missing) + "；先补齐积木，不要给接线建议。")
    return "\n".join(parts)


class OpenAICompatibleClient:
    def __init__(
        self,
        config: RuntimeConfigStore,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http_client = http_client
        self._shared_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        if self._shared_client is None or self._shared_client.is_closed:
            self._shared_client = httpx.AsyncClient()
        return self._shared_client

    async def close(self) -> None:
        if self._shared_client is not None and not self._shared_client.is_closed:
            await self._shared_client.aclose()
            self._shared_client = None

    async def decide(
        self,
        request: DecisionRequest,
        history: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
    ) -> DecisionResponse:
        tr_id = trace_id or f"tr_{int(time.time())}"
        api_key = self._config.api_key()
        if not api_key:
            raise LlmConfigurationError("LLM API key is not configured")

        payload = self._build_payload(request, history=history)
        LOGGER.info("[%s] [LLM-REQUEST] 发送大模型载荷: Model=%s, MessagesCount=%d",
                    tr_id, self._config.model, len(payload.get("messages", [])))
        
        response = await self._post(payload, api_key)
        resp_json = response.json()
        
        decision = self._parse_response(resp_json, request.circuit.topology_revision)
        decision = _normalize_highlight_decision(request, decision)
        
        # 记录模型响应日志
        if decision.tool_call:
            LOGGER.info(
                "[%s] [LLM-RESPONSE] 模型决定调用工具 ToolCall: ID=%s, Name=%s, Args=%s",
                tr_id,
                decision.tool_call.call_id,
                decision.tool_call.name,
                decision.tool_call.arguments,
            )
        else:
            LOGGER.info(
                "[%s] [LLM-RESPONSE] 模型纯文本回复 Text: %s",
                tr_id,
                decision.assistant_text,
            )

        if decision.tool_call is None and decision.assistant_text:
            decision.tool_call = _fallback_highlight(request, decision.assistant_text)
            LOGGER.info("[%s] [LLM-RESPONSE] 触发后置兜底高亮工具 (基于回复文本生成)", tr_id)

        return decision

    async def complete_after_tool(
        self,
        request: DecisionRequest,
        decision: DecisionResponse,
        result: dict[str, Any],
    ) -> str:
        if decision.tool_call is None:
            return decision.assistant_text or ""
        api_key = self._config.api_key()
        if not api_key:
            raise LlmConfigurationError("LLM API key is not configured")
        payload = self._build_payload(request)
        arguments = decision.tool_call.arguments.model_dump()
        payload["messages"].extend(
            [
                {
                    "role": "assistant",
                    "content": decision.assistant_text,
                    "tool_calls": [
                        {
                            "id": decision.tool_call.call_id,
                            "type": "function",
                            "function": {
                                "name": decision.tool_call.name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": decision.tool_call.call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                },
            ]
        )
        payload["tool_choice"] = "none"
        response = await self._post(payload, api_key)
        parsed = self._parse_response(response.json(), request.circuit.topology_revision)
        if not parsed.assistant_text:
            raise LlmProtocolError("LLM did not return final text after tool execution")
        return parsed.assistant_text

    async def _post(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        headers = {"Authorization": f"Bearer {api_key}"}
        url = f"{self._config.base_url}/chat/completions"
        client = await self._get_client()
        response = await client.post(
            url, headers=headers, json=payload, timeout=self._config.timeout_seconds
        )
        response.raise_for_status()
        return response


    def _build_payload(
        self,
        request: DecisionRequest,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        circuit_context = build_circuit_context(request.circuit)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            }
        ]
        missing_components_instruction = build_missing_components_instruction(request.circuit)
        if missing_components_instruction:
            messages.append(
                {"role": "system", "content": missing_components_instruction}
            )
        binary_adder_instruction = build_binary_adder_instruction(request.circuit)
        if binary_adder_instruction:
            messages.append({"role": "system", "content": binary_adder_instruction})
        if history:
            for item in history[-6:]:
                role = item.get("role")
                content = item.get("content")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append(
            {
                "role": "user",
                "content": f"当前电路信息：\n{circuit_context}\n\n用户问题：{request.question}",
            }
        )
        return {
            "model": self._config.model,
            "stream": False,
            "messages": messages,
            "tools": available_tools(),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }

    def _parse_response(self, payload: dict[str, Any], topology_revision: int) -> DecisionResponse:
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
            if function.get("name") != "highlight_ports":
                raise LlmProtocolError("unsupported tool call")
            try:
                raw_arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise LlmProtocolError("tool arguments are not valid JSON") from exc
            arguments = HighlightPortsArgs.model_validate(raw_arguments)
            return DecisionResponse(
                assistant_text=message.get("content"),
                tool_call=ToolCall(
                    call_id=call["id"],
                    name="highlight_ports",
                    arguments=arguments,
                ),
                topology_revision=topology_revision,
            )

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LlmProtocolError("LLM response contains neither text nor a tool call")
        return DecisionResponse(
            assistant_text=content.strip(),
            topology_revision=topology_revision,
        )
