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
        port_roles=[2, 2, 2, 2, 1] + [0] * 59,
        invalid_link_count=0,
    )

    context = build_circuit_context(circuit)

    assert "输入积木1块" in context
    assert "输出积木1块" in context
    assert "输入积木的输出端连接到输出积木的输入端" in context
    assert "还差1块输入积木" in context
    assert "未知积木" not in context
    assert "输入积木: 输出端=[0, 1, 2, 3]" in context
    assert "输出积木: 输入端=[4]" in context


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


@pytest.mark.parametrize(
    ("level_id", "expected_example", "expected_focus"),
    [
        (401, "1+1=10", "个位"),
        (402, "1+1=10", "进位"),
        (403, "1+1=10", "小计算器"),
        (501, "1+1+1=11", "三个 0 或 1"),
        (502, "1+1+0=10", "进位"),
        (503, "1+1=10", "进位"),
        (504, "1+1+1=11", "小计算器"),
    ],
)
def test_adder_levels_add_decimal_binary_teaching_instruction(
    level_id: int, expected_example: str, expected_focus: str
) -> None:
    circuit = CircuitSnapshot(
        topology_revision=9,
        level=LevelContext(
            level_id=level_id,
            short_goal="技术化的关卡描述",
            input_count=3 if level_id >= 500 else 2,
            output_count=2 if level_id in (403, 504) else 1,
        ),
    )
    payload = OpenAICompatibleClient(
        RuntimeConfigStore(Settings(llm_api_key="secret"))
    )._build_payload(DecisionRequest(question="这关要做什么？", circuit=circuit))

    teaching_instruction = next(
        message["content"]
        for message in payload["messages"]
        if message["role"] == "system" and "二进制加法教学规则" in message["content"]
    )

    assert "十进制" in teaching_instruction
    assert expected_example in teaching_instruction
    assert expected_focus in teaching_instruction
    assert "不要使用“奇偶”" in teaching_instruction
    assert "先告诉孩子本关要做什么，再用十进制类比解释" in teaching_instruction
    assert "不要一开始用十进制算式开头" in teaching_instruction


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
                {"slot": 0, "present": True, "component": "input", "display_name": "输入积木"},
                {"slot": 1, "present": True, "component": "output", "display_name": "输出积木"},
            ],
            port_roles=[2, 0, 0, 0, 1] + [0] * 59,
            links=[
                {
                    "first_port": 0,
                    "second_port": 4,
                    "valid": True,
                    "error": 0,
                }
            ],
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await OpenAICompatibleClient(
            RuntimeConfigStore(Settings(llm_api_key="secret")), http_client=http_client
        ).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.arguments.ports == [0, 4]
    assert decision.tool_call.arguments.duration_ms == 20000


@pytest.mark.asyncio
async def test_decide_normalizes_highlight_to_one_pair_and_supplies_guidance_text() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_pair",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_ports",
                                        "arguments": json.dumps(
                                            {
                                                "ports": list(range(8)),
                                                "duration_ms": 20000,
                                                "pattern": "pulse",
                                                "reason": "请标出接线位置",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    request = DecisionRequest(
        question="接下来应该怎么接？",
        circuit=CircuitSnapshot(
            topology_revision=9,
            slots=[
                {"slot": 0, "present": True, "component": "input", "display_name": "输入积木"},
                {"slot": 1, "present": True, "component": "output", "display_name": "输出积木"},
            ],
            port_roles=[2, 0, 0, 0, 1] + [0] * 59,
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await OpenAICompatibleClient(
            RuntimeConfigStore(Settings(llm_api_key="secret")), http_client=http_client
        ).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.arguments.ports == [0, 4]
    assert decision.assistant_text == "请把亮起的输入积木输出端和输出积木输入端连起来。"


@pytest.mark.asyncio
async def test_decide_rejects_highlight_that_selects_two_output_ports() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "请把输入积木接到异或门的输入端。",
                            "tool_calls": [
                                {
                                    "id": "call_wrong_direction",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_ports",
                                        "arguments": json.dumps(
                                            {
                                                "ports": [0, 6],
                                                "duration_ms": 20000,
                                                "pattern": "pulse",
                                                "reason": "标出下一条连接",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    request = DecisionRequest(
        question="接下来应该怎么接？",
        circuit=CircuitSnapshot(
            topology_revision=9,
            slots=[
                {"slot": 0, "present": True, "component": "input", "display_name": "输入积木"},
                {"slot": 1, "present": True, "component": "xor_gate", "display_name": "异或门积木"},
            ],
            port_roles=[2, 2, 2, 2, 1, 1, 2, 0] + [0] * 56,
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await OpenAICompatibleClient(
            RuntimeConfigStore(Settings(llm_api_key="secret")), http_client=http_client
        ).decide(request)

    assert decision.tool_call is None
    assert decision.assistant_text == "请把输入积木接到异或门的输入端。"


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
