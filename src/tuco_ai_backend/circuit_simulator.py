from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tuco_ai_backend.circuit_graph import CircuitGraph
from tuco_ai_backend.level_logic import LevelLogicSpec

OutputStatus = Literal["correct", "wrong", "incomplete", "blocked"]


@dataclass(frozen=True)
class OutputSemanticState:
    output_index: int
    slot_id: int
    current_signature: int | None
    target_signature: int
    status: OutputStatus


@dataclass(frozen=True)
class SimulationResult:
    input_signature_by_slot: dict[int, int]
    signal_by_output_port: dict[int, int]
    output_states: tuple[OutputSemanticState, ...]
    unresolved_slots: frozenset[int]


def apply_gate_signature(gate: str, inputs: tuple[int, ...], *, mask: int) -> int:
    normalized_gate = gate.upper()
    if normalized_gate == "NOT" and len(inputs) == 1:
        return (~inputs[0]) & mask
    if len(inputs) != 2:
        raise ValueError(f"gate {normalized_gate} requires the expected input count")
    first, second = inputs
    if normalized_gate == "AND":
        return first & second
    if normalized_gate == "OR":
        return first | second
    if normalized_gate == "NAND":
        return (~(first & second)) & mask
    if normalized_gate == "NOR":
        return (~(first | second)) & mask
    if normalized_gate == "XOR":
        return first ^ second
    if normalized_gate == "XNOR":
        return (~(first ^ second)) & mask
    raise ValueError(f"unsupported gate: {gate}")


def _input_signature(input_index: int, row_count: int) -> int:
    return sum(
        1 << row_index
        for row_index in range(row_count)
        if row_index & (1 << input_index)
    )


def _target_signature(spec: LevelLogicSpec, output_index: int) -> int:
    return sum(
        1 << row_index
        for row_index, output_value in enumerate(spec.expected_outputs)
        if output_value & (1 << output_index)
    )


def simulate(graph: CircuitGraph, spec: LevelLogicSpec) -> SimulationResult:
    row_count = 1 << spec.input_count
    mask = (1 << row_count) - 1
    input_slots = sorted(
        (slot for slot in graph.slots_by_id.values() if slot.gate == "INPUT"),
        key=lambda slot: slot.slot_id,
    )
    input_signature_by_slot: dict[int, int] = {}
    signal_by_output_port: dict[int, int] = {}
    for input_index, slot in enumerate(input_slots):
        signature = _input_signature(input_index, row_count)
        input_signature_by_slot[slot.slot_id] = signature
        for output_port in slot.output_ports:
            signal_by_output_port[output_port] = signature

    unresolved_slots = set(graph.blocked_slots)
    pending = {
        slot.slot_id: slot
        for slot in graph.slots_by_id.values()
        if slot.state == "present" and slot.gate not in {None, "INPUT", "OUTPUT"}
    }
    while pending:
        progressed = False
        for slot_id in sorted(tuple(pending)):
            slot = pending[slot_id]
            if slot_id in graph.blocked_slots:
                del pending[slot_id]
                continue
            required_inputs = 1 if slot.gate == "NOT" else 2
            if len(slot.input_ports) != required_inputs:
                unresolved_slots.add(slot_id)
                del pending[slot_id]
                continue
            source_ports = tuple(graph.source_by_input.get(port) for port in slot.input_ports)
            if any(source_port is None for source_port in source_ports):
                unresolved_slots.add(slot_id)
                del pending[slot_id]
                continue
            if any(source_port not in signal_by_output_port for source_port in source_ports):
                continue
            inputs = tuple(signal_by_output_port[source_port] for source_port in source_ports)
            signature = apply_gate_signature(slot.gate, inputs, mask=mask)
            for output_port in slot.output_ports:
                signal_by_output_port[output_port] = signature
            del pending[slot_id]
            progressed = True
        if not progressed:
            unresolved_slots.update(pending)
            break

    output_slots = sorted(
        (slot for slot in graph.slots_by_id.values() if slot.gate == "OUTPUT"),
        key=lambda slot: slot.slot_id,
    )
    output_states: list[OutputSemanticState] = []
    for output_index, slot in enumerate(output_slots):
        target_signature = _target_signature(spec, output_index)
        input_port = slot.input_ports[0] if slot.input_ports else None
        source_port = graph.source_by_input.get(input_port) if input_port is not None else None
        current_signature = (
            signal_by_output_port.get(source_port) if source_port is not None else None
        )
        source_slot = (
            graph.ports_by_id[source_port].slot_id if source_port in graph.ports_by_id else None
        )
        if slot.slot_id in graph.blocked_slots or source_slot in graph.blocked_slots:
            status: OutputStatus = "blocked"
        elif current_signature is None:
            status = "incomplete"
        elif current_signature == target_signature:
            status = "correct"
        else:
            status = "wrong"
        output_states.append(
            OutputSemanticState(
                output_index=output_index,
                slot_id=slot.slot_id,
                current_signature=current_signature,
                target_signature=target_signature,
                status=status,
            )
        )

    return SimulationResult(
        input_signature_by_slot=input_signature_by_slot,
        signal_by_output_port=signal_by_output_port,
        output_states=tuple(output_states),
        unresolved_slots=frozenset(unresolved_slots),
    )
