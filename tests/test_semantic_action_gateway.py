from __future__ import annotations

from tuco_ai_backend.circuit_planner import (
    CircuitPlan,
    ConnectPortsAction,
    PlaceGateAction,
    PlannedCandidate,
)
from tuco_ai_backend.semantic_action_gateway import SemanticActionGateway


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
