from __future__ import annotations

import json
from typing import Any

import httpx

from tuco_ai_backend.config import RuntimeConfigStore
from tuco_ai_backend.models import DecisionRequest, DecisionResponse, ToolCall
from tuco_ai_backend.tools import HighlightPortsArgs, available_tools


class LlmConfigurationError(RuntimeError):
    pass


class LlmProtocolError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(
        self,
        config: RuntimeConfigStore,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http_client = http_client

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        api_key = self._config.api_key()
        if not api_key:
            raise LlmConfigurationError("LLM API key is not configured")

        payload = self._build_payload(request)
        response = await self._post(payload, api_key)
        return self._parse_response(response.json(), request.circuit.topology_revision)

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
        if self._http_client is not None:
            response = await self._http_client.post(
                url, headers=headers, json=payload, timeout=self._config.timeout_seconds
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, json=payload, timeout=self._config.timeout_seconds
                )
        response.raise_for_status()
        return response

    def _build_payload(self, request: DecisionRequest) -> dict[str, Any]:
        circuit_json = request.circuit.model_dump_json(exclude_none=True)
        return {
            "model": self._config.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是面向儿童的数字电路学习助手。根据结构化电路快照回答，"
                        "不要臆造不存在的端口。需要指出接线位置时调用 highlight_ports，"
                        "否则直接给出简短、可操作的中文建议。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"电路快照：\n{circuit_json}\n\n用户问题：{request.question}",
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
