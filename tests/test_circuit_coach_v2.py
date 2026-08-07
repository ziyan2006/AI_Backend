from __future__ import annotations

import json

import httpx
import pytest

import tuco_ai_backend.providers.circuit_coach_v2 as circuit_coach_v2_provider
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
from tuco_ai_backend.evaluation_presets import load_conversation_presets
from tuco_ai_backend.evaluation_tracing import EvaluationTraceCollector
from tuco_ai_backend.level_logic import get_level_logic_spec
from tuco_ai_backend.models import (
    CircuitCoachDecisionRequest,
    CircuitCoachV2Port,
    CircuitCoachV2Snapshot,
    DecisionResponse,
    ToolCall,
)
from tuco_ai_backend.providers.circuit_coach_v2 import (
    CIRCUIT_COACH_V2_SYSTEM_PROMPT,
    LEARNING_ACTIVITY_SYSTEM_PROMPT,
    CircuitCoachV2Client,
    _candidate_instruction,
    _candidate_spoken_text,
    _disconnect_plan_for_diagnosis,
    _normalize_decision,
    _plan_for_request,
    _semantic_context_for_request,
)
from tuco_ai_backend.providers.openai_compatible import LlmProtocolError
from tuco_ai_backend.tools import CircuitCoachHighlightPortsArgs


def test_parse_turn_decision_downgrades_act_without_candidate_to_clarify() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "decide_circuit_turn",
                                "arguments": json.dumps(
                                    {
                                        "mode": "act",
                                        "assistant_text": "先确认要连接哪一对端口。",
                                        "candidate_id": None,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        }
                    ]
                }
            }
        ]
    }

    decision = CircuitCoachV2Client._parse_turn_decision(payload)

    assert decision.mode == "clarify"
    assert decision.help_seeking is False
    assert decision.candidate_id is None
    assert decision.assistant_text == "先确认要连接哪一对端口。"


def test_parse_turn_decision_normalizes_literal_newline_escape() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "decide_circuit_turn",
                                "arguments": json.dumps(
                                    {
                                        "mode": "hint",
                                        "assistant_text": (
                                            "先观察两个输入。\\n"
                                            "再想想什么时候结果会亮。"
                                        ),
                                        "candidate_id": None,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        }
                    ]
                }
            }
        ]
    }

    decision = CircuitCoachV2Client._parse_turn_decision(payload)

    assert decision.assistant_text == "先观察两个输入。\n再想想什么时候结果会亮。"
    assert "\\n" not in decision.assistant_text


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


def test_normalize_disconnect_keeps_existing_edge_tool() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[3].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="normalize-existing-disconnect",
        user_text="帮我亮出要拆掉的线。",
        circuit_snapshot=snapshot,
    )
    decision = DecisionResponse(
        assistant_text="先拆掉亮红灯的这条线。",
        tool_call=ToolCall(
            call_id="rev32-action-1",
            name="highlight_ports",
            arguments=CircuitCoachHighlightPortsArgs(
                output_port=16,
                input_port=2,
                intent="disconnect",
            ),
        ),
        topology_revision=snapshot.board.topology_revision,
    )

    normalized = _normalize_decision(request, decision)

    assert normalized.tool_call == decision.tool_call


def test_normalize_connect_rejects_existing_edge_ports() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[3].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="normalize-existing-connect",
        user_text="帮我亮出下一条线。",
        circuit_snapshot=snapshot,
    )
    decision = DecisionResponse(
        assistant_text="连接亮红灯的两个端口。",
        tool_call=ToolCall(
            call_id="rev32-action-2",
            name="highlight_ports",
            arguments=CircuitCoachHighlightPortsArgs(
                output_port=16,
                input_port=2,
                intent="connect",
            ),
        ),
        topology_revision=snapshot.board.topology_revision,
    )

    normalized = _normalize_decision(request, decision)

    assert normalized.tool_call is None


def test_connect_candidate_spoken_text_names_source_and_target_gates() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[5].snapshot
    candidate = PlannedCandidate(
        candidate_id="rev33-action-1",
        topology_revision=33,
        action=ConnectPortsAction(output_port=22, input_port=7),
        score=(0,),
        child_facts=("补上这条线后，现有OR积木就能继续产生有用结果。",),
        invalidated_output_indexes=frozenset(),
    )

    spoken = _candidate_spoken_text(candidate, snapshot)

    assert "与门" in spoken
    assert "或门" in spoken
    assert "22" not in spoken
    assert "7" not in spoken


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        ("AND", "同时成立"),
        ("OR", "汇总"),
        ("NOT", "反过来"),
        ("NAND", "都为1"),
        ("NOR", "都为0"),
        ("XOR", "不一样"),
        ("XNOR", "相同"),
    ],
)
def test_place_candidate_spoken_text_explains_gate_purpose(
    gate: str,
    expected: str,
) -> None:
    candidate = PlannedCandidate(
        candidate_id="rev4-action-1",
        topology_revision=4,
        action=PlaceGateAction(slot=4, gate=gate),
        score=(0,),
        child_facts=(),
        invalidated_output_indexes=frozenset(),
    )

    assert expected in _candidate_spoken_text(candidate)


def test_wrong_direct_output_is_prioritized_as_disconnect_action() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[3].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="diagnose-502-next-step",
        user_text="接下来应该怎么做？",
        circuit_snapshot=snapshot,
    )

    plan = _plan_for_request(request)

    assert plan is not None
    assert isinstance(plan.candidates[0].action, DisconnectPortsAction)
    assert plan.candidates[0].action == DisconnectPortsAction(
        output_port=16,
        input_port=2,
    )


def test_flexible_action_question_has_semantic_context() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    request = CircuitCoachDecisionRequest(
        session_id="flexible-action-502",
        user_text="我该从哪儿下手？",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )

    plan, diagnosis = _semantic_context_for_request(request)

    assert plan is not None
    assert plan.candidates
    assert diagnosis is not None


def test_learning_activity_has_no_circuit_semantic_context() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 401)
    request = CircuitCoachDecisionRequest(
        session_id="learning-activity-no-plan",
        user_text="我该从哪儿下手？",
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

    assert _semantic_context_for_request(request) == (None, None)


def test_hint_request_uses_grounded_plan_without_enabling_tools() -> None:
    request = CircuitCoachDecisionRequest(
        session_id="hint-502",
        user_text="我有点不会了，给我一点提示。",
        circuit_snapshot=load_conversation_presets(["502-guidance-quality"])[
            0
        ].scenario.turns[2].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert payload["tool_choice"]["function"]["name"] == "decide_circuit_turn"
    assert payload["tools"][0]["function"]["name"] == "decide_circuit_turn"
    assert "当前提问意图：提示求助" in instruction
    assert "可靠提示依据" in instruction
    assert "或门" in instruction


@pytest.mark.parametrize("level_id", [301, 501, 504])
def test_hint_request_prioritizes_existing_unwired_gate(level_id: int) -> None:
    scenario = next(
        item.scenario
        for item in load_conversation_presets(["all-other-guidance-quality"])
        if item.scenario.level_id == level_id
    )
    request = CircuitCoachDecisionRequest(
        session_id=f"hint-existing-{level_id}",
        user_text=scenario.turns[2].user_text,
        circuit_snapshot=scenario.turns[2].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "当前已经放置与非门" in instruction
    assert "还有2个输入端未连接" in instruction
    assert "不要提议新增积木" in instruction
    assert "后续需要用" not in instruction


def test_diagnosis_request_injects_wrong_edge_fact_without_enabling_tools() -> None:
    request = CircuitCoachDecisionRequest(
        session_id="diagnose-502",
        user_text="我这样接对了吗？哪里有问题？",
        circuit_snapshot=load_conversation_presets(["502-guidance-quality"])[
            0
        ].scenario.turns[3].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert payload["tool_choice"]["function"]["name"] == "decide_circuit_turn"
    assert "当前提问意图：检查诊断" in instruction
    assert "最终输出现在直接来自一块与门" in instruction
    assert "明确指出这条直连线有问题" in instruction


def test_semantic_blocking_edge_does_not_inject_direction_error_explanation() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[2].snapshot
    wrong_snapshot = snapshot.model_copy(
        update={
            "board": snapshot.board.model_copy(
                update={
                    "topology_revision": 32,
                    "edges": [
                        edge
                        for edge in snapshot.board.edges
                        if {edge.port_a, edge.port_b} != {29, 41}
                    ]
                    + [
                        type(snapshot.board.edges[0])(
                            port_a=16,
                            port_b=41,
                            status="valid",
                        )
                    ],
                }
            )
        }
    )
    request = CircuitCoachDecisionRequest(
        session_id="semantic-blocking-diagnosis",
        user_text="我这样接对了吗？哪里有问题？",
        circuit_snapshot=wrong_snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "不能帮助缩短到目标的完成路线" in instruction
    assert "不是端口方向错误" in instruction
    assert "信号必须从发出端流向接收端" not in instruction


def test_explanation_request_names_required_combining_gate() -> None:
    request = CircuitCoachDecisionRequest(
        session_id="explain-502",
        user_text="为什么不能把这个与门直接接到输出？为什么还需要别的积木？",
        circuit_snapshot=load_conversation_presets(["502-guidance-quality"])[
            0
        ].scenario.turns[4].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert payload["tool_choice"]["function"]["name"] == "decide_circuit_turn"
    assert "原理解释依据" in instruction
    assert "后续需要用或门积木" in instruction
    assert "自然说明这种积木负责汇总" in instruction


def test_and_gate_explanation_never_describes_and_as_result_aggregation() -> None:
    scenario = next(
        item.scenario
        for item in load_conversation_presets(["flexible-routing-quality"])
        if item.scenario.level_id == 301
    )
    request = CircuitCoachDecisionRequest(
        session_id="explain-301-and-role",
        user_text=scenario.turns[2].user_text,
        circuit_snapshot=scenario.turns[2].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "与门用来判断两个条件是否同时成立" in instruction
    assert "不得把与门描述为汇总结果" in instruction


@pytest.mark.asyncio
async def test_explanation_decision_adds_missing_required_gate_name() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "一块与门只管一对开关同时亮，漏掉别的组合。\n"
                                "你能找出另外两块与门检查哪一对吗？"
                            )
                        }
                    }
                ]
            },
        )

    request = CircuitCoachDecisionRequest(
        session_id="explain-502-normalized",
        user_text="为什么不能把这个与门直接接到输出？为什么还需要别的积木？",
        circuit_snapshot=load_conversation_presets(["502-guidance-quality"])[
            0
        ].scenario.turns[4].snapshot,
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert "或门" in decision.assistant_text
    assert "汇总" in decision.assistant_text


def test_physical_connection_explanation_stays_on_connection_rule() -> None:
    scenario = next(
        item.scenario
        for item in load_conversation_presets(["all-other-guidance-quality"])
        if item.scenario.level_id == 501
    )
    request = CircuitCoachDecisionRequest(
        session_id="explain-physical-501",
        user_text="为什么这两个光点不能这样连接？",
        circuit_snapshot=scenario.turns[4].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "只解释这条线的连接规则" in instruction
    assert "后续需要用" not in instruction


@pytest.mark.asyncio
async def test_physical_connection_explanation_does_not_append_gate_plan() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "这两个光点都在发出信号，所以谁也接收不到。"
                        }
                    }
                ]
            },
        )

    scenario = next(
        item.scenario
        for item in load_conversation_presets(["all-other-guidance-quality"])
        if item.scenario.level_id == 501
    )
    request = CircuitCoachDecisionRequest(
        session_id="normalize-physical-501",
        user_text="为什么这两个光点不能这样连接？",
        circuit_snapshot=scenario.turns[4].snapshot,
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "这两个光点都在发出信号，所以谁也接收不到。"


def test_missing_components_instruction_requires_exact_counts_without_question() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    request = CircuitCoachDecisionRequest(
        session_id="missing-502",
        user_text="这关要做什么？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "还缺3块输入积木和1块输出积木" in instruction
    assert "直接说出缺少的准确数量" in instruction
    assert "不能反问孩子还缺什么" in instruction
    assert "核心判定条件" in instruction


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


def test_missing_rule_version_still_uses_level_one_semantic_plan() -> None:
    snapshot = _three_input_carry_pairwise_and_snapshot()
    snapshot["level"].pop("rule_version", None)
    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-missing-rule-version",
            "user_text": "怎么做？",
            "circuit_snapshot": snapshot,
        }
    )

    plan = _plan_for_request(request)

    assert plan is not None
    assert isinstance(plan.candidates[0].action, PlaceGateAction)
    assert plan.candidates[0].action.gate == "OR"


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


def test_502_action_guidance_prioritizes_wrong_output_disconnect() -> None:
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
    assert "当前有一条连接需要先调整" in instruction
    assert "先拆掉安全候选指出的这条线" in instruction
    assert "INPUT@6 的未连接输出端接到 AND@12 的空输入端" not in instruction


def test_502_completed_pairwise_and_progress_follows_or_candidate() -> None:
    scenario = load_conversation_presets(["502-guidance-quality"])[0].scenario
    request = CircuitCoachDecisionRequest(
        session_id="fw-502-three-and-complete",
        user_text="接下来应该怎么做？",
        circuit_snapshot=scenario.turns[2].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "本轮优先操作：先摆放一块 OR 积木" in instruction
    assert "不得把任一现有逻辑门直接接到最终输出积木" in instruction
    assert "把它的输出接到 OUTPUT" not in instruction


def test_502_incomplete_pairwise_and_progress_follows_connect_candidate() -> None:
    scenario = load_conversation_presets(["502-guidance-quality"])[0].scenario
    snapshot = scenario.turns[2].snapshot
    incomplete_snapshot = snapshot.model_copy(
        update={
            "board": snapshot.board.model_copy(
                update={
                    "edges": [
                        edge
                        for edge in snapshot.board.edges
                        if {edge.port_a, edge.port_b} != {29, 41}
                    ]
                }
            )
        }
    )
    request = CircuitCoachDecisionRequest(
        session_id="fw-502-three-and-incomplete",
        user_text="接下来应该怎么做？",
        circuit_snapshot=incomplete_snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "本轮优先操作：把 INPUT@7" in instruction
    assert "AND@10 的空输入端" in instruction
    assert "先摆放一块 OR 积木" not in instruction
    assert "把 INPUT@6" not in instruction


@pytest.mark.parametrize(
    ("level_id", "expected_gate"),
    [(202, "NOT"), (203, "OR"), (504, "XOR"), (602, "NOT")],
)
def test_noncanonical_unwired_gate_cannot_override_place_candidate(
    level_id: int, expected_gate: str
) -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == level_id)
    request = CircuitCoachDecisionRequest(
        session_id=f"noncanonical-progress-{level_id}",
        user_text="接下来应该怎么做？",
        circuit_snapshot=build_circuit_coach_v2(level, "actionable-logic"),
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert f"本轮优先操作：先摆放一块 {expected_gate} 积木" in instruction
    assert "把 INPUT@0 的未连接输出端接到 NAND@12" not in instruction


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
    preferred_gate = get_level_logic_spec(level_id, 1).preferred_gate_order[0]
    snapshot.board.slots[12].state = "present"
    snapshot.board.slots[12].gate = preferred_gate
    snapshot.board.slots[12].ports = (
        [
            CircuitCoachV2Port(port_id=48, side="right", role="output"),
            CircuitCoachV2Port(port_id=49, side="down", role="input"),
        ]
        if preferred_gate == "NOT"
        else [
            CircuitCoachV2Port(port_id=48, side="right", role="output"),
            CircuitCoachV2Port(port_id=49, side="down", role="input"),
            CircuitCoachV2Port(port_id=51, side="up", role="input"),
        ]
    )
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


def test_601_actionable_prompt_prioritizes_live_semantic_facts() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 601)
    request = CircuitCoachDecisionRequest(
        session_id="actionable-601-semantic-priority",
        user_text="为什么要这样接？",
        circuit_snapshot=build_circuit_coach_v2(level, "actionable-logic"),
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)

    instruction = payload["messages"][-1]["content"]
    assert "实时语义事实优先规则" in instruction
    assert "不得把与门描述为汇总结果" in instruction
    assert "只有或门可以描述为汇总" in instruction
    assert "不得只说“满足控制条件”" in instruction
    assert "送进与门的两个信号同时亮时" in instruction
    assert "提问时也必须说哪两个信号要同时亮" in instruction


def test_502_actionable_prompt_explains_or_gate_as_combining_pairwise_results() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    request = CircuitCoachDecisionRequest(
        session_id="actionable-502-or-semantics",
        user_text="为什么要这样接？",
        circuit_snapshot=build_circuit_coach_v2(level, "actionable-logic"),
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "或门汇总的是前面与门产生的结果" in instruction
    assert "不得说成直接判断原始开关" in instruction


def test_disconnect_diagnosis_replaces_conflicting_progress_instruction() -> None:
    scenario = next(
        item.scenario
        for item in load_conversation_presets(["flexible-routing-quality"])
        if item.scenario.level_id == 403
    )
    request = CircuitCoachDecisionRequest(
        session_id="actionable-403-disconnect-priority",
        user_text=scenario.turns[4].user_text,
        circuit_snapshot=scenario.turns[4].snapshot,
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "当前有一条真实存在的错误连线" in instruction
    assert "不得建议保留这条线或继续使用被它占用的端口" in instruction
    assert "本轮优先操作：把 INPUT@0" not in instruction
    assert "只需要动哪一下" in instruction
    assert "直接提示开关处于关闭状态" in instruction
    assert "禁止选择 act" in instruction


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
    assert "不得说左边、右边、上面、下面或第几个" in (
        CIRCUIT_COACH_V2_SYSTEM_PROMPT
    )
    assert "积木名称、连接状态或亮起位置" in CIRCUIT_COACH_V2_SYSTEM_PROMPT


def test_circuit_coach_prompt_keeps_identity_and_physical_actor_consistent() -> None:
    assert "图灵号的机载 AI 助手" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "第一句必须明确说“我是图灵号的机载 AI 助手”" in (
        CIRCUIT_COACH_V2_SYSTEM_PROMPT
    )
    assert "身份问题只回答身份或能力" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "不得继续亮灯、接线或摆放提示" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "孩子才是实际动手的人" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "我先放" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "我来接" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "不得对孩子说 XOR、NAND" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "与非门不能说成非与门" in CIRCUIT_COACH_V2_SYSTEM_PROMPT


def test_circuit_coach_prompt_forbids_story_objects_as_connection_targets() -> None:
    assert "输出积木的输入端" in CIRCUIT_COACH_V2_SYSTEM_PROMPT
    assert "不得称为灯、灯泡" in CIRCUIT_COACH_V2_SYSTEM_PROMPT


def test_circuit_coach_payload_forces_structured_turn_decision() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-payload",
        user_text="我该从哪儿下手？",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"历史{index}"}
        for index in range(7)
    ]

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request, history=history)

    assert payload["tools"][0]["function"]["name"] == "decide_circuit_turn"
    assert payload["tool_choice"]["function"]["name"] == "decide_circuit_turn"
    assert payload["parallel_tool_calls"] is False
    history_messages = payload["messages"][1:-1]
    assert [item["content"] for item in history_messages] == [
        f"历史{index}" for index in range(1, 7)
    ]
    assert "它、这个、刚才那个" in payload["messages"][-1]["content"]


def test_direct_hint_prompt_focuses_on_one_child_action_without_inventory_recap() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    request = CircuitCoachDecisionRequest(
        session_id="direct-hint-natural-wording",
        user_text="接下来应该怎么做？",
        direct_hint_requested=True,
        circuit_snapshot=build_circuit_coach_v2(level, "actionable-logic"),
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "只描述当前这个安全候选动作" in instruction
    assert "不要顺带盘点无关的已放积木" in instruction
    assert "不得以“已经有”“现在已有”“现有的”" in instruction
    assert "不得先盘点已完成部分" in instruction
    assert "我帮你把位置亮出来" in instruction


def test_guidance_prompt_translates_abstract_terms_and_explains_the_last_step() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 601)
    request = CircuitCoachDecisionRequest(
        session_id="guidance-concrete-language",
        user_text="为什么要这样接？",
        circuit_snapshot=build_circuit_coach_v2(level, "actionable-logic"),
    )

    payload = CircuitCoachV2Client(
        RuntimeConfigStore(Settings(llm_api_key="configured"))
    )._build_payload(request)
    instruction = payload["messages"][-1]["content"]

    assert "先用孩子看得见的现象说白话" in instruction
    assert "哪个开关亮或灭、哪一路通过、结果是否亮" in instruction
    assert "这一步是为了" in instruction
    assert "只能处理部分情况" in instruction
    assert "第一句直接回答这一步的目的" in instruction


@pytest.mark.asyncio
async def test_hint_mode_returns_text_without_device_tool() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-hint",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "hint",
                                                "assistant_text": "先观察哪一块积木还没有接线。",
                                                "candidate_id": None,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-hint",
        user_text="给我一点点方向，别直接公布答案。",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先观察哪一块积木还没有接线。"
    assert decision.tool_call is None


@pytest.mark.asyncio
async def test_explicit_unique_action_overrides_hint_route() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[2].snapshot
    incomplete_snapshot = snapshot.model_copy(
        update={
            "board": snapshot.board.model_copy(
                update={
                    "edges": [
                        edge
                        for edge in snapshot.board.edges
                        if {edge.port_a, edge.port_b} != {29, 41}
                    ]
                }
            )
        }
    )
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-force-unique-act",
        user_text="现在只告诉我该动哪一下，并亮灯提示。",
        direct_hint_requested=True,
        circuit_snapshot=incomplete_snapshot,
    )
    grounding_plan, diagnosis = _semantic_context_for_request(request)
    assert grounding_plan is not None
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or grounding_plan
    assert len(execution_plan.candidates) == 1

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-wrong-hint",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "chat",
                                                "assistant_text": "找找哪块与门还有空着的输入端。",
                                                "help_seeking": True,
                                                "candidate_id": None,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.arguments.output_port == 29
    assert decision.tool_call.arguments.input_port == 41
    assert decision.tool_call.arguments.intent == "connect"


async def test_next_step_chat_route_is_replaced_with_grounded_connection_hint() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[5].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-grounded-next-step",
        user_text="我已经拆掉错误的线，放好了或门，也接进去一组结果，接下来做什么？",
        circuit_snapshot=snapshot,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-status-only-chat",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "chat",
                                                "assistant_text": "你已经把一组结果接进或门了。",
                                                "candidate_id": None,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is None
    assert "与门" in decision.assistant_text
    assert "或门" in decision.assistant_text
    assert "接到" in decision.assistant_text
    assert "已经把一组结果接进或门" not in decision.assistant_text


async def test_direct_hint_promotes_grounded_hint_to_one_safe_action() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[5].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="direct-hint-promote",
        user_text="接下来应该怎么做？",
        direct_hint_requested=True,
        circuit_snapshot=snapshot,
    )
    plan, diagnosis = _semantic_context_for_request(request)
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or plan
    assert execution_plan is not None
    candidate = execution_plan.candidates[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-direct-hint",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "hint",
                                                "assistant_text": "再找一组还没汇总的结果。",
                                                "help_seeking": True,
                                                "candidate_id": None,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.arguments.output_port == candidate.action.output_port
    assert decision.tool_call.arguments.input_port == candidate.action.input_port
    assert decision.tool_call.arguments.intent == "connect"


async def test_direct_hint_accepts_natural_help_seeking_without_keyword_match() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[5].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="direct-hint-natural-help",
        user_text="这一步我有点迷糊，能指给我看吗？",
        direct_hint_requested=True,
        circuit_snapshot=snapshot,
    )
    plan, diagnosis = _semantic_context_for_request(request)
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or plan
    assert execution_plan is not None
    candidate = execution_plan.candidates[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-natural-help",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "hint",
                                                "assistant_text": "我给你指出一小步。",
                                                "help_seeking": True,
                                                "candidate_id": None,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.arguments.output_port == candidate.action.output_port
    assert decision.tool_call.arguments.input_port == candidate.action.input_port


@pytest.mark.parametrize(
    ("user_text", "assistant_text"),
    [
        ("你是谁？", "我是图灵号的机载 AI 助手。"),
        ("这关要做什么？", "这关要完成当前的电路任务。"),
        ("什么是与门？", "与门会判断两个条件是否同时成立。"),
        ("为什么要这样接？", "这样连接是为了让信号继续参与判断。"),
        ("为什么下一步要这样做？", "这一步是为了让信号继续参与判断。"),
    ],
)
async def test_direct_hint_vetoes_model_action_for_non_action_question(
    user_text: str,
    assistant_text: str,
) -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[5].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="direct-hint-chat-mistouch",
        user_text=user_text,
        direct_hint_requested=True,
        circuit_snapshot=snapshot,
    )
    plan, diagnosis = _semantic_context_for_request(request)
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or plan
    assert execution_plan is not None
    candidate = execution_plan.candidates[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-chat-wrong-act",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": assistant_text,
                                                "help_seeking": True,
                                                "candidate_id": candidate.candidate_id,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is None
    assert decision.assistant_text == assistant_text


async def test_direct_hint_disabled_vetoes_model_action_for_next_step_request() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[5].snapshot
    request = CircuitCoachDecisionRequest(
        session_id="direct-hint-disabled",
        user_text="接下来应该怎么做？",
        direct_hint_requested=False,
        circuit_snapshot=snapshot,
    )
    plan, diagnosis = _semantic_context_for_request(request)
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or plan
    assert execution_plan is not None
    candidate = execution_plan.candidates[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-direct-hint-disabled",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先完成这一小步吧。",
                                                "help_seeking": True,
                                                "candidate_id": candidate.candidate_id,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is None
    assert "与门" in decision.assistant_text
    assert "或门" in decision.assistant_text
    assert "输入端" in decision.assistant_text


@pytest.mark.asyncio
async def test_structured_act_uses_preferred_candidate_when_model_returns_hint() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 403)
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-explicit-act",
        user_text="请给我执行下一步。",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-hint-despite-act",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "hint",
                                                "assistant_text": "先想想哪种积木负责个位结果。",
                                                "candidate_id": None,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.gate == "XOR"
    assert "异或门" in decision.assistant_text
    assert "另一块" not in decision.assistant_text


@pytest.mark.asyncio
async def test_model_act_cannot_choose_nonpreferred_safe_candidate() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 403)
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-preferred-candidate",
        user_text="接下来应该怎么做？",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    plan, _diagnosis = _semantic_context_for_request(request)
    assert plan is not None
    assert len(plan.candidates) >= 2

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-nonpreferred-act",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先放另一块也能用的积木。",
                                                "candidate_id": plan.candidates[1].candidate_id,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.gate == "XOR"


@pytest.mark.asyncio
async def test_act_mode_maps_valid_candidate() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 403)
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-act",
        user_text="好，那此刻我只需要动哪一下？",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    grounding_plan, diagnosis = _semantic_context_for_request(request)
    assert grounding_plan is not None
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or grounding_plan
    candidate_id = execution_plan.candidates[0].candidate_id

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-act",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先完成这一小步吧。",
                                                "candidate_id": candidate_id,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None


@pytest.mark.asyncio
async def test_invalid_candidate_never_falls_back_to_first_action() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-invalid",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先完成这一小步吧。",
                                                "candidate_id": "rev999-action-9",
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 403)
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-invalid",
        user_text="现在直接帮我亮出下一步。",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is None
    assert decision.assistant_text == "电路刚刚发生了变化，请再问我一次下一步。"


@pytest.mark.asyncio
async def test_mapped_action_failure_uses_candidate_grounded_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 403)
    request = CircuitCoachDecisionRequest(
        session_id="structured-turn-grounded-fallback",
        user_text="现在直接帮我亮出下一步。",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    grounding_plan, diagnosis = _semantic_context_for_request(request)
    assert grounding_plan is not None
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or grounding_plan
    candidate = execution_plan.candidates[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "turn-grounded-fallback",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先完成这一小步吧。",
                                                "candidate_id": candidate.candidate_id,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        circuit_coach_v2_provider,
        "_map_candidate_to_decision",
        lambda *_args, **_kwargs: None,
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is None
    assert candidate.child_facts[0] in (decision.assistant_text or "")
    assert "空着的槽位" in (decision.assistant_text or "")
    assert "我先确认一下" not in (decision.assistant_text or "")
    assert "再问我一次" not in (decision.assistant_text or "")


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
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先放一块输入积木吧。",
                                                "candidate_id": "rev0-action-1",
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

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    decision_request = CircuitCoachDecisionRequest(
        session_id="device-level-101",
        user_text="接下来应该怎么做？",
        interaction_intent="act",
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
    assert tool_names == ["decide_circuit_turn"]
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
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": (
                                                    "先用与非门看看两个输入是不是都为1，"
                                                    "放一块与非门积木吧。"
                                                ),
                                                "candidate_id": "rev3-action-1",
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

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    decision_request = CircuitCoachDecisionRequest(
        session_id="device-level-102",
        user_text="接下来应该怎么做？给我点提示",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(
            decision_request
        )

    assert decision.assistant_text == "先用与非门看看两个输入是不是都为1，放一块与非门积木吧。"
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
        "decide_circuit_turn"
    ]
    assert payload["tool_choice"]["function"]["name"] == "decide_circuit_turn"
    assert "rev31-action-1" in payload["messages"][-1]["content"]
    assert "output_port" not in payload["messages"][-1]["content"]


def test_chat_payload_still_requires_structured_turn_routing() -> None:
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

    assert payload["tool_choice"]["function"]["name"] == "decide_circuit_turn"
    assert payload["tools"][0]["function"]["name"] == "decide_circuit_turn"


@pytest.mark.asyncio
async def test_unknown_candidate_does_not_use_local_fallback() -> None:
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
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先完成这一小步吧。",
                                                "candidate_id": "rev31-action-999",
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

    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-unknown-candidate",
            "user_text": "接下来应该怎么做？给我点提示",
            "interaction_intent": "act",
            "circuit_snapshot": _three_input_carry_pairwise_and_snapshot(),
        }
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "电路刚刚发生了变化，请再问我一次下一步。"
    assert decision.tool_call is None


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
async def test_502_structured_router_rejects_direct_hardware_tool() -> None:
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
        with pytest.raises(LlmProtocolError, match="unsupported circuit turn tool call"):
            await CircuitCoachV2Client(store, http_client=http_client).decide(request)


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

    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "fw-502-first-or",
            "user_text": "接下来应该怎么做？给我点提示",
            "interaction_intent": "act",
            "circuit_snapshot": snapshot,
        }
    )
    plan, diagnosis = _semantic_context_for_request(request)
    assert plan is not None
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or plan
    candidate = next(
        item
        for item in execution_plan.candidates
        if isinstance(item.action, ConnectPortsAction)
        and item.action.input_port in {5, 7}
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "wrong-direct-output-again",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "把一组与门结果接进或门吧。",
                                                "candidate_id": candidate.candidate_id,
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

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_ports"
    assert decision.tool_call.arguments.output_port in {16, 22, 40}
    assert decision.tool_call.arguments.input_port in {5, 7}


@pytest.mark.asyncio
async def test_connect_tool_replaces_status_only_text_with_actionable_guidance() -> None:
    scenario = load_conversation_presets(["502-guidance-quality"])[0].scenario
    request = CircuitCoachDecisionRequest(
        session_id="connect-guidance-must-name-action",
        user_text=scenario.turns[5].user_text,
        interaction_intent="act",
        circuit_snapshot=scenario.turns[5].snapshot,
    )
    plan, diagnosis = _semantic_context_for_request(request)
    execution_plan = (
        _disconnect_plan_for_diagnosis(request, diagnosis)
        if diagnosis is not None
        else None
    ) or plan
    assert execution_plan is not None
    candidate = execution_plan.candidates[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "status-only-connect",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "已经接进一组结果了。",
                                                "candidate_id": candidate.candidate_id,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.tool_call is not None
    assert decision.tool_call.arguments.output_port == 22
    assert decision.tool_call.arguments.input_port == 7
    assert decision.assistant_text != "已经接进一组结果了。"
    assert any(word in decision.assistant_text for word in ("接到", "连到", "接入"))
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
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "我来帮你亮一下这个位置。",
                                                "candidate_id": "rev3-action-1",
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

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-with-text",
        user_text="接下来应该怎么做？给我点提示",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "我来帮你亮一下这个位置。"
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "rev3-action-1"


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_uses_text_from_structured_turn() -> None:
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
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": (
                                                    "先用与非门看看两个输入是不是都为1，"
                                                    "放一块与非门积木吧。"
                                                ),
                                                "candidate_id": "rev3-action-1",
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

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-tool-only",
        user_text="接下来应该怎么做？给我点提示",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先用与非门看看两个输入是不是都为1，放一块与非门积木吧。"
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
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": invalid_text,
                                                "candidate_id": "rev3-action-1",
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

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-transport-ack",
        user_text="接下来应该怎么做？",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先用与非门看看两个输入是不是都为1，放一块与非门积木吧。"
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
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "Ours 0 to 49.",
                                                "candidate_id": "rev3-action-1",
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

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-invalid-regeneration",
        user_text="接下来应该怎么做？",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "先用与非门看看两个输入是不是都为1，放一块与非门积木吧。"
    assert decision.tool_call is not None
    assert len(payloads) == 1


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_records_complete_decision_trace() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-request-id": "req-1"},
            json={
                "choices": [
                    {
                        "message": {
                            "content": "先放一块与非门积木吧。",
                            "tool_calls": [
                                {
                                    "id": "choose-traced",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_circuit_turn",
                                        "arguments": json.dumps(
                                            {
                                                "mode": "act",
                                                "assistant_text": "先放一块与非门积木吧。",
                                                "candidate_id": "rev3-action-1",
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

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-trace",
        user_text="接下来应该怎么做？",
        interaction_intent="act",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    history = [{"role": "user", "content": "这关要做什么？"}]
    collector = EvaluationTraceCollector()
    store = RuntimeConfigStore(Settings(llm_api_key="secret", llm_model="trace-model"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(
            store,
            http_client=http_client,
            trace_sink=collector,
        ).decide(request, history=history, trace_id="trace-provider")

    trace = collector.take("trace-provider")
    assert trace["decision_request"]["user_text"] == "接下来应该怎么做？"
    assert trace["conversation_history"] == history
    assert trace["semantic_plan"]["execution_plan"]["candidates"][0][
        "candidate_id"
    ] == "rev3-action-1"
    assert trace["route_decision"] == {
        "mode": "act",
        "help_seeking": True,
        "candidate_id": "rev3-action-1",
        "candidate_resolved": True,
        "tool_mapped": True,
        "direct_hint_requested": False,
    }
    assert trace["provider_request"]["model"] == "trace-model"
    assert trace["provider_request"]["messages"][-1]["role"] == "user"
    assert trace["provider_response"]["status_code"] == 200
    assert trace["provider_response"]["headers"] == {
        "content-type": "application/json",
        "x-request-id": "req-1",
    }
    assert trace["provider_response"]["body"]["choices"][0]["message"][
        "tool_calls"
    ]
    assert trace["normalized_decision"]["tool_call"]["name"] == "highlight_empty_slot"
    json.dumps(trace, ensure_ascii=False)
    assert decision.tool_call is not None


@pytest.mark.asyncio
async def test_circuit_coach_v2_trace_sink_failure_does_not_break_decision(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingTraceSink:
        def record(self, trace_id: str, event: str, payload: object) -> None:
            raise RuntimeError(f"cannot record {trace_id}:{event}:{payload!r}")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "我是图灵号的机载AI助手。"}}]},
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-101-trace-failure",
        user_text="你是谁？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(
            store,
            http_client=http_client,
            trace_sink=FailingTraceSink(),
        ).decide(request, trace_id="trace-failure")

    assert decision.assistant_text == "我是图灵号的机载AI助手。"
    assert "decision trace sink failed" in caplog.text


@pytest.mark.asyncio
async def test_transient_provider_failure_retries_once_and_records_attempts() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "我是图灵号的机载AI助手。"}}]},
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    request = CircuitCoachDecisionRequest(
        session_id="provider-retry-once",
        user_text="你是谁？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
    )
    collector = EvaluationTraceCollector()
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(
            store,
            http_client=http_client,
            trace_sink=collector,
        ).decide(request, trace_id="retry-trace")

    trace = collector.take("retry-trace")
    assert attempts == 2
    assert decision.assistant_text == "我是图灵号的机载AI助手。"
    assert trace["provider_attempts"] == [
        {"attempt": 1, "outcome": "retry", "error_type": "ReadTimeout"},
        {"attempt": 2, "outcome": "success", "status_code": 200},
    ]


@pytest.mark.asyncio
async def test_second_transient_failure_is_raised_without_local_fallback() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("still unavailable", request=request)

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    request = CircuitCoachDecisionRequest(
        session_id="provider-retry-fails",
        user_text="你是谁？",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
    )
    collector = EvaluationTraceCollector()
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(httpx.ReadTimeout, match="still unavailable"):
            await CircuitCoachV2Client(
                store,
                http_client=http_client,
                trace_sink=collector,
            ).decide(request, trace_id="retry-failed-trace")

    trace = collector.take("retry-failed-trace")
    assert attempts == 2
    assert trace["provider_attempts"] == [
        {"attempt": 1, "outcome": "retry", "error_type": "ReadTimeout"},
        {"attempt": 2, "outcome": "failed", "error_type": "ReadTimeout"},
    ]
