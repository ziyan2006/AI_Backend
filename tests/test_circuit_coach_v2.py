from __future__ import annotations

import json

import httpx
import pytest

from tuco_ai_backend.circuit_planner import (
    CircuitPlan,
    ConnectPortsAction,
    DisconnectPortsAction,
    PlaceGateAction,
    PlannedCandidate,
    plan_circuit_actions,
)
from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES, build_circuit_coach_v2
from tuco_ai_backend.level_logic import get_level_logic_spec
from tuco_ai_backend.models import (
    CircuitCoachDecisionRequest,
    CircuitCoachV2Port,
    CircuitCoachV2Snapshot,
)
from tuco_ai_backend.providers.circuit_coach_v2 import (
    CIRCUIT_COACH_V2_SYSTEM_PROMPT,
    LEARNING_ACTIVITY_SYSTEM_PROMPT,
    CircuitCoachV2Client,
    _candidate_instruction,
    _candidate_spoken_text,
)


def test_disconnect_candidate_uses_disconnect_wording() -> None:
    candidate = PlannedCandidate(
        candidate_id="rev3-action-1",
        topology_revision=3,
        action=DisconnectPortsAction(output_port=16, input_port=2),
        score=(3,),
        child_facts=("这条线形成了环路。",),
        invalidated_output_indexes=frozenset(),
    )
    plan = CircuitPlan(
        candidates=(candidate,),
        preserved_output_indexes=frozenset(),
        search_states=0,
        elapsed_ms=0.0,
    )

    assert "拆掉一条" in _candidate_instruction(plan)
    assert _candidate_spoken_text(candidate) == "先拆掉亮红灯的这条线。"


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


def test_v2_level_accepts_rule_version() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    snapshot = build_circuit_coach_v2(level, "empty").model_dump(by_alias=True)
    snapshot["level"]["rule_version"] = 1

    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "device-level-502",
            "user_text": "接下来怎么做？",
            "circuit_snapshot": snapshot,
        }
    )

    assert request.circuit_snapshot.level.rule_version == 1


def test_missing_rule_version_remains_parseable() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    snapshot = build_circuit_coach_v2(level, "empty").model_dump(by_alias=True)
    snapshot["level"].pop("rule_version", None)

    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "legacy-device-level-502",
            "user_text": "接下来怎么做？",
            "circuit_snapshot": snapshot,
        }
    )

    assert request.circuit_snapshot.level.rule_version is None


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
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "choose-input",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_circuit_action",
                                        "arguments": json.dumps(
                                            {"candidate_id": "rev0-action-1"}
                                        ),
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
    assert tool_names == ["choose_circuit_action"]
    assert "unlocked_gates" in captured[0]["messages"][-1]["content"]
    assert len(captured) == 1
    assert decision.assistant_text == "先放一块输入积木吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.call_id == "rev0-action-1"
    assert decision.tool_call.arguments.slot == 0


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_keeps_empty_slot_tool_when_io_ports_exist() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "choose-nand",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_circuit_action",
                                        "arguments": json.dumps(
                                            {"candidate_id": "rev3-action-1"}
                                        ),
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

    assert decision.assistant_text == "先放一块与非门积木吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.slot == 3
    assert decision.tool_call.arguments.gate == "NAND"


def _three_input_carry_pairwise_and_snapshot() -> dict[str, object]:
    return {
        "schema": "tuco_circuit_v2",
        "level": {
            "id": 502,
            "rule_version": 1,
            "goal": "局部进位",
            "inputs": "加数 A, B, C",
            "outputs": "局部进位",
            "input_count": 3,
            "output_count": 1,
        },
        "unlocked_gates": ["INPUT", "OUTPUT", "AND", "OR", "XOR"],
        "board": {
            "topology_revision": 31,
            "link_count": 6,
            "slots": [
                [0, 0, 0, "present", "OUTPUT", [[2, "left", "input"]]],
                [1, 0, 1, "empty", None, []],
                [
                    4,
                    0,
                    2,
                    "present",
                    "AND",
                    [
                        [16, "right", "output"],
                        [17, "down", "input"],
                        [19, "up", "input"],
                    ],
                ],
                [
                    5,
                    1,
                    2,
                    "present",
                    "AND",
                    [
                        [22, "right", "output"],
                        [21, "down", "input"],
                        [23, "up", "input"],
                    ],
                ],
                [
                    6,
                    0,
                    0,
                    "present",
                    "INPUT",
                    [[24, "right", "output"], [25, "down", "output"]],
                ],
                [
                    7,
                    1,
                    0,
                    "present",
                    "INPUT",
                    [[28, "right", "output"], [29, "down", "output"]],
                ],
                [
                    8,
                    2,
                    0,
                    "present",
                    "INPUT",
                    [[32, "right", "output"], [33, "down", "output"]],
                ],
                [
                    10,
                    2,
                    2,
                    "present",
                    "AND",
                    [
                        [40, "right", "output"],
                        [41, "down", "input"],
                        [43, "up", "input"],
                    ],
                ],
            ],
            "edges": [
                [24, 17, "valid"],
                [28, 19, "valid"],
                [25, 21, "valid"],
                [32, 23, "valid"],
                [29, 41, "valid"],
                [33, 43, "valid"],
            ],
        },
    }


def test_action_payload_only_exposes_safe_candidate_selection_tool() -> None:
    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-candidates",
            "user_text": "接下来应该怎么做？给我点提示",
            "circuit_snapshot": _three_input_carry_pairwise_and_snapshot(),
        }
    )
    plan = plan_circuit_actions(
        request.circuit_snapshot, get_level_logic_spec(502, 1)
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request, plan=plan)

    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "choose_circuit_action"
    ]
    assert payload["tool_choice"] == "auto"
    assert "rev31-action-1" in payload["messages"][-1]["content"]
    assert "output_port" not in payload["messages"][-1]["content"]


def test_chat_payload_disables_circuit_action_tools() -> None:
    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-chat",
            "user_text": "你是谁？",
            "circuit_snapshot": _three_input_carry_pairwise_and_snapshot(),
        }
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)

    assert payload["tool_choice"] == "none"
    assert "tools" not in payload


@pytest.mark.asyncio
async def test_unknown_candidate_uses_highest_safe_local_fallback() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "unknown-candidate",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_circuit_action",
                                        "arguments": json.dumps(
                                            {"candidate_id": "rev31-action-999"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-unknown-candidate",
            "user_text": "接下来应该怎么做？给我点提示",
            "circuit_snapshot": _three_input_carry_pairwise_and_snapshot(),
        }
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text is not None
    assert "或门" in decision.assistant_text
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.slot == 1
    assert decision.tool_call.arguments.gate == "OR"


def test_502_real_snapshot_generic_plan_requires_or_before_output() -> None:
    snapshot = CircuitCoachV2Snapshot.model_validate(
        _three_input_carry_pairwise_and_snapshot()
    )

    plan = plan_circuit_actions(snapshot, get_level_logic_spec(502, 1))

    assert plan.candidates
    assert all(
        not isinstance(candidate.action, ConnectPortsAction)
        or candidate.action.input_port != 2
        for candidate in plan.candidates
    )
    assert isinstance(plan.candidates[0].action, PlaceGateAction)
    assert plan.candidates[0].action.gate == "OR"


@pytest.mark.asyncio
async def test_502_semantic_guard_replaces_invalid_direct_output_highlight() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "把亮起的两个光点连起来。",
                            "tool_calls": [
                                {
                                    "id": "wrong-direct-output",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_ports",
                                        "arguments": json.dumps(
                                            {"output_port": 16, "input_port": 2}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-pairwise-and",
            "user_text": "接下来应该怎么做？给我点提示",
            "circuit_snapshot": _three_input_carry_pairwise_and_snapshot(),
        }
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text is not None
    assert "或门" in decision.assistant_text
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.gate == "OR"
    assert decision.tool_call.arguments.slot == 1


@pytest.mark.asyncio
async def test_502_semantic_guard_connects_pairwise_result_only_into_or_gate() -> None:
    snapshot = _three_input_carry_pairwise_and_snapshot()
    board = snapshot["board"]
    assert isinstance(board, dict)
    slots = board["slots"]
    assert isinstance(slots, list)
    slots[1] = [
        1,
        0,
        1,
        "present",
        "OR",
        [[4, "right", "output"], [5, "down", "input"], [7, "up", "input"]],
    ]

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "把亮起的两个光点连起来。",
                            "tool_calls": [
                                {
                                    "id": "wrong-direct-output-again",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_ports",
                                        "arguments": json.dumps(
                                            {"output_port": 22, "input_port": 2}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-first-or",
            "user_text": "接下来应该怎么做？给我点提示",
            "circuit_snapshot": snapshot,
        }
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_ports"
    assert decision.tool_call.arguments.output_port in {16, 22, 40}
    assert decision.tool_call.arguments.input_port in {5, 7}
    assert decision.tool_call.arguments.input_port != 2


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
                                    "id": "choose-with-text",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_circuit_action",
                                        "arguments": json.dumps(
                                            {"candidate_id": "rev3-action-1"}
                                        ),
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
    assert decision.tool_call.call_id == "rev3-action-1"


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_generates_spoken_text_after_tool_only_response() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "choose-tool-only",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_circuit_action",
                                        "arguments": json.dumps(
                                            {"candidate_id": "rev3-action-1"}
                                        ),
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
        session_id="device-level-102-tool-only",
        user_text="接下来应该怎么做？给我点提示",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先放一块与非门积木吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "rev3-action-1"
    assert len(payloads) == 1


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
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": invalid_text,
                            "tool_calls": [
                                {
                                    "id": "choose-invalid-text",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_circuit_action",
                                        "arguments": json.dumps(
                                            {"candidate_id": "rev3-action-1"}
                                        ),
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
        session_id="device-level-102-transport-ack",
        user_text="接下来应该怎么做？",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先放一块与非门积木吧。"
    assert decision.tool_call is not None
    assert len(payloads) == 1


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_uses_safe_fallback_after_invalid_regeneration() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Ours 0 to 49.",
                            "tool_calls": [
                                {
                                    "id": "choose-invalid-regeneration",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_circuit_action",
                                        "arguments": json.dumps(
                                            {"candidate_id": "rev3-action-1"}
                                        ),
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
        session_id="device-level-102-invalid-regeneration",
        user_text="接下来应该怎么做？",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先放一块与非门积木吧。"
    assert decision.tool_call is not None
    assert len(payloads) == 1
