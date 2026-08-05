from __future__ import annotations

from tuco_ai_backend.circuit_planner import (
    ConnectPortsAction,
    DisconnectPortsAction,
    PlaceGateAction,
    plan_circuit_actions,
)
from tuco_ai_backend.level_logic import get_level_logic_spec
from tuco_ai_backend.models import CircuitCoachV2Snapshot


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
        level_id=503,
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
        level_id=504,
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


def test_planner_finishes_existing_gate_before_placing_new_gate() -> None:
    plan = plan_circuit_actions(
        partially_connected_or_snapshot(), get_level_logic_spec(503, 1)
    )

    assert isinstance(plan.candidates[0].action, ConnectPortsAction)
    assert plan.candidates[0].action.input_port == 18


def test_multi_output_plan_preserves_already_correct_sum_output() -> None:
    plan = plan_circuit_actions(
        full_adder_snapshot_with_correct_sum_only(), get_level_logic_spec(504, 1)
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
