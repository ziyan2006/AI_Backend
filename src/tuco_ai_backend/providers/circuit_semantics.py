from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tuco_ai_backend.models import CircuitCoachV2Slot, CircuitCoachV2Snapshot
from tuco_ai_backend.tools import CircuitCoachHighlightPortsArgs, HighlightEmptySlotArgs

SemanticToolName = Literal["highlight_ports", "highlight_empty_slot"]
PairTerm = frozenset[int]
TermSet = frozenset[PairTerm]


@dataclass(frozen=True)
class SemanticAction:
    tool_name: SemanticToolName
    arguments: CircuitCoachHighlightPortsArgs | HighlightEmptySlotArgs
    spoken_text: str
    instruction: str


def plan_semantic_next_action(circuit: CircuitCoachV2Snapshot) -> SemanticAction | None:
    if circuit.level.id != 502:
        return None

    return _plan_level_502(circuit)


def _plan_level_502(circuit: CircuitCoachV2Snapshot) -> SemanticAction | None:
    input_slots = sorted(
        slot.slot_id
        for slot in circuit.board.slots
        if slot.state == "present" and _gate_name(slot.gate) == "INPUT"
    )
    if len(input_slots) != 3:
        return None

    expected_pairs = frozenset(
        {
            frozenset((input_slots[0], input_slots[1])),
            frozenset((input_slots[0], input_slots[2])),
            frozenset((input_slots[1], input_slots[2])),
        }
    )
    connections = _valid_connections(circuit)
    connected_ports = {port for connection in connections for port in connection}
    pairwise_and_outputs = _pairwise_and_outputs(circuit, connections, expected_pairs)
    if frozenset(pairwise_and_outputs) != expected_pairs:
        return None

    or_slots = [
        slot
        for slot in circuit.board.slots
        if slot.state == "present" and _gate_name(slot.gate) == "OR"
    ]
    if not or_slots:
        return _highlight_empty_or_slot(circuit)

    resolved_or_terms = _resolved_or_terms_by_slot(
        circuit,
        connections,
        pairwise_and_outputs,
        expected_pairs,
    )
    source_terms = {
        output_port: frozenset((pair,))
        for pair, output_port in pairwise_and_outputs.items()
        if output_port not in connected_ports
    }
    source_terms.update(
        {
            output_port: terms
            for slot_id, terms in resolved_or_terms.items()
            if terms and terms < expected_pairs
            for output_port in _output_ports(_slot_by_id(circuit, slot_id))
            if output_port not in connected_ports
        }
    )

    for or_slot in sorted(or_slots, key=lambda slot: slot.slot_id):
        target_inputs = _input_ports(or_slot)
        connected_sources = {
            target: source for source, target in connections if target in target_inputs
        }
        empty_inputs = [port for port in target_inputs if port not in connected_sources]
        if not empty_inputs:
            continue
        used_terms = _terms_for_sources(
            connected_sources.values(),
            pairwise_and_outputs,
            resolved_or_terms,
            circuit,
        )
        candidates = sorted(
            (
                (source_port, terms)
                for source_port, terms in source_terms.items()
                if terms.isdisjoint(used_terms)
                and terms | used_terms <= expected_pairs
                and source_port not in connected_sources.values()
            ),
            key=lambda item: (-len(item[1]), item[0]),
        )
        if candidates:
            source_port, _ = candidates[0]
            target_port = empty_inputs[0]
            return _highlight_or_connection(source_port, target_port)

    final_or_output = next(
        (
            output_port
            for slot_id, terms in sorted(resolved_or_terms.items())
            if terms == expected_pairs
            for output_port in _output_ports(_slot_by_id(circuit, slot_id))
            if output_port not in connected_ports
        ),
        None,
    )
    output_target = next(
        (
            input_port
            for slot in sorted(circuit.board.slots, key=lambda item: item.slot_id)
            if slot.state == "present" and _gate_name(slot.gate) == "OUTPUT"
            for input_port in _input_ports(slot)
            if input_port not in connected_ports
        ),
        None,
    )
    if final_or_output is not None and output_target is not None:
        return SemanticAction(
            tool_name="highlight_ports",
            arguments=CircuitCoachHighlightPortsArgs(
                output_port=final_or_output,
                input_port=output_target,
            ),
            spoken_text="三路结果已经合在一起了。现在把亮起的两个光点连起来吧。",
            instruction=(
                "502 关语义校验（强制）：三组两两输入结果已完整合成为最终进位。"
                "本轮只能把最终或门输出接到输出积木；不得回接任何单独的与门结果。"
                "只能调用 highlight_ports，"
                f"output_port={final_or_output}，input_port={output_target}。"
            ),
        )

    if any(terms and terms < expected_pairs for terms in resolved_or_terms.values()):
        return _highlight_empty_or_slot(circuit)
    return None


def _highlight_empty_or_slot(circuit: CircuitCoachV2Snapshot) -> SemanticAction | None:
    if "OR" not in {_gate_name(gate) for gate in circuit.unlocked_gates}:
        return None
    empty_slot = next(
        (
            slot.slot_id
            for slot in sorted(circuit.board.slots, key=lambda item: item.slot_id)
            if slot.state == "empty"
        ),
        None,
    )
    if empty_slot is None:
        return None
    return SemanticAction(
        tool_name="highlight_empty_slot",
        arguments=HighlightEmptySlotArgs(slot=empty_slot, gate="OR"),
        spoken_text="三组两两一起为一的结果已经准备好了。下一步先放一块或门，把它们一步步合在一起。",
        instruction=(
            "502 关语义校验（强制）：已从真实连线识别出三组不同输入的两两与结果，"
            "它们还没有合成为完整进位。"
            "本轮只能摆放一块已解锁的 OR，绝不能把任意一块与门直接接到输出积木。"
            f"只能调用 highlight_empty_slot，slot={empty_slot}，gate=OR。"
        ),
    )


def _highlight_or_connection(output_port: int, input_port: int) -> SemanticAction:
    return SemanticAction(
        tool_name="highlight_ports",
        arguments=CircuitCoachHighlightPortsArgs(
            output_port=output_port,
            input_port=input_port,
        ),
        spoken_text="先把亮起的两个光点连起来，让这一组结果进入或门。",
        instruction=(
            "502 关语义校验（强制）：当前应把一组尚未合并的两两与结果接入或门。"
            "不得把单独的与门结果直接接到输出积木。"
            f"只能调用 highlight_ports，output_port={output_port}，input_port={input_port}。"
        ),
    )


def _pairwise_and_outputs(
    circuit: CircuitCoachV2Snapshot,
    connections: set[tuple[int, int]],
    expected_pairs: TermSet,
) -> dict[PairTerm, int]:
    source_by_target = {target: source for source, target in connections}
    input_source_slot = {
        output_port: slot.slot_id
        for slot in circuit.board.slots
        if slot.state == "present" and _gate_name(slot.gate) == "INPUT"
        for output_port in _output_ports(slot)
    }
    outputs: dict[PairTerm, int] = {}
    for slot in circuit.board.slots:
        if slot.state != "present" or _gate_name(slot.gate) != "AND":
            continue
        sources = [source_by_target.get(port) for port in _input_ports(slot)]
        if len(sources) != 2 or any(source is None for source in sources):
            continue
        source_slots = frozenset(input_source_slot.get(source) for source in sources)
        if None in source_slots or len(source_slots) != 2 or source_slots not in expected_pairs:
            continue
        output_ports = _output_ports(slot)
        if len(output_ports) == 1:
            outputs[source_slots] = output_ports[0]
    return outputs


def _resolved_or_terms_by_slot(
    circuit: CircuitCoachV2Snapshot,
    connections: set[tuple[int, int]],
    pairwise_and_outputs: dict[PairTerm, int],
    expected_pairs: TermSet,
) -> dict[int, TermSet]:
    source_by_target = {target: source for source, target in connections}
    pair_by_output = {output: frozenset((pair,)) for pair, output in pairwise_and_outputs.items()}
    or_slots = {
        slot.slot_id: slot
        for slot in circuit.board.slots
        if slot.state == "present" and _gate_name(slot.gate) == "OR"
    }
    resolved: dict[int, TermSet] = {}

    def resolve_source(source_port: int, visiting: set[int]) -> TermSet | None:
        if source_port in pair_by_output:
            return pair_by_output[source_port]
        source_slot = next(
            (
                slot_id
                for slot_id, slot in or_slots.items()
                if source_port in _output_ports(slot)
            ),
            None,
        )
        if source_slot is None or source_slot in visiting:
            return None
        return resolve_or(source_slot, visiting | {source_slot})

    def resolve_or(slot_id: int, visiting: set[int]) -> TermSet | None:
        if slot_id in resolved:
            return resolved[slot_id]
        slot = or_slots[slot_id]
        source_ports = [source_by_target.get(port) for port in _input_ports(slot)]
        if not source_ports or any(source is None for source in source_ports):
            return None
        parts = [resolve_source(source, visiting) for source in source_ports]
        if any(part is None for part in parts):
            return None
        terms = frozenset().union(*parts)
        if not terms or terms > expected_pairs or sum(len(part) for part in parts) != len(terms):
            return None
        resolved[slot_id] = terms
        return terms

    for slot_id in sorted(or_slots):
        terms = resolve_or(slot_id, {slot_id})
        if terms:
            resolved[slot_id] = terms
    return resolved


def _terms_for_sources(
    sources: object,
    pairwise_and_outputs: dict[PairTerm, int],
    resolved_or_terms: dict[int, TermSet],
    circuit: CircuitCoachV2Snapshot,
) -> TermSet:
    terms = frozenset()
    pair_by_output = {output: frozenset((pair,)) for pair, output in pairwise_and_outputs.items()}
    for source in sources:
        if source in pair_by_output:
            terms |= pair_by_output[source]
            continue
        slot = next(
            (
                item
                for item in circuit.board.slots
                if source in _output_ports(item) and item.slot_id in resolved_or_terms
            ),
            None,
        )
        if slot is not None:
            terms |= resolved_or_terms[slot.slot_id]
    return terms


def _valid_connections(circuit: CircuitCoachV2Snapshot) -> set[tuple[int, int]]:
    ports = {
        port.port_id: (slot.slot_id, port.role)
        for slot in circuit.board.slots
        if slot.state == "present"
        for port in slot.ports
    }
    connections: set[tuple[int, int]] = set()
    for edge in circuit.board.edges:
        if edge.status != "valid":
            continue
        first = ports.get(edge.port_a)
        second = ports.get(edge.port_b)
        if first is None or second is None:
            continue
        if first[1] == "output" and second[1] == "input":
            connections.add((edge.port_a, edge.port_b))
        elif second[1] == "output" and first[1] == "input":
            connections.add((edge.port_b, edge.port_a))
    return connections


def _slot_by_id(circuit: CircuitCoachV2Snapshot, slot_id: int) -> CircuitCoachV2Slot:
    return next(slot for slot in circuit.board.slots if slot.slot_id == slot_id)


def _input_ports(slot: CircuitCoachV2Slot) -> list[int]:
    return [port.port_id for port in slot.ports if port.role == "input"]


def _output_ports(slot: CircuitCoachV2Slot) -> list[int]:
    return [port.port_id for port in slot.ports if port.role == "output"]


def _gate_name(value: str | int | None) -> str | None:
    return value.upper() if isinstance(value, str) else None
