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
    LlmProtocolError,
    OpenAICompatibleClient,
    _level_question_intent,
    available_gate_components_for_level,
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


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("我有点不会了，给我一点提示", "提示求助"),
        ("我卡住了，帮帮我", "提示求助"),
        ("我这样接对了吗", "检查诊断"),
        ("你帮我看看哪里有问题", "检查诊断"),
        ("为什么灯不亮", "检查诊断"),
    ],
)
def test_level_question_intent_recognizes_hint_and_diagnosis(
    question: str,
    expected: str,
) -> None:
    assert _level_question_intent(question) == expected


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
async def test_decide_rejects_non_json_provider_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body>Internal Server Error</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
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
        with pytest.raises(LlmProtocolError, match="non-JSON response"):
            await client.decide(sample_request())


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
    assert any("可放置积木只有" in message for message in system_messages)
    user_message = next(
        message["content"]
        for message in captured["messages"]
        if message["role"] == "user"
    )
    assert "当前电路尚未就绪" in user_message
    assert "1块输入积木" in user_message
    assert "1块输出积木" in user_message
    assert "不得把输入或输出信号标签" in user_message
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
    ("level_id", "expected_plain_goal", "expected_term"),
    [
        (102, "两个开关都打开时，输出反而关闭", "与非门"),
        (103, "开关和输出总是相反", "非门"),
        (201, "两个条件都满足", "与门"),
        (202, "任意一个条件满足", "或门"),
        (203, "两个探测器都没发现危险", "或非门"),
        (301, "两个开关一亮一灭", "异或门"),
        (302, "两个开关一样", "同或门"),
        (401, "只会是 0 或 1", "二进制"),
        (402, "送到下一位", "进位"),
        (403, "个位结果和进位一起算", "半加器"),
        (501, "三个 0 或 1 一起相加", "三路求和"),
        (502, "有两个或三个开关亮起", "进位"),
        (503, "三条可能多出来的1", "进位"),
        (504, "三个 0 或 1 一起相加", "全加器"),
        (601, "从两条路中选一条", "信号选择器"),
        (602, "每次只选亮四个舱室中的一个", "二转四译码器"),
    ],
)
def test_guided_levels_use_short_child_friendly_teaching_instruction(
    level_id: int, expected_plain_goal: str, expected_term: str
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
        if message["role"] == "user" and "教学规则" in message["content"]
    )

    assert all(
        "教学规则" not in message["content"]
        for message in payload["messages"]
        if message["role"] == "system"
    )
    assert "日常加法" in teaching_instruction
    assert expected_plain_goal in teaching_instruction
    assert expected_term in teaching_instruction
    assert "每轮最多引入一个新术语" in teaching_instruction
    assert "先用白话解释，再告诉孩子术语" in teaching_instruction
    assert "不得直接复制关卡目标" in teaching_instruction
    assert "不得直接复制输入输出标签" in teaching_instruction
    assert "普通聊天" in teaching_instruction
    assert "概念首提时不要先要求摆放积木" in teaching_instruction
    assert "每次只推进一个小台阶" in teaching_instruction
    assert "总共只写两句话" in teaching_instruction
    assert "输出恰好两行" in teaching_instruction
    assert "第二行结束后立刻停止" in teaching_instruction
    assert "不要同时给问题和动作" in teaching_instruction
    assert "不要逐条复述教学提示" in teaching_instruction
    assert "Sum" not in teaching_instruction
    assert "Carry" not in teaching_instruction


@pytest.mark.parametrize(
    ("level_id", "expected_opening_instruction"),
    [
        (401, "先用“只用0和1来数数”解释二进制"),
        (403, "先说清两个0或1相加会得到个位结果和进位"),
        (501, "本关只是完整全加器前的三路求和练习"),
        (504, "先说清三个0或1相加会得到个位结果和进位"),
    ],
)
def test_binary_addition_levels_require_concept_before_term(
    level_id: int, expected_opening_instruction: str
) -> None:
    circuit = CircuitSnapshot(
        topology_revision=0,
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

    teaching_instruction = payload["messages"][-1]["content"]

    assert "二进制加法启蒙规则" in teaching_instruction
    assert expected_opening_instruction in teaching_instruction


@pytest.mark.parametrize(
    ("level_id", "expected_opening_instruction"),
    [
        (301, "先说清两个开关一亮一灭时钥匙才会亮"),
        (403, "先说清两个0或1相加会得到个位结果和进位"),
    ],
)
def test_key_levels_require_plain_goal_before_gate_or_circuit_term(
    level_id: int, expected_opening_instruction: str
) -> None:
    circuit = CircuitSnapshot(
        topology_revision=0,
        level=LevelContext(
            level_id=level_id,
            short_goal="技术化的关卡描述",
            input_count=2,
            output_count=2 if level_id == 403 else 1,
        ),
    )
    payload = OpenAICompatibleClient(
        RuntimeConfigStore(Settings(llm_api_key="secret"))
    )._build_payload(DecisionRequest(question="这关要做什么？", circuit=circuit))

    teaching_instruction = payload["messages"][-1]["content"]
    assert expected_opening_instruction in teaching_instruction
    assert "不要先抛出术语名称" in teaching_instruction


def test_xor_level_uses_task_intent_and_only_previously_unlocked_gates() -> None:
    circuit = CircuitSnapshot(
        topology_revision=0,
        level=LevelContext(
            level_id=301,
            short_goal="两个输入不同时输出亮",
            input_count=2,
            output_count=1,
        ),
    )
    payload = OpenAICompatibleClient(
        RuntimeConfigStore(Settings(llm_api_key="secret"))
    )._build_payload(DecisionRequest(question="接下来怎么做呢？", circuit=circuit))

    user_message = payload["messages"][-1]["content"]

    assert "当前提问意图：开始行动" in user_message
    assert "这关要搭建一个" in user_message
    assert "本关开始时可使用的逻辑积木只有" in user_message
    assert "与非门积木" in user_message
    assert "非门积木" in user_message
    assert "与门积木" in user_message
    assert "或门积木" in user_message
    assert "或非门积木" in user_message
    assert "异或门积木尚未解锁" in user_message
    assert "不得建议孩子直接放置、连接或使用异或门积木" in user_message
    assert "同或门积木" not in user_message


@pytest.mark.parametrize(
    ("level_id", "expected_action_instruction"),
    [
        (201, "第一步只邀请摆放一块与非门积木"),
        (202, "不要说“已解锁”"),
        (203, "两个探测器都没发现危险，护罩才打开"),
        (301, "这是拼出钥匙电路的第一步"),
        (501, "三个只会是0或1的小开关"),
        (502, "有两个或三个开关亮起时，多出来的1"),
        (503, "不要直接使用“进位汇聚”或“进位信号”"),
        (504, "三个只会是0或1的小开关相加"),
        (602, "第一行必须说“两个只会是0或1的开关，每次只选亮四个舱室中的一个”"),
    ],
)
def test_action_guidance_uses_one_child_sized_step(
    level_id: int, expected_action_instruction: str
) -> None:
    circuit = CircuitSnapshot(
        topology_revision=0,
        level=LevelContext(
            level_id=level_id,
            short_goal="技术化的关卡描述",
            input_count=3 if level_id in (501, 502, 503, 504) else 2,
            output_count=2 if level_id == 504 else 1,
        ),
    )
    payload = OpenAICompatibleClient(
        RuntimeConfigStore(Settings(llm_api_key="secret"))
    )._build_payload(DecisionRequest(question="接下来应该怎么做？给我点提示", circuit=circuit))

    teaching_instruction = payload["messages"][-1]["content"]

    assert "严格只摆放一块积木" in teaching_instruction
    assert expected_action_instruction in teaching_instruction


@pytest.mark.parametrize(
    ("level_id", "expected_components"),
    [
        (101, ()),
        (102, ("nand_gate",)),
        (103, ("nand_gate",)),
        (201, ("nand_gate", "not_gate")),
        (202, ("nand_gate", "not_gate", "and_gate")),
        (203, ("nand_gate", "not_gate", "and_gate", "or_gate")),
        (301, ("nand_gate", "not_gate", "and_gate", "or_gate", "nor_gate")),
        (
            302,
            (
                "nand_gate",
                "not_gate",
                "and_gate",
                "or_gate",
                "nor_gate",
                "xor_gate",
            ),
        ),
        (
            401,
            (
                "nand_gate",
                "not_gate",
                "and_gate",
                "or_gate",
                "nor_gate",
                "xor_gate",
                "xnor_gate",
            ),
        ),
        (
            602,
            (
                "nand_gate",
                "not_gate",
                "and_gate",
                "or_gate",
                "nor_gate",
                "xor_gate",
                "xnor_gate",
            ),
        ),
    ],
)
def test_available_gate_components_match_firmware_progression(
    level_id: int, expected_components: tuple[str, ...]
) -> None:
    assert available_gate_components_for_level(level_id) == expected_components


@pytest.mark.parametrize(
    ("level_id", "target_name"),
    [
        (103, "非门积木"),
        (201, "与门积木"),
        (202, "或门积木"),
        (203, "或非门积木"),
        (301, "异或门积木"),
        (302, "同或门积木"),
    ],
)
def test_level_guidance_forbids_target_gate_before_its_reward_unlocks(
    level_id: int, target_name: str
) -> None:
    circuit = CircuitSnapshot(
        topology_revision=0,
        level=LevelContext(
            level_id=level_id,
            short_goal="测试目标",
            input_count=2,
            output_count=1,
        ),
    )
    payload = OpenAICompatibleClient(
        RuntimeConfigStore(Settings(llm_api_key="secret"))
    )._build_payload(DecisionRequest(question="接下来怎么做？", circuit=circuit))

    user_message = payload["messages"][-1]["content"]

    assert f"{target_name}尚未解锁" in user_message
    assert f"不得建议孩子直接放置、连接或使用{target_name}" in user_message


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
