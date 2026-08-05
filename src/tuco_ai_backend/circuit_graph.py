from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tuco_ai_backend.models import CircuitCoachV2Snapshot

PortRole = Literal["input", "output", "unused"]
SlotState = Literal["empty", "unidentified", "present"]
DiagnosticReason = Literal["unknown_port", "same_slot", "direction", "multiple_sources"]


@dataclass(frozen=True)
class CircuitEdge:
    output_port: int
    input_port: int


@dataclass(frozen=True)
class CircuitPort:
    port_id: int
    slot_id: int
    role: PortRole


@dataclass(frozen=True)
class CircuitSlot:
    slot_id: int
    gate: str | None
    state: SlotState
    input_ports: tuple[int, ...]
    output_ports: tuple[int, ...]


@dataclass(frozen=True)
class RawEdgeDiagnostic:
    port_a: int
    port_b: int
    reason: DiagnosticReason


@dataclass(frozen=True)
class CircuitGraph:
    slots_by_id: dict[int, CircuitSlot]
    ports_by_id: dict[int, CircuitPort]
    edges: tuple[CircuitEdge, ...]
    source_by_input: dict[int, int]
    connected_ports: frozenset[int]
    invalid_edges: tuple[RawEdgeDiagnostic, ...]
    cycles: tuple[tuple[int, ...], ...]
    blocked_slots: frozenset[int]


def _normalize_role(role: str) -> PortRole:
    normalized = role.lower()
    if normalized == "input":
        return "input"
    if normalized == "output":
        return "output"
    return "unused"


def _normalize_gate(gate: str | int | None) -> str | None:
    if isinstance(gate, str):
        return gate.upper()
    return None


def _find_cycles(
    slots_by_id: dict[int, CircuitSlot],
    ports_by_id: dict[int, CircuitPort],
    edges: tuple[CircuitEdge, ...],
) -> tuple[tuple[int, ...], ...]:
    adjacency = {slot_id: set() for slot_id in slots_by_id}
    for edge in edges:
        source_slot = ports_by_id[edge.output_port].slot_id
        target_slot = ports_by_id[edge.input_port].slot_id
        adjacency[source_slot].add(target_slot)

    next_index = 0
    indexes: dict[int, int] = {}
    low_links: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    cycles: list[tuple[int, ...]] = []

    def visit(slot_id: int) -> None:
        nonlocal next_index
        indexes[slot_id] = next_index
        low_links[slot_id] = next_index
        next_index += 1
        stack.append(slot_id)
        on_stack.add(slot_id)

        for target_slot in sorted(adjacency[slot_id]):
            if target_slot not in indexes:
                visit(target_slot)
                low_links[slot_id] = min(low_links[slot_id], low_links[target_slot])
            elif target_slot in on_stack:
                low_links[slot_id] = min(low_links[slot_id], indexes[target_slot])

        if low_links[slot_id] != indexes[slot_id]:
            return
        component: list[int] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == slot_id:
                break
        if len(component) > 1:
            cycles.append(tuple(sorted(component)))

    for slot_id in sorted(slots_by_id):
        if slot_id not in indexes:
            visit(slot_id)
    return tuple(sorted(cycles))


def build_circuit_graph(snapshot: CircuitCoachV2Snapshot) -> CircuitGraph:
    ports_by_id: dict[int, CircuitPort] = {}
    slots_by_id: dict[int, CircuitSlot] = {}

    for raw_slot in snapshot.board.slots:
        input_ports: list[int] = []
        output_ports: list[int] = []
        for raw_port in raw_slot.ports:
            role = _normalize_role(raw_port.role)
            ports_by_id[raw_port.port_id] = CircuitPort(
                port_id=raw_port.port_id,
                slot_id=raw_slot.slot_id,
                role=role,
            )
            if role == "input":
                input_ports.append(raw_port.port_id)
            elif role == "output":
                output_ports.append(raw_port.port_id)
        slots_by_id[raw_slot.slot_id] = CircuitSlot(
            slot_id=raw_slot.slot_id,
            gate=_normalize_gate(raw_slot.gate),
            state=raw_slot.state,
            input_ports=tuple(sorted(input_ports)),
            output_ports=tuple(sorted(output_ports)),
        )

    edges: list[CircuitEdge] = []
    source_by_input: dict[int, int] = {}
    connected_ports: set[int] = set()
    invalid_edges: list[RawEdgeDiagnostic] = []
    for raw_edge in snapshot.board.edges:
        port_a = ports_by_id.get(raw_edge.port_a)
        port_b = ports_by_id.get(raw_edge.port_b)
        if port_a is None or port_b is None:
            invalid_edges.append(
                RawEdgeDiagnostic(raw_edge.port_a, raw_edge.port_b, "unknown_port")
            )
            continue
        if port_a.slot_id == port_b.slot_id:
            invalid_edges.append(
                RawEdgeDiagnostic(raw_edge.port_a, raw_edge.port_b, "same_slot")
            )
            continue
        if port_a.role == "output" and port_b.role == "input":
            output_port, input_port = port_a.port_id, port_b.port_id
        elif port_b.role == "output" and port_a.role == "input":
            output_port, input_port = port_b.port_id, port_a.port_id
        else:
            invalid_edges.append(
                RawEdgeDiagnostic(raw_edge.port_a, raw_edge.port_b, "direction")
            )
            continue
        if input_port in source_by_input:
            invalid_edges.append(
                RawEdgeDiagnostic(raw_edge.port_a, raw_edge.port_b, "multiple_sources")
            )
            continue
        source_by_input[input_port] = output_port
        connected_ports.update((output_port, input_port))
        edges.append(CircuitEdge(output_port=output_port, input_port=input_port))

    normalized_edges = tuple(edges)
    cycles = _find_cycles(slots_by_id, ports_by_id, normalized_edges)
    blocked_slots = frozenset(slot_id for cycle in cycles for slot_id in cycle)
    return CircuitGraph(
        slots_by_id=slots_by_id,
        ports_by_id=ports_by_id,
        edges=normalized_edges,
        source_by_input=source_by_input,
        connected_ports=frozenset(connected_ports),
        invalid_edges=tuple(invalid_edges),
        cycles=cycles,
        blocked_slots=blocked_slots,
    )
