from __future__ import annotations

from tuco_ai_backend.circuit_planner import (
    ConnectPortsAction,
    DisconnectPortsAction,
    PlaceGateAction,
    plan_circuit_actions,
)
from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES, build_circuit_coach_v2
from tuco_ai_backend.level_logic import LevelLogicSpec, get_level_logic_spec
from tuco_ai_backend.models import CircuitCoachV2Edge, CircuitCoachV2Snapshot

THREE_INPUT_OR_SPEC = LevelLogicSpec(
    level_id=9001,
    rule_version=1,
    input_count=3,
    output_count=1,
    short_goal="测试三输入或门",
    input_labels=("A", "B", "C"),
    output_labels=("Y",),
    expected_outputs=(0, 1, 1, 1, 1, 1, 1, 1),
    preferred_gate_order=("OR",),
)


def _snapshot(
    *,
    level_id: int,
    unlocked_gates: list[str],
    slots: list[list[object]],
    edges: list[list[object]],
    topology_revision: int = 1,
) -> CircuitCoachV2Snapshot:
    return CircuitCoachV2Snapshot.model_validate(
        {
            "schema": "tuco_circuit_v2",
            "level": {"id": level_id, "rule_version": 1},
            "unlocked_gates": unlocked_gates,
            "board": {
                "topology_revision": topology_revision,
                "slots": slots,
                "edges": edges,
            },
        }
    )


def _binary_gate_slot(
    slot_id: int,
    row: int,
    column: int,
    gate: str,
    output_port: int,
    first_input_port: int,
    second_input_port: int,
) -> list[object]:
    return [
        slot_id,
        row,
        column,
        "present",
        gate,
        [
            [output_port, "right", "output"],
            [first_input_port, "left", "input"],
            [second_input_port, "up", "input"],
        ],
    ]


def real_502_pairwise_and_snapshot() -> CircuitCoachV2Snapshot:
    return _snapshot(
        level_id=502,
        unlocked_gates=["INPUT", "OUTPUT", "AND", "OR", "XOR"],
        topology_revision=31,
        slots=[
            [0, 0, 0, "present", "OUTPUT", [[2, "left", "input"]]],
            [1, 0, 1, "empty", None, []],
            _binary_gate_slot(4, 0, 2, "AND", 16, 17, 19),
            _binary_gate_slot(5, 1, 2, "AND", 22, 21, 23),
            [6, 0, 0, "present", "INPUT", [[24, "right", "output"], [25, "down", "output"]]],
            [7, 1, 0, "present", "INPUT", [[28, "right", "output"], [29, "down", "output"]]],
            [8, 2, 0, "present", "INPUT", [[32, "right", "output"], [33, "down", "output"]]],
            _binary_gate_slot(10, 2, 2, "AND", 40, 41, 43),
        ],
        edges=[
            [24, 17, "valid"],
            [28, 19, "valid"],
            [25, 21, "valid"],
            [32, 23, "valid"],
            [29, 41, "valid"],
            [33, 43, "valid"],
        ],
    )


def nand_only_xor_partial_snapshot() -> CircuitCoachV2Snapshot:
    return _snapshot(
        level_id=301,
        unlocked_gates=["INPUT", "OUTPUT", "NAND"],
        slots=[
            [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
            [1, 0, 1, "present", "INPUT", [[4, "right", "output"]]],
            [2, 0, 2, "present", "OUTPUT", [[8, "left", "input"]]],
            [3, 0, 3, "empty", None, []],
        ],
        edges=[],
    )


def partially_connected_or_snapshot() -> CircuitCoachV2Snapshot:
    return _snapshot(
        level_id=9001,
        unlocked_gates=["INPUT", "OUTPUT", "OR"],
        slots=[
            [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
            [1, 0, 1, "present", "INPUT", [[4, "right", "output"]]],
            [2, 0, 2, "present", "INPUT", [[8, "right", "output"]]],
            [3, 0, 3, "present", "OUTPUT", [[12, "left", "input"]]],
            _binary_gate_slot(4, 1, 0, "OR", 16, 17, 18),
            [5, 1, 1, "empty", None, []],
        ],
        edges=[[0, 17, "valid"]],
    )


def full_adder_snapshot_with_correct_sum_only() -> CircuitCoachV2Snapshot:
    return _snapshot(
        level_id=503,
        unlocked_gates=["INPUT", "OUTPUT", "AND", "OR", "XOR"],
        slots=[
            [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
            [1, 0, 1, "present", "INPUT", [[4, "right", "output"]]],
            [2, 0, 2, "present", "INPUT", [[8, "right", "output"]]],
            [3, 0, 3, "present", "OUTPUT", [[12, "left", "input"]]],
            [4, 1, 0, "present", "OUTPUT", [[16, "left", "input"]]],
            _binary_gate_slot(5, 1, 1, "XOR", 20, 21, 22),
            _binary_gate_slot(6, 1, 2, "XOR", 24, 25, 26),
            [7, 1, 3, "empty", None, []],
        ],
        edges=[
            [0, 21, "valid"],
            [4, 22, "valid"],
            [20, 25, "valid"],
            [8, 26, "valid"],
            [24, 12, "valid"],
        ],
    )


def downstream_wrong_branch_snapshot() -> CircuitCoachV2Snapshot:
    return _snapshot(
        level_id=201,
        unlocked_gates=["INPUT", "OUTPUT", "AND", "OR"],
        topology_revision=44,
        slots=[
            [
                0,
                0,
                0,
                "present",
                "INPUT",
                [
                    [0, "right", "output"],
                    [1, "down", "output"],
                    [2, "up", "output"],
                ],
            ],
            [
                1,
                0,
                1,
                "present",
                "INPUT",
                [[4, "right", "output"], [5, "down", "output"]],
            ],
            [2, 0, 2, "present", "OUTPUT", [[8, "left", "input"]]],
            _binary_gate_slot(3, 1, 0, "OR", 14, 12, 13),
            _binary_gate_slot(4, 1, 1, "AND", 19, 17, 18),
        ],
        edges=[
            [0, 12, "valid"],
            [4, 13, "valid"],
            [14, 17, "valid"],
            [5, 18, "valid"],
            [19, 8, "valid"],
        ],
    )


def test_majority_pairwise_and_requires_or_before_output() -> None:
    plan = plan_circuit_actions(
        real_502_pairwise_and_snapshot(), get_level_logic_spec(502, 1)
    )

    assert plan.candidates
    assert all(
        not isinstance(candidate.action, ConnectPortsAction)
        or candidate.action.input_port != 2
        for candidate in plan.candidates
    )
    assert isinstance(plan.candidates[0].action, PlaceGateAction)
    assert plan.candidates[0].action.gate == "OR"


def test_majority_finishes_missing_pairwise_and_before_placing_or() -> None:
    snapshot = real_502_pairwise_and_snapshot()
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

    plan = plan_circuit_actions(incomplete_snapshot, get_level_logic_spec(502, 1))

    assert plan.candidates
    action = plan.candidates[0].action
    assert isinstance(action, ConnectPortsAction)
    assert action.output_port == 29
    assert action.input_port == 41


def test_majority_disconnects_redundant_internal_signal_before_adding_gate() -> None:
    snapshot = real_502_pairwise_and_snapshot()
    wrong_snapshot = snapshot.model_copy(
        update={
            "board": snapshot.board.model_copy(
                update={
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
                    ]
                }
            )
        }
    )

    plan = plan_circuit_actions(wrong_snapshot, get_level_logic_spec(502, 1))

    assert plan.candidates
    action = plan.candidates[0].action
    assert isinstance(action, DisconnectPortsAction)
    assert action.output_port == 16
    assert action.input_port == 41
    assert any("目标" in fact for fact in plan.candidates[0].child_facts)


def test_xor_level_accepts_nand_only_partial_solution() -> None:
    plan = plan_circuit_actions(
        nand_only_xor_partial_snapshot(), get_level_logic_spec(301, 1)
    )

    assert plan.candidates
    assert all(
        not isinstance(candidate.action, PlaceGateAction)
        or candidate.action.gate == "NAND"
        for candidate in plan.candidates
    )


def test_planner_disconnects_blocking_edge_even_when_gate_feeds_downstream() -> None:
    plan = plan_circuit_actions(
        downstream_wrong_branch_snapshot(), get_level_logic_spec(201, 1)
    )

    assert plan.candidates
    action = plan.candidates[0].action
    assert isinstance(action, DisconnectPortsAction)
    assert action.output_port == 4
    assert action.input_port == 13


def test_teaching_priority_orders_equally_safe_gate_candidates() -> None:
    expected = {301: "OR", 403: "XOR", 503: "XOR", 602: "NOT"}

    for level_id, gate in expected.items():
        case = next(item for item in LEVEL_EVAL_CASES if item.level_id == level_id)
        plan = plan_circuit_actions(
            build_circuit_coach_v2(case, "placed-io"),
            get_level_logic_spec(level_id, 1),
        )

        assert plan.candidates
        action = plan.candidates[0].action
        assert isinstance(action, PlaceGateAction)
        assert action.gate == gate


def test_planner_finishes_existing_gate_before_placing_new_gate() -> None:
    plan = plan_circuit_actions(
        partially_connected_or_snapshot(), THREE_INPUT_OR_SPEC
    )

    assert isinstance(plan.candidates[0].action, ConnectPortsAction)
    assert plan.candidates[0].action.input_port == 18


def test_multi_output_plan_preserves_already_correct_sum_output() -> None:
    plan = plan_circuit_actions(
        full_adder_snapshot_with_correct_sum_only(), get_level_logic_spec(503, 1)
    )

    assert plan.preserved_output_indexes == frozenset({0})
    assert all(0 not in candidate.invalidated_output_indexes for candidate in plan.candidates)


def test_cycle_generates_disconnect_candidate_when_no_safe_build_step_exists() -> None:
    snapshot = _snapshot(
        level_id=201,
        unlocked_gates=["INPUT", "OUTPUT", "AND", "OR"],
        slots=[
            [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
            [1, 0, 1, "present", "INPUT", [[4, "right", "output"]]],
            [2, 0, 2, "present", "OUTPUT", [[8, "left", "input"]]],
            _binary_gate_slot(3, 0, 3, "AND", 14, 12, 13),
            _binary_gate_slot(4, 1, 0, "OR", 18, 16, 17),
        ],
        edges=[
            [0, 13, "valid"],
            [4, 17, "valid"],
            [14, 16, "valid"],
            [18, 12, "valid"],
        ],
    )

    plan = plan_circuit_actions(snapshot, get_level_logic_spec(201, 1))

    assert plan.candidates
    assert all(
        isinstance(candidate.action, DisconnectPortsAction)
        for candidate in plan.candidates
    )


def test_502_actionable_setup_stays_within_default_search_budget() -> None:
    case = next(item for item in LEVEL_EVAL_CASES if item.level_id == 502)
    snapshot = build_circuit_coach_v2(case, "actionable-logic")

    plan = plan_circuit_actions(snapshot, get_level_logic_spec(502, 1))

    assert plan.degraded_reason is None
    assert plan.candidates
    assert plan.search_states <= 20000


def test_504_actionable_setup_ignores_unwired_noncanonical_gate_within_budget() -> None:
    case = next(item for item in LEVEL_EVAL_CASES if item.level_id == 503)
    snapshot = build_circuit_coach_v2(case, "actionable-logic")

    plan = plan_circuit_actions(snapshot, get_level_logic_spec(503, 1))

    assert plan.degraded_reason is None
    assert plan.candidates
    action = plan.candidates[0].action
    assert isinstance(action, PlaceGateAction)
    assert action.gate == "XOR"
    assert plan.search_states <= 20000


def test_504_partially_wired_noncanonical_gate_does_not_exhaust_budget() -> None:
    case = next(item for item in LEVEL_EVAL_CASES if item.level_id == 503)
    snapshot = build_circuit_coach_v2(case, "actionable-logic")
    snapshot = snapshot.model_copy(
        update={
            "board": snapshot.board.model_copy(
                update={
                    "edges": [
                        CircuitCoachV2Edge(port_a=0, port_b=49, status="valid")
                    ]
                }
            )
        }
    )

    plan = plan_circuit_actions(snapshot, get_level_logic_spec(503, 1))

    assert plan.degraded_reason is None
    assert plan.candidates
    action = plan.candidates[0].action
    assert isinstance(action, PlaceGateAction)
    assert action.gate == "XOR"
    assert plan.elapsed_ms < 25
