from __future__ import annotations

from tuco_ai_backend.circuit_graph import build_circuit_graph
from tuco_ai_backend.circuit_planner import (
    CircuitPlan,
    ConnectPortsAction,
    DisconnectPortsAction,
    PlaceGateAction,
    PlannedCandidate,
)
from tuco_ai_backend.models import CircuitCoachV2Snapshot, ToolCall
from tuco_ai_backend.tools import CircuitCoachHighlightPortsArgs, HighlightEmptySlotArgs


def map_candidate_to_device_tool(
    candidate: PlannedCandidate,
    snapshot: CircuitCoachV2Snapshot,
) -> ToolCall | None:
    if candidate.topology_revision != snapshot.board.topology_revision:
        return None

    action = candidate.action
    graph = build_circuit_graph(snapshot)
    if isinstance(action, ConnectPortsAction):
        output_port = graph.ports_by_id.get(action.output_port)
        input_port = graph.ports_by_id.get(action.input_port)
        if (
            output_port is None
            or input_port is None
            or output_port.role != "output"
            or input_port.role != "input"
            or output_port.slot_id == input_port.slot_id
            or action.output_port in graph.connected_ports
            or action.input_port in graph.connected_ports
        ):
            return None
        arguments = CircuitCoachHighlightPortsArgs(
            output_port=action.output_port,
            input_port=action.input_port,
            intent="connect",
        )
        name = "highlight_ports"
    elif isinstance(action, PlaceGateAction):
        slot = graph.slots_by_id.get(action.slot)
        unlocked_gates = {gate.upper() for gate in snapshot.unlocked_gates}
        if (
            slot is None
            or slot.state != "empty"
            or action.gate.upper() not in unlocked_gates
        ):
            return None
        arguments = HighlightEmptySlotArgs(slot=action.slot, gate=action.gate)
        name = "highlight_empty_slot"
    elif isinstance(action, DisconnectPortsAction):
        edge_exists = any(
            {edge.port_a, edge.port_b} == {action.output_port, action.input_port}
            for edge in snapshot.board.edges
        )
        if not edge_exists:
            return None
        arguments = CircuitCoachHighlightPortsArgs(
            output_port=action.output_port,
            input_port=action.input_port,
            intent="disconnect",
        )
        name = "highlight_ports"
    else:
        return None

    return ToolCall(
        call_id=candidate.candidate_id,
        name=name,
        arguments=arguments,
    )


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
