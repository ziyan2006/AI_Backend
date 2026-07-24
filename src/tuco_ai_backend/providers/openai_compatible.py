from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx

from tuco_ai_backend.config import RuntimeConfigStore
from tuco_ai_backend.models import CircuitSnapshot, DecisionRequest, DecisionResponse, ToolCall
from tuco_ai_backend.tools import HighlightPortsArgs, available_tools


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

SYSTEM_PROMPT = (
    "你是图灵号飞船上的电路指导员，正在陪伴儿童修复飞船。"
    "你熟悉输入积木、输出积木、逻辑门和端口连线。"
    "只根据提供的当前关卡和实际检测电路指导，不要编造不存在的积木、连接或结果。"
    "每次只回答玩家刚才的问题，不要开场寒暄、复述问题、罗列完整电路或重复已知信息。"
    "回复控制在45到60个汉字，使用一到两句自然中文；可用“好呀”“我们先试试”等亲切语气。"
    "不要直接说完整答案，只给一个明确的方向性小提示。"
    "描述连线时，只说哪个积木的输出端连接到哪个积木的输入端；"
    "不要提及API、网络、扫描、槽位号、端口编号或上下左右等开发与硬件术语。"
    "语气要活泼、耐心，像一起冒险的队友。"
    "玩家询问亮灯、位置、怎么接、从哪里接到哪里，或你的回答提到从一个积木接到另一个积木时，"
    "必须调用highlight_ports。调用时必须同时提供一段可直接朗读的提示；系统会先完整朗读，再亮灯。"
    "工具端口映射仅供调用工具使用，绝不能在朗读内容中说出端口编号。"
    "凡是指出两个积木之间的接线，必须把两块积木的四个端口全部加入ports，共八个端口。"
    "duration_ms默认20000；仅当玩家明确说明亮灯时长时才按其要求调整，范围500到60000。"
)


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


def _slot_labels(slots: list[dict[str, Any]]) -> dict[int, str]:
    counts: dict[str, int] = {}
    records: list[tuple[int, str, str]] = []
    for slot in slots:
        if slot.get("present") is False or not isinstance(slot.get("slot"), int):
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


def _highlight_slots_from_text(circuit: CircuitSnapshot, text: str) -> list[int]:
    labels = _slot_labels(circuit.slots)
    mentioned = [slot for slot, label in labels.items() if label in text]
    if len(mentioned) >= 2:
        return mentioned[:2]
    if any(word in text for word in ("亮灯", "怎么接", "接到", "连接")):
        for link in circuit.valid_links:
            source = link.get("from_slot")
            target = link.get("to_slot")
            if isinstance(source, int) and isinstance(target, int):
                return [source, target]
    return []


def _needs_highlight(question: str, answer: str) -> bool:
    text = question + answer
    return any(word in text for word in ("亮灯", "怎么接", "接到", "连接", "哪里接", "位置"))


def _fallback_highlight(request: DecisionRequest, answer: str) -> ToolCall | None:
    if not _needs_highlight(request.question, answer):
        return None
    slots = _highlight_slots_from_text(request.circuit, answer)
    if len(slots) < 2:
        return None
    return ToolCall(
        call_id=f"fallback-{uuid4().hex}",
        name="highlight_ports",
        arguments=HighlightPortsArgs(
            ports=[port for slot in slots for port in _slot_ports(slot)],
            duration_ms=20000,
            pattern="pulse",
            reason="标记语音提示中提到的两块积木",
        ),
    )


def build_circuit_context(circuit: CircuitSnapshot) -> str:
    parts: list[str] = []
    if circuit.level is not None:
        level = circuit.level
        parts.append(f"当前关卡：第{level.level_id}关。目标：{level.short_goal}。")
        if level.input_names:
            parts.append(f"输入名称：{level.input_names}。")
        if level.output_names:
            parts.append(f"输出名称：{level.output_names}。")
    else:
        parts.append("当前关卡信息未上传，只能依据电路状态给提示。")

    labels = _slot_labels(circuit.slots)
    component_counts: dict[str, int] = {}
    for slot in circuit.slots:
        if slot.get("present") is not False:
            component, _ = _slot_component(slot)
            component_counts[component] = component_counts.get(component, 0) + 1
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
    for link in circuit.valid_links:
        from_slot = link.get("from_slot")
        to_slot = link.get("to_slot")
        if isinstance(from_slot, int) and isinstance(to_slot, int):
            source = labels.get(from_slot)
            target = labels.get(to_slot)
            if source and target:
                connections.append(f"{source}的输出端连接到{target}的输入端")
    if connections:
        parts.append("当前有效连接：" + "；".join(connections) + "。")
    else:
        parts.append("暂未确认可描述的有效连接。")
    if circuit.invalid_links:
        parts.append(f"另有{len(circuit.invalid_links)}条连接不完整或方向不合适，不能当作有效电路。")
    if labels:
        ports = "；".join(
            f"{label}={','.join(str(port) for port in _slot_ports(slot))}"
            for slot, label in labels.items()
        )
        parts.append(f"工具端口映射（不可朗读）：{ports}。")
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


def _missing_component_reply(circuit: CircuitSnapshot) -> str | None:
    if circuit.level is None:
        return None
    counts: dict[str, int] = {}
    for slot in circuit.slots:
        if slot.get("present") is not False:
            component, _ = _slot_component(slot)
            counts[component] = counts.get(component, 0) + 1
    missing: list[str] = []
    if counts.get("input", 0) < circuit.level.input_count:
        missing.append(f"{circuit.level.input_count - counts.get('input', 0)}块输入积木")
    if counts.get("output", 0) < circuit.level.output_count:
        missing.append(f"{circuit.level.output_count - counts.get('output', 0)}块输出积木")
    return f"好呀，我们先放上{ '和'.join(missing) }，再一起接线吧！" if missing else None


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

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        api_key = self._config.api_key()
        if not api_key:
            raise LlmConfigurationError("LLM API key is not configured")

        missing = _missing_component_reply(request.circuit)
        if missing is not None:
            return DecisionResponse(
                assistant_text=missing,
                topology_revision=request.circuit.topology_revision,
            )
        payload = self._build_payload(request)
        response = await self._post(payload, api_key)
        decision = self._parse_response(response.json(), request.circuit.topology_revision)
        if decision.tool_call is None and decision.assistant_text:
            decision.tool_call = _fallback_highlight(request, decision.assistant_text)
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


    def _build_payload(self, request: DecisionRequest) -> dict[str, Any]:
        circuit_context = build_circuit_context(request.circuit)
        return {
            "model": self._config.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": f"当前电路信息：\n{circuit_context}\n\n用户问题：{request.question}",
                },
            ],
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
