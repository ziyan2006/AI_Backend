from __future__ import annotations

from tuco_ai_backend.circuit_graph import CircuitEdge, build_circuit_graph
from tuco_ai_backend.models import CircuitCoachV2Snapshot


def _snapshot(
    slots: list[list[object]], edges: list[list[object]]
) -> CircuitCoachV2Snapshot:
    return CircuitCoachV2Snapshot.model_validate(
        {
            "schema": "tuco_circuit_v2",
            "level": {"id": 101, "rule_version": 1},
            "board": {
                "topology_revision": 1,
                "slots": slots,
                "edges": edges,
            },
        }
    )


def snapshot_with_reversed_edge_record() -> CircuitCoachV2Snapshot:
    return _snapshot(
        [
            [4, 1, 0, "present", "OUTPUT", [[17, "left", "input"]]],
            [6, 1, 2, "present", "INPUT", [[24, "right", "output"]]],
        ],
        [[17, 24, "valid"]],
    )


def snapshot_with_two_gate_cycle() -> CircuitCoachV2Snapshot:
    return _snapshot(
        [
            [4, 1, 0, "present", "AND", [[16, "left", "input"], [17, "right", "output"]]],
            [5, 1, 1, "present", "OR", [[20, "left", "input"], [21, "right", "output"]]],
        ],
        [[17, 20, "valid"], [21, 16, "valid"]],
    )


def test_graph_orients_valid_edge_from_output_to_input() -> None:
    graph = build_circuit_graph(snapshot_with_reversed_edge_record())

    assert graph.edges == (CircuitEdge(output_port=24, input_port=17),)


def test_graph_reports_cycle_without_propagating_it() -> None:
    graph = build_circuit_graph(snapshot_with_two_gate_cycle())

    assert graph.cycles == ((4, 5),)
    assert graph.blocked_slots == frozenset({4, 5})


def test_graph_reports_invalid_edges_and_keeps_first_input_source() -> None:
    graph = build_circuit_graph(
        _snapshot(
            [
                [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
                [1, 0, 1, "present", "INPUT", [[4, "right", "output"]]],
                [2, 0, 2, "present", "AND", [[8, "left", "input"], [9, "right", "output"]]],
                [3, 0, 3, "present", "OUTPUT", [[12, "left", "input"]]],
            ],
            [
                [0, 8, "valid"],
                [4, 8, "valid"],
                [8, 12, "invalid_direction"],
                [8, 9, "invalid_same_slot"],
                [99, 12, "invalid_unknown"],
            ],
        )
    )

    assert graph.source_by_input == {8: 0}
    assert [diagnostic.reason for diagnostic in graph.invalid_edges] == [
        "multiple_sources",
        "direction",
        "same_slot",
        "unknown_port",
    ]
