from __future__ import annotations

import json

import httpx
import pytest

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES, build_circuit_coach_v2
from tuco_ai_backend.models import CircuitCoachDecisionRequest, CircuitCoachV2Port
from tuco_ai_backend.providers.circuit_coach_v2 import (
    CIRCUIT_COACH_V2_SYSTEM_PROMPT,
    LEARNING_ACTIVITY_SYSTEM_PROMPT,
    CircuitCoachV2Client,
)


def test_circuit_coach_request_decodes_firmware_v2_compact_snapshot() -> None:
    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "device-level-101",
            "user_text": "next step",
            "circuit_snapshot": {
                "schema": "tuco_circuit_v2",
                "level": {
                    "id": 101,
                    "goal": "direct",
                    "inputs": "input A",
                    "outputs": "output Y",
                    "input_count": 1,
                    "output_count": 1,
                },
                "unlocked_gates": ["INPUT", "OUTPUT"],
                "gate_templates": [["INPUT", ["up", "output"]]],
                "board": {
                    "topology_revision": 2,
                    "link_count": 0,
                    "invalid_link_count": 0,
                    "ignored_link_count": 0,
                    "link_overflow": False,
                    "slots": [
                        [0, 0, 0, "present", "INPUT", [[0, "left", "output"]]],
                        [1, 1, 0, "empty", None, [[4, "right", "unused"]]],
                    ],
                    "edges": [],
                },
            },
        }
    )

    assert request.user_text == "next step"
    assert request.circuit_snapshot.model_dump(by_alias=True)["schema"] == "tuco_circuit_v2"
    assert request.circuit_snapshot.level.id == 101
    assert request.circuit_snapshot.gate_templates == [["INPUT", ["up", "output"]]]
    assert request.circuit_snapshot.board.topology_revision == 2
    assert request.circuit_snapshot.board.slots[0].gate == "INPUT"
    assert request.circuit_snapshot.board.slots[0].ports[0].port_id == 0
    assert request.circuit_snapshot.board.slots[1].state == "empty"


def test_circuit_coach_request_accepts_learning_activity_context() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 401)
    request = CircuitCoachDecisionRequest(
        session_id="fw-401-1",
        user_text="为什么这里是 1？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
        learning_activity={
            "kind": "binary_slots",
            "stage": "practice",
            "round_index": 1,
            "round_total": 3,
            "slot_roles": ["8", "4", "2", "1"],
            "slot_weights": [8, 4, 2, 1],
            "slot_bits": [0, 1, 0, 1],
            "target_bits": [0, 1, 1, 0],
            "target_decimal": 6,
            "current_decimal": 5,
            "solved": False,
            "complete": False,
        },
    )

    assert request.learning_activity is not None
    assert request.learning_activity.kind == "binary_slots"
    assert request.learning_activity.slot_bits == [0, 1, 0, 1]


def test_circuit_coach_request_accepts_three_input_parity_activity_context() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 501)
    request = CircuitCoachDecisionRequest(
        session_id="fw-501-1",
        user_text="为什么这里的个位是 0？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
        learning_activity={
            "kind": "three_input_parity",
            "stage": "practice",
            "round_index": 2,
            "round_total": 3,
            "slot_roles": ["A", "B", "进位输入"],
            "slot_bits": [1, 1, 0],
            "target_bits": [1, 0, 0],
            "target_decimal": 1,
            "current_decimal": 2,
            "solved": False,
            "complete": False,
        },
    )

    assert request.learning_activity is not None
    assert request.learning_activity.kind == "three_input_parity"
    assert request.learning_activity.current_decimal == 2


def test_circuit_coach_request_accepts_three_input_carry_activity_context() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    request = CircuitCoachDecisionRequest(
        session_id="fw-502-1",
        user_text="为什么现在还没有进位？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
        learning_activity={
            "kind": "three_input_carry",
            "stage": "practice",
            "round_index": 1,
            "round_total": 3,
            "slot_roles": ["A", "B", "进位输入"],
            "slot_bits": [1, 0, 1],
            "target_bits": [1, 0, 0],
            "target_decimal": 0,
            "current_decimal": 2,
            "solved": False,
            "complete": False,
        },
    )

    assert request.learning_activity is not None
    assert request.learning_activity.kind == "three_input_carry"
    assert request.learning_activity.current_decimal == 2


def test_502_action_guidance_prioritizes_existing_unwired_logic_gate() -> None:
    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-progress",
            "user_text": "接下来该怎么做？",
            "circuit_snapshot": {
                "schema": "tuco_circuit_v2",
                "level": {
                    "id": 502,
                    "goal": "局部进位",
                    "inputs": "加数 A, B, C",
                    "outputs": "局部进位",
                    "input_count": 3,
                    "output_count": 1,
                },
                "unlocked_gates": ["INPUT", "OUTPUT", "AND", "OR", "XOR"],
                "board": {
                    "topology_revision": 24,
                    "link_count": 5,
                    "slots": [
                        [0, 0, 0, "present", "OUTPUT", [[2, "left", "input"]]],
                        [
                            3,
                            1,
                            2,
                            "present",
                            "AND",
                            [
                                [13, "up", "input"],
                                [14, "right", "output"],
                                [15, "down", "input"],
                            ],
                        ],
                        [
                            4,
                            0,
                            1,
                            "present",
                            "XOR",
                            [
                                [16, "right", "output"],
                                [17, "down", "input"],
                                [19, "up", "input"],
                            ],
                        ],
                        [
                            6,
                            0,
                            0,
                            "present",
                            "INPUT",
                            [
                                [24, "right", "output"],
                                [25, "down", "output"],
                                [26, "left", "output"],
                                [27, "up", "output"],
                            ],
                        ],
                        [
                            7,
                            1,
                            0,
                            "present",
                            "INPUT",
                            [
                                [28, "left", "output"],
                                [29, "up", "output"],
                                [30, "right", "output"],
                                [31, "down", "output"],
                            ],
                        ],
                        [
                            8,
                            0,
                            2,
                            "present",
                            "INPUT",
                            [
                                [32, "right", "output"],
                                [33, "down", "output"],
                                [34, "left", "output"],
                                [35, "up", "output"],
                            ],
                        ],
                        [
                            12,
                            0,
                            2,
                            "present",
                            "AND",
                            [
                                [48, "right", "output"],
                                [49, "down", "input"],
                                [51, "up", "input"],
                            ],
                        ],
                    ],
                    "edges": [
                        [2, 14, "valid"],
                        [13, 16, "valid"],
                        [15, 32, "valid"],
                        [17, 30, "valid"],
                        [19, 27, "valid"],
                    ],
                },
            },
        }
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)

    instruction = payload["messages"][-1]["content"]
    assert "AND@12 已摆放但还没有接入输入" in instruction
    assert "INPUT@6 的未连接输出端接到 AND@12 的空输入端" in instruction
    assert "先接好已有积木，不要建议新增同类逻辑门" in instruction
    assert "当前已放" not in instruction
    assert "只邀请摆放一块与门" not in instruction


FIXED_FIRST_ACTION_CASES = [
    (201, "第一步只邀请摆放一块与非门积木"),
    (202, "第一步只邀请摆放一块非门"),
    (203, "第一步只邀请摆放一块或门积木"),
    (301, "第一步只邀请摆放一块或门"),
    (501, "第二行只邀请摆放一块异或门积木"),
    (503, "第二行只邀请摆放一块或门积木"),
    (504, "第二行只邀请摆放一块异或门积木"),
    (602, "第二行只邀请摆放一块非门积木"),
]


@pytest.mark.parametrize(("level_id", "fixed_instruction"), FIXED_FIRST_ACTION_CASES)
def test_actionable_logic_gate_suppresses_fixed_first_action(
    level_id: int, fixed_instruction: str
) -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == level_id)
    snapshot = build_circuit_coach_v2(level, "placed-io")
    snapshot.board.slots[12].state = "present"
    snapshot.board.slots[12].gate = "AND"
    snapshot.board.slots[12].ports = [
        CircuitCoachV2Port(port_id=48, side="right", role="output"),
        CircuitCoachV2Port(port_id=49, side="down", role="input"),
        CircuitCoachV2Port(port_id=51, side="up", role="input"),
    ]
    request = CircuitCoachDecisionRequest(
        session_id=f"actionable-{level_id}",
        user_text="接下来该怎么做？",
        circuit_snapshot=snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)

    instruction = payload["messages"][-1]["content"]
    assert "电路进度判断（仅依据有效连线）" in instruction
    assert fixed_instruction not in instruction


@pytest.mark.parametrize(("level_id", "fixed_instruction"), FIXED_FIRST_ACTION_CASES)
def test_cold_start_keeps_fixed_first_action(
    level_id: int, fixed_instruction: str
) -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == level_id)
    request = CircuitCoachDecisionRequest(
        session_id=f"cold-start-{level_id}",
        user_text="接下来该怎么做？",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)

    assert fixed_instruction in payload["messages"][-1]["content"]


def test_learning_activity_prompt_requires_tts_safe_plain_text() -> None:
    assert "不要使用 Markdown" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "星号" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "第一个位置" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "slot_roles" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "介绍规则和目标" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "当前值、目标值和一个简短原因" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "两句" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "三个只会是0或1的小开关" in LEARNING_ACTIVITY_SYSTEM_PROMPT
    assert "本题需要放几个1" in LEARNING_ACTIVITY_SYSTEM_PROMPT


def test_circuit_coach_prompt_requires_child_facing_tool_guidance() -> None:
    assert "传输确认" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "上下左右" in CIRCUIT_COACH_V2_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_accepts_empty_slot_tool_call() -> None:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 2:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "看一看亮起的位置，先放一块输入积木吧。"}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-slot",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_empty_slot",
                                        "arguments": json.dumps({"slot": 3, "gate": "INPUT"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    decision_request = CircuitCoachDecisionRequest(
        session_id="device-level-101",
        user_text="接下来应该怎么做？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
    )
    store = RuntimeConfigStore(
        Settings(
            llm_base_url="https://relay.example/v1",
            llm_model="test-model",
            llm_api_key="secret",
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(
            decision_request
        )

    tool_names = [tool["function"]["name"] for tool in captured[0]["tools"]]
    assert tool_names == ["highlight_ports", "highlight_empty_slot"]
    assert "unlocked_gates" in captured[0]["messages"][-1]["content"]
    assert "tools" not in captured[1]
    assert decision.assistant_text == "看一看亮起的位置，先放一块输入积木吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.slot == 3


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_keeps_empty_slot_tool_when_io_ports_exist() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "看一看亮起的位置，把与非门放进去吧。"}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-gate-slot",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_empty_slot",
                                        "arguments": json.dumps({"slot": 5, "gate": "NAND"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    decision_request = CircuitCoachDecisionRequest(
        session_id="device-level-102",
        user_text="接下来应该怎么做？给我点提示",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(
            decision_request
        )

    assert decision.assistant_text == "看一看亮起的位置，把与非门放进去吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.gate == "NAND"


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_preserves_text_when_a_tool_call_is_present() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "我来帮你亮一下这个位置。",
                            "tool_calls": [
                                {
                                    "id": "call-with-text",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_empty_slot",
                                        "arguments": json.dumps({"slot": 5, "gate": "NAND"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-with-text",
        user_text="接下来应该怎么做？给我点提示",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "我来帮你亮一下这个位置。"
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "call-with-text"


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_generates_spoken_text_after_tool_only_response() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-tool-only",
                                        "type": "function",
                                        "function": {
                                            "name": "highlight_empty_slot",
                                            "arguments": json.dumps({"slot": 5, "gate": "NAND"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "看一看亮起的位置，把与非门放在那里吧。"}}]},
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-tool-only",
        user_text="接下来应该怎么做？给我点提示",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "看一看亮起的位置，把与非门放在那里吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "call-tool-only"
    assert len(payloads) == 2
    assert "tools" not in payloads[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_text",
    [
        "已将指令发送。",
        "Ours 0 to 49.",
        "底部的与非门还没有连线。",
        "我看到你已经在底下放好了一个与非门。",
    ],
)
async def test_circuit_coach_v2_client_regenerates_invalid_tool_text(
    invalid_text: str,
) -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": invalid_text,
                                "tool_calls": [
                                    {
                                        "id": "call-transport-ack",
                                        "type": "function",
                                        "function": {
                                            "name": "highlight_empty_slot",
                                            "arguments": json.dumps({"slot": 5, "gate": "NAND"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "先看亮起的位置，把与非门放进去吧。"}}
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-transport-ack",
        user_text="接下来应该怎么做？",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先看亮起的位置，把与非门放进去吧。"
    assert decision.tool_call is not None
    assert len(payloads) == 2
    assert "tools" not in payloads[1]


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_uses_safe_fallback_after_invalid_regeneration() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "Ours 0 to 49.",
                                "tool_calls": [
                                    {
                                        "id": "call-invalid-regeneration",
                                        "type": "function",
                                        "function": {
                                            "name": "highlight_empty_slot",
                                            "arguments": json.dumps(
                                                {"slot": 5, "gate": "NAND"}
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Ours 0 to 49."}}]},
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-invalid-regeneration",
        user_text="接下来应该怎么做？",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "看一看亮起的位置，把需要的积木放进去吧。"
    assert decision.tool_call is not None
    assert len(payloads) == 2
