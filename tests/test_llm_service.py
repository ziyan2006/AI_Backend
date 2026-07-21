import json

import httpx
import pytest

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.models import (
    CircuitSnapshot,
    DecisionRequest,
    DecisionResponse,
    ToolCall,
)
from tuco_ai_backend.providers.openai_compatible import OpenAICompatibleClient
from tuco_ai_backend.tools import HighlightPortsArgs


def sample_request() -> DecisionRequest:
    return DecisionRequest(
        question="为什么灯不亮？",
        circuit=CircuitSnapshot(
            schema_version=1,
            topology_revision=42,
            slots=[],
            valid_links=[{"from": 2, "to": 8}],
            invalid_links=[],
            scan={"count": 10, "stable_count": 4},
        ),
    )


@pytest.mark.asyncio
async def test_decide_parses_highlight_ports_tool_call() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_ports",
                                        "arguments": json.dumps(
                                            {
                                                "ports": [8, 21],
                                                "duration_ms": 3000,
                                                "pattern": "pulse",
                                                "reason": "与门缺少第二个输入连接",
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(
        Settings(
            llm_base_url="https://relay.example/v1",
            llm_model="test-model",
            llm_api_key="secret",
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenAICompatibleClient(store, http_client=http_client)
        decision = await client.decide(sample_request())

    assert captured["parallel_tool_calls"] is False
    assert captured["tool_choice"] == "auto"
    assert captured["tools"][0]["function"]["strict"] is True
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "call_abc"
    assert decision.tool_call.arguments.ports == [8, 21]
    assert decision.topology_revision == 42


@pytest.mark.asyncio
async def test_decide_returns_text_when_no_tool_is_needed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "先检查电源是否接通。"}}]},
        )

    store = RuntimeConfigStore(
        Settings(
            llm_base_url="https://relay.example/v1",
            llm_model="test-model",
            llm_api_key="secret",
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await OpenAICompatibleClient(store, http_client=http_client).decide(
            sample_request()
        )

    assert decision.assistant_text == "先检查电源是否接通。"
    assert decision.tool_call is None


@pytest.mark.asyncio
async def test_complete_after_tool_sends_tool_result_and_disables_more_tools() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "请检查闪烁的两个端口。"}}]},
        )

    store = RuntimeConfigStore(
        Settings(
            llm_base_url="https://relay.example/v1",
            llm_model="test-model",
            llm_api_key="secret",
        )
    )
    decision = DecisionResponse(
        topology_revision=42,
        tool_call=ToolCall(
            call_id="call_abc",
            name="highlight_ports",
            arguments=HighlightPortsArgs(
                ports=[8, 21],
                duration_ms=3000,
                pattern="pulse",
                reason="检查接线",
            ),
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        text = await OpenAICompatibleClient(
            store, http_client=http_client
        ).complete_after_tool(
            sample_request(), decision, {"ok": True, "message": "done"}
        )

    assert text == "请检查闪烁的两个端口。"
    assert captured["tool_choice"] == "none"
    assistant_message = captured["messages"][-2]
    tool_message = captured["messages"][-1]
    assert assistant_message["tool_calls"][0]["id"] == "call_abc"
    assert tool_message == {
        "role": "tool",
        "tool_call_id": "call_abc",
        "content": json.dumps({"ok": True, "message": "done"}, ensure_ascii=False),
    }
