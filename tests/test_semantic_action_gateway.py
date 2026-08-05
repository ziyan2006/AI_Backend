from __future__ import annotations

from tuco_ai_backend.circuit_planner import (
    CircuitPlan,
    ConnectPortsAction,
    DisconnectPortsAction,
    PlaceGateAction,
    PlannedCandidate,
)
from tuco_ai_backend.models import CircuitCoachV2Snapshot
from tuco_ai_backend.semantic_action_gateway import (
    SemanticActionGateway,
    map_candidate_to_device_tool,
)


def _plan_for_revision(revision: int) -> CircuitPlan:
    return CircuitPlan(
        candidates=(
            PlannedCandidate(
                candidate_id=f"rev{revision}-action-1",
                topology_revision=revision,
                action=ConnectPortsAction(output_port=16, input_port=2),
                score=(0,),
                child_facts=("已有信号可以直接接到输出。",),
                invalidated_output_indexes=frozenset(),
            ),
            PlannedCandidate(
                candidate_id=f"rev{revision}-action-2",
                topology_revision=revision,
                action=PlaceGateAction(slot=1, gate="OR"),
                score=(1,),
                child_facts=("需要先放一块或门。",),
                invalidated_output_indexes=frozenset(),
            ),
        ),
        preserved_output_indexes=frozenset(),
        search_states=10,
        elapsed_ms=1.0,
    )


def test_unknown_candidate_id_is_rejected() -> None:
    gateway = SemanticActionGateway(_plan_for_revision(31))

    assert gateway.resolve("invented-action", topology_revision=31) is None


def test_candidate_is_bound_to_topology_revision() -> None:
    gateway = SemanticActionGateway(_plan_for_revision(31))

    assert gateway.resolve("rev31-action-1", topology_revision=32) is None


def test_known_candidate_is_resolved() -> None:
    gateway = SemanticActionGateway(_plan_for_revision(31))

    candidate = gateway.resolve("rev31-action-2", topology_revision=31)

    assert candidate is not None
    assert candidate.action == PlaceGateAction(slot=1, gate="OR")


def _disconnect_candidate() -> PlannedCandidate:
    return PlannedCandidate(
        candidate_id="rev31-action-1",
        topology_revision=31,
        action=DisconnectPortsAction(output_port=16, input_port=2),
        score=(3,),
        child_facts=("这条线形成了环路。",),
        invalidated_output_indexes=frozenset(),
    )


def _snapshot_with_edges(edges: list[list[object]]) -> CircuitCoachV2Snapshot:
    return CircuitCoachV2Snapshot.model_validate(
        {
            "schema": "tuco_circuit_v2",
            "level": {"id": 201, "rule_version": 1},
            "unlocked_gates": ["INPUT", "OUTPUT", "AND"],
            "board": {
                "topology_revision": 31,
                "slots": [
                    [0, 0, 0, "present", "OUTPUT", [[2, "left", "input"]]],
                    [4, 1, 0, "present", "AND", [[16, "right", "output"]]],
                ],
                "edges": edges,
            },
        }
    )


def test_disconnect_requires_existing_edge() -> None:
    result = map_candidate_to_device_tool(
        _disconnect_candidate(), _snapshot_with_edges([])
    )

    assert result is None


def test_disconnect_maps_existing_edge_to_disconnect_intent() -> None:
    result = map_candidate_to_device_tool(
        _disconnect_candidate(), _snapshot_with_edges([[16, 2, "valid"]])
    )

    assert result is not None
    assert result.name == "highlight_ports"
    assert result.arguments.intent == "disconnect"
