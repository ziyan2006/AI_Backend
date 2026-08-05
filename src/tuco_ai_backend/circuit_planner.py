from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter

from tuco_ai_backend.circuit_graph import CircuitGraph, build_circuit_graph
from tuco_ai_backend.circuit_simulator import apply_gate_signature, simulate
from tuco_ai_backend.level_logic import LevelLogicSpec
from tuco_ai_backend.models import CircuitCoachV2Snapshot

SUPPORTED_GATES = frozenset({"NOT", "AND", "OR", "NAND", "NOR", "XOR", "XNOR"})


@dataclass(frozen=True)
class ConnectPortsAction:
    output_port: int
    input_port: int


@dataclass(frozen=True)
class PlaceGateAction:
    slot: int
    gate: str


@dataclass(frozen=True)
class DisconnectPortsAction:
    output_port: int
    input_port: int


@dataclass(frozen=True)
class PlannedCandidate:
    candidate_id: str
    topology_revision: int
    action: ConnectPortsAction | PlaceGateAction | DisconnectPortsAction
    score: tuple[int, ...]
    child_facts: tuple[str, ...]
    invalidated_output_indexes: frozenset[int]


@dataclass(frozen=True)
class CircuitPlan:
    candidates: tuple[PlannedCandidate, ...]
    preserved_output_indexes: frozenset[int]
    search_states: int
    elapsed_ms: float
    degraded_reason: str | None = None


@dataclass(frozen=True)
class _SignatureRoute:
    cost: int
    first_gates: frozenset[str]


@dataclass(frozen=True)
class _SynthesisResult:
    routes: dict[int, _SignatureRoute]
    search_states: int
    budget_exceeded: bool


def _synthesize_signatures(
    initial_signatures: set[int],
    gates: tuple[str, ...],
    *,
    target_signatures: frozenset[int],
    mask: int,
    deadline: float,
    state_budget: int,
) -> _SynthesisResult:
    routes = {signature: _SignatureRoute(0, frozenset()) for signature in initial_signatures}
    search_states = 0
    if target_signatures.issubset(routes):
        return _SynthesisResult(routes, search_states, False)
    changed = True
    while changed:
        changed = False
        signatures = tuple(routes)
        for gate in gates:
            operand_pairs = (
                ((signature,) for signature in signatures)
                if gate == "NOT"
                else (
                    (first, second)
                    for first_index, first in enumerate(signatures)
                    for second in signatures[first_index:]
                )
            )
            for operands in operand_pairs:
                search_states += 1
                if search_states > state_budget or perf_counter() >= deadline:
                    return _SynthesisResult(routes, search_states, True)
                result = apply_gate_signature(gate, operands, mask=mask)
                operand_routes = tuple(routes[operand] for operand in operands)
                cost = 1 + sum(route.cost for route in operand_routes)
                derived_first_gates = frozenset(
                    first_gate
                    for route in operand_routes
                    for first_gate in route.first_gates
                )
                first_gates = derived_first_gates or frozenset({gate})
                previous = routes.get(result)
                if previous is None or cost < previous.cost:
                    routes[result] = _SignatureRoute(cost, first_gates)
                    changed = True
                elif cost == previous.cost and not first_gates.issubset(previous.first_gates):
                    routes[result] = _SignatureRoute(
                        cost, previous.first_gates | first_gates
                    )
                    changed = True
        if target_signatures.issubset(routes):
            return _SynthesisResult(routes, search_states, False)
    return _SynthesisResult(routes, search_states, False)


def _available_signal_ports(
    graph: CircuitGraph, signal_by_output_port: dict[int, int]
) -> dict[int, tuple[int, ...]]:
    ports_by_signature: dict[int, list[int]] = {}
    for output_port, signature in sorted(signal_by_output_port.items()):
        if graph.ports_by_id[output_port].role != "output":
            continue
        ports_by_signature.setdefault(signature, []).append(output_port)
    return {
        signature: tuple(ports) for signature, ports in ports_by_signature.items()
    }


def _target_output_inputs(
    graph: CircuitGraph, spec: LevelLogicSpec
) -> dict[int, int]:
    output_slots = sorted(
        (slot for slot in graph.slots_by_id.values() if slot.gate == "OUTPUT"),
        key=lambda slot: slot.slot_id,
    )
    return {
        output_index: slot.input_ports[0]
        for output_index, slot in enumerate(output_slots[: spec.output_count])
        if slot.input_ports
    }


def _signature_advances_target(
    signature: int,
    available_signatures: set[int],
    gates: tuple[str, ...],
    target_signatures: frozenset[int],
    *,
    mask: int,
) -> bool:
    if signature in target_signatures:
        return True
    if "NOT" in gates and apply_gate_signature("NOT", (signature,), mask=mask) in target_signatures:
        return True
    for gate in gates:
        if gate == "NOT":
            continue
        for other_signature in available_signatures:
            if (
                apply_gate_signature(gate, (signature, other_signature), mask=mask)
                in target_signatures
            ):
                return True
    return False


def _candidate(
    snapshot: CircuitCoachV2Snapshot,
    action: ConnectPortsAction | PlaceGateAction | DisconnectPortsAction,
    score: tuple[int, ...],
    *facts: str,
) -> PlannedCandidate:
    return PlannedCandidate(
        candidate_id="",
        topology_revision=snapshot.board.topology_revision,
        action=action,
        score=score,
        child_facts=tuple(facts),
        invalidated_output_indexes=frozenset(),
    )


def plan_circuit_actions(
    snapshot: CircuitCoachV2Snapshot,
    spec: LevelLogicSpec,
    *,
    time_budget_ms: float = 50.0,
    state_budget: int = 20000,
) -> CircuitPlan:
    started = perf_counter()
    deadline = started + time_budget_ms / 1000.0
    graph = build_circuit_graph(snapshot)
    simulation = simulate(graph, spec)
    preserved = frozenset(
        state.output_index for state in simulation.output_states if state.status == "correct"
    )
    present_input_count = sum(
        slot.gate == "INPUT" and slot.state == "present"
        for slot in graph.slots_by_id.values()
    )
    present_output_count = sum(
        slot.gate == "OUTPUT" and slot.state == "present"
        for slot in graph.slots_by_id.values()
    )
    missing_gate = None
    if present_input_count < spec.input_count:
        missing_gate = "INPUT"
    elif present_output_count < spec.output_count:
        missing_gate = "OUTPUT"
    if missing_gate is not None:
        empty_slot = next(
            (
                slot.slot_id
                for slot in sorted(graph.slots_by_id.values(), key=lambda item: item.slot_id)
                if slot.state == "empty"
            ),
            None,
        )
        candidates = ()
        if empty_slot is not None:
            candidate = _candidate(
                snapshot,
                PlaceGateAction(empty_slot, missing_gate),
                (0, empty_slot),
                f"先补齐一块{missing_gate}积木，才能继续判断电路。",
            )
            candidates = (
                replace(
                    candidate,
                    candidate_id=f"rev{snapshot.board.topology_revision}-action-1",
                ),
            )
        return CircuitPlan(
            candidates=candidates,
            preserved_output_indexes=preserved,
            search_states=0,
            elapsed_ms=(perf_counter() - started) * 1000.0,
        )
    remaining_states = tuple(
        state for state in simulation.output_states if state.output_index not in preserved
    )
    available_ports = _available_signal_ports(graph, simulation.signal_by_output_port)
    available_signatures = set(available_ports)
    unlocked_gates = tuple(
        sorted(
            {
                gate.upper()
                for gate in snapshot.unlocked_gates
                if gate.upper() in SUPPORTED_GATES
            }
        )
    )
    row_count = 1 << spec.input_count
    mask = (1 << row_count) - 1
    synthesis = _synthesize_signatures(
        available_signatures,
        unlocked_gates,
        target_signatures=frozenset(
            state.target_signature for state in remaining_states
        ),
        mask=mask,
        deadline=deadline,
        state_budget=state_budget,
    )
    search_states = synthesis.search_states
    if synthesis.budget_exceeded:
        return CircuitPlan(
            candidates=(),
            preserved_output_indexes=preserved,
            search_states=search_states,
            elapsed_ms=(perf_counter() - started) * 1000.0,
            degraded_reason="search_budget_exceeded",
        )

    candidates: list[PlannedCandidate] = []
    output_inputs = _target_output_inputs(graph, spec)
    for state in remaining_states:
        input_port = output_inputs.get(state.output_index)
        if input_port is None or input_port in graph.source_by_input:
            continue
        for output_port in available_ports.get(state.target_signature, ()):
            if graph.ports_by_id[output_port].slot_id == graph.ports_by_id[input_port].slot_id:
                continue
            candidates.append(
                _candidate(
                    snapshot,
                    ConnectPortsAction(output_port, input_port),
                    (0, state.output_index, output_port, input_port),
                    "这个信号已经符合目标输出，可以直接接过去。",
                )
            )

    target_signatures = frozenset(
        state.target_signature for state in remaining_states
    )
    for slot in sorted(graph.slots_by_id.values(), key=lambda item: item.slot_id):
        if slot.gate not in SUPPORTED_GATES or slot.slot_id in graph.blocked_slots:
            continue
        missing_inputs = [
            input_port
            for input_port in slot.input_ports
            if input_port not in graph.source_by_input
        ]
        expected_inputs = 1 if slot.gate == "NOT" else 2
        if (
            len(slot.input_ports) != expected_inputs
            or not missing_inputs
            or len(missing_inputs) > 2
        ):
            continue
        known_signatures = [
            simulation.signal_by_output_port.get(graph.source_by_input[input_port])
            for input_port in slot.input_ports
            if input_port in graph.source_by_input
        ]
        if any(signature is None for signature in known_signatures):
            continue
        input_port = missing_inputs[0]
        existing_sources = {
            graph.source_by_input[connected_input]
            for connected_input in slot.input_ports
            if connected_input in graph.source_by_input
        }
        for source_signature, output_ports in available_ports.items():
            partial_operands = tuple(
                int(signature) for signature in (*known_signatures, source_signature)
            )
            completion_signatures = (
                (apply_gate_signature(slot.gate, partial_operands, mask=mask),)
                if len(partial_operands) == expected_inputs
                else tuple(
                    apply_gate_signature(
                        slot.gate,
                        (*partial_operands, completion_signature),
                        mask=mask,
                    )
                    for completion_signature in available_signatures
                )
            )
            useful = False
            for new_signature in completion_signatures:
                useful = _signature_advances_target(
                    new_signature,
                    available_signatures,
                    unlocked_gates,
                    target_signatures,
                    mask=mask,
                )
                if useful:
                    break
            if not useful:
                continue
            for output_port in output_ports:
                if output_port in existing_sources:
                    continue
                if graph.ports_by_id[output_port].slot_id == slot.slot_id:
                    continue
                candidates.append(
                    _candidate(
                        snapshot,
                        ConnectPortsAction(output_port, input_port),
                        (1, slot.slot_id, output_port, input_port),
                        f"补上这条线后，现有{slot.gate}积木就能继续产生有用结果。",
                    )
                )

    if not any(candidate.score[0] < 2 for candidate in candidates):
        empty_slot = next(
            (
                slot.slot_id
                for slot in sorted(graph.slots_by_id.values(), key=lambda item: item.slot_id)
                if slot.state == "empty"
            ),
            None,
        )
        if empty_slot is not None:
            first_gates = {
                gate
                for state in remaining_states
                if (route := synthesis.routes.get(state.target_signature)) is not None
                for gate in route.first_gates
            }
            for gate in sorted(first_gates):
                candidates.append(
                    _candidate(
                        snapshot,
                        PlaceGateAction(empty_slot, gate),
                        (2, empty_slot, sorted(SUPPORTED_GATES).index(gate)),
                        f"还需要用{gate}积木组合现有信号。",
                    )
                )

    for cycle in graph.cycles:
        cycle_slots = frozenset(cycle)
        for edge in graph.edges:
            output_slot = graph.ports_by_id[edge.output_port].slot_id
            input_slot = graph.ports_by_id[edge.input_port].slot_id
            if output_slot not in cycle_slots or input_slot not in cycle_slots:
                continue
            candidates.append(
                _candidate(
                    snapshot,
                    DisconnectPortsAction(edge.output_port, edge.input_port),
                    (3, output_slot, input_slot, edge.output_port, edge.input_port),
                    "这条线让信号绕成了环路，先拆掉它才能继续判断。",
                )
            )

    for invalid_edge in graph.invalid_edges:
        if invalid_edge.reason not in {"same_slot", "multiple_sources"}:
            continue
        port_a = graph.ports_by_id.get(invalid_edge.port_a)
        port_b = graph.ports_by_id.get(invalid_edge.port_b)
        if port_a is None or port_b is None:
            continue
        if port_a.role == "output" and port_b.role == "input":
            output_port, input_port = port_a.port_id, port_b.port_id
        elif port_b.role == "output" and port_a.role == "input":
            output_port, input_port = port_b.port_id, port_a.port_id
        else:
            continue
        candidates.append(
            _candidate(
                snapshot,
                DisconnectPortsAction(output_port, input_port),
                (3, 255, output_port, input_port),
                "这条线不符合安全连接规则，先拆掉再继续。",
            )
        )

    unique_candidates: dict[
        ConnectPortsAction | PlaceGateAction | DisconnectPortsAction, PlannedCandidate
    ] = {}
    for candidate in sorted(candidates, key=lambda item: item.score):
        unique_candidates.setdefault(candidate.action, candidate)
    selected = tuple(unique_candidates.values())[:3]
    identified = tuple(
        replace(
            candidate,
            candidate_id=(
                f"rev{snapshot.board.topology_revision}-action-{candidate_index}"
            ),
        )
        for candidate_index, candidate in enumerate(selected, start=1)
    )
    return CircuitPlan(
        candidates=identified,
        preserved_output_indexes=preserved,
        search_states=search_states,
        elapsed_ms=(perf_counter() - started) * 1000.0,
    )
