from __future__ import annotations

from tuco_ai_backend.circuit_planner import CircuitPlan, PlannedCandidate


class SemanticActionGateway:
    def __init__(self, plan: CircuitPlan) -> None:
        self._candidates = {
            candidate.candidate_id: candidate for candidate in plan.candidates
        }

    def resolve(
        self, candidate_id: str, topology_revision: int
    ) -> PlannedCandidate | None:
        candidate = self._candidates.get(candidate_id)
        if candidate is None or candidate.topology_revision != topology_revision:
            return None
        return candidate
