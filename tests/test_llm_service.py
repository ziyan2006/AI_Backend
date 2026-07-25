import json

import httpx
import pytest

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.models import (
    CircuitSnapshot,
    DecisionRequest,
    DecisionResponse,
    LevelContext,
    ToolCall,
)
from tuco_ai_backend.providers.openai_compatible import (
    OpenAICompatibleClient,
    build_circuit_context,
)
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


def test_circuit_context_keeps_level_goal_and_chinese_module_names() -> None:
    circuit = CircuitSnapshot(
        schema_version=1,
        topology_revision=7,
        level=LevelContext(
            level_id=101,
            short_goal="让输出和输入保持一样",
            input_names="A",
            output_names="Y",
            input_count=1,
            output_count=1,
        ),
        slots=[
            {"slot": 0, "present": True, "gate": 0},
            {"slot": 1, "present": True, "gate": 1},
        ],
        valid_links=[{"from_slot": 0, "to_slot": 1}],
        invalid_links=[{"from": 2, "to": 8, "error": "direction"}],
    )

    context = build_circuit_context(circuit)

    assert "第101关" in context
    assert "让输出和输入保持一样" in context
    assert "输入积木1块" in context
    assert "输出积木1块" in context
    assert "输入积木的输出端连接到输出积木的输入端" in context
    assert "另有1条连接" in context
    assert "输入积木=0,1,2,3" in context
    assert "输出积木=4,5,6,7" in context


def test_circuit_context_reads_firmware_style_links_and_ignores_invalid_ids() -> None:
    circuit = CircuitSnapshot(
        schema_version=3,
        topology_revision=7,
        level=LevelContext(
            level_id=101,
            short_goal="让输出和输入保持一样",
            input_names="A",
            output_names="Y",
            input_count=2,
            output_count=1,
        ),
        slots=[
            {"slot": 0, "present": True, "id_valid": True, "raw_id": 0xF0, "gate": 0},
            {"slot": 1, "present": True, "id_valid": True, "raw_id": 0xF1, "gate": 1},
            {"slot": 2, "present": True, "id_valid": False, "raw_id": 42, "gate": 0},
        ],
        links=[
            {
                "first_port": 0,
                "second_port": 4,
                "color_index": 0,
                "valid": True,
                "error": 0,
            }
        ],
        invalid_link_count=0,
    )

    context = build_circuit_context(circuit)

    assert "输入积木1块" in context
    assert "输出积木1块" in context
    assert "输入积木的输出端连接到输出积木的输入端" in context
    assert "还差1块输入积木" in context
    assert "未知积木" not in context


@pytest.mark.asyncio
async def test_decide_calls_llm_with_dynamic_missing_components_instruction() -> None:
    captured: dict[str, object] = {}
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "我是图灵号的电路小伙伴。"}}]},
        )

    circuit = CircuitSnapshot(
        topology_revision=9,
        level=LevelContext(
            level_id=101,
            short_goal="总电门只需要把输入 A 直连到输出 Y 即可恢复主控台供电",
            input_names="总电门 A",
            output_names="主控台供电 Y",
            input_count=1,
            output_count=1,
        ),
        slots=[],
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
            DecisionRequest(question="你是谁？", circuit=circuit)
        )

    system_messages = [
        message["content"]
        for message in captured["messages"]
        if message["role"] == "system"
    ]
    assert request_count == 1
    assert decision.assistant_text == "我是图灵号的电路小伙伴。"
    assert any(
        "当前电路尚未就绪" in message
        and "1块输入积木" in message
        and "1块输出积木" in message
        for message in system_messages
    )
    assert any("可放置积木只有" in message for message in system_messages)
    assert any("不得把输入或输出信号标签" in message for message in system_messages)
    user_message = next(
        message["content"]
        for message in captured["messages"]
        if message["role"] == "user"
    )
    assert "输入信号标签（不是积木）：总电门 A" in user_message
    assert "输出信号标签（不是积木）：主控台供电 Y" in user_message


def test_ready_circuit_does_not_add_missing_components_instruction() -> None:
    circuit = CircuitSnapshot(
        topology_revision=9,
        level=LevelContext(level_id=101, short_goal="直连", input_count=1, output_count=1),
        slots=[
            {"slot": 0, "present": True, "gate": 0},
            {"slot": 1, "present": True, "gate": 1},
        ],
    )
    payload = OpenAICompatibleClient(
        RuntimeConfigStore(Settings(llm_api_key="secret"))
    )._build_payload(DecisionRequest(question="你是谁？", circuit=circuit))

    assert all("当前电路尚未就绪" not in message["content"] for message in payload["messages"])


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
async def test_decide_adds_two_block_highlight_for_a_connection_answer() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "把输入积木接到输出积木。"}}]},
        )

    request = DecisionRequest(
        question="怎么接？",
        circuit=CircuitSnapshot(
            topology_revision=9,
            slots=[
                {"slot": 0, "present": True, "gate": 0},
                {"slot": 1, "present": True, "gate": 1},
            ],
            valid_links=[{"from_slot": 0, "to_slot": 1}],
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await OpenAICompatibleClient(
            RuntimeConfigStore(Settings(llm_api_key="secret")), http_client=http_client
        ).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.arguments.ports == list(range(8))
    assert decision.tool_call.arguments.duration_ms == 20000


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
