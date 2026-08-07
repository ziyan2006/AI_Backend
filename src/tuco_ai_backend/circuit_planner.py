from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from itertools import combinations_with_replacement
from time import perf_counter

from tuco_ai_backend.circuit_graph import CircuitEdge, CircuitGraph, build_circuit_graph
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
    frontier = deque(routes)
    processed_operations: set[tuple[str, tuple[int, ...]]] = set()
    search_states = 0
    if target_signatures.issubset(routes):
        return _SynthesisResult(routes, search_states, False)
    while frontier:
        active_signature = frontier.popleft()
        signatures = tuple(routes)
        for gate in gates:
            operand_pairs = (
                ((active_signature,),)
                if gate == "NOT"
                else (
                    tuple(sorted((active_signature, other_signature)))
                    for other_signature in signatures
                )
            )
            for operands in operand_pairs:
                operation = (gate, operands)
                if operation in processed_operations:
                    continue
                processed_operations.add(operation)
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
                if previous is None:
                    routes[result] = _SignatureRoute(cost, first_gates)
                    frontier.append(result)
                    if target_signatures.issubset(routes):
                        return _SynthesisResult(routes, search_states, False)
                elif cost < previous.cost:
                    routes[result] = _SignatureRoute(cost, first_gates)
                elif cost == previous.cost and not first_gates.issubset(previous.first_gates):
                    routes[result] = _SignatureRoute(
                        cost, previous.first_gates | first_gates
                    )
    return _SynthesisResult(routes, search_states, False)


def _completion_cost(
    synthesis: _SynthesisResult, target_signatures: frozenset[int]
) -> int | None:
    target_routes = tuple(
        synthesis.routes.get(signature) for signature in target_signatures
    )
    if any(route is None for route in target_routes):
        return None
    return sum(route.cost for route in target_routes if route is not None)


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


def _graph_with_replaced_source(
    graph: CircuitGraph,
    *,
    input_port: int,
    replacement_output_port: int,
) -> CircuitGraph | None:
    replaced = False
    edges: list[CircuitEdge] = []
    for edge in graph.edges:
        if edge.input_port == input_port:
            edges.append(
                CircuitEdge(
                    output_port=replacement_output_port,
                    input_port=input_port,
                )
            )
            replaced = True
        else:
            edges.append(edge)
    if not replaced:
        return None
    source_by_input = dict(graph.source_by_input)
    source_by_input[input_port] = replacement_output_port
    connected_ports = frozenset(
        port
        for edge in edges
        for port in (edge.output_port, edge.input_port)
    )
    return replace(
        graph,
        edges=tuple(edges),
        source_by_input=source_by_input,
        connected_ports=connected_ports,
    )


def _cost_rank(cost: int | None) -> float:
    return float("inf") if cost is None else float(cost)


def _gate_priority(spec: LevelLogicSpec, gate: str) -> tuple[int, int]:
    try:
        teaching_index = spec.preferred_gate_order.index(gate)
    except ValueError:
        teaching_index = len(spec.preferred_gate_order)
    return teaching_index, sorted(SUPPORTED_GATES).index(gate)


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
    target_signatures = frozenset(
        state.target_signature for state in remaining_states
    )
    unlocked_gates = tuple(
        sorted(
            {
                gate.upper()
                for gate in snapshot.unlocked_gates
                if gate.upper() in SUPPORTED_GATES
            },
            key=lambda gate: _gate_priority(spec, gate),
        )
    )
    preferred_unlocked_gates = tuple(
        gate for gate in spec.preferred_gate_order if gate in unlocked_gates
    )
    planning_gates = preferred_unlocked_gates or unlocked_gates
    row_count = 1 << spec.input_count
    mask = (1 << row_count) - 1
    synthesis = _synthesize_signatures(
        available_signatures,
        planning_gates,
        target_signatures=target_signatures,
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

    baseline_completion_cost = _completion_cost(synthesis, target_signatures)
    completion_cost_cache: dict[frozenset[int], int | None] = {
        frozenset(available_signatures): baseline_completion_cost
    }
    analysis_budget_exceeded = False

    def completion_cost_for(signatures: set[int]) -> int | None:
        nonlocal analysis_budget_exceeded, search_states
        cache_key = frozenset(signatures)
        if cache_key in completion_cost_cache:
            return completion_cost_cache[cache_key]
        remaining_budget = state_budget - search_states
        if remaining_budget <= 0 or perf_counter() >= deadline:
            analysis_budget_exceeded = True
            return None
        result = _synthesize_signatures(
            set(signatures),
            planning_gates,
            target_signatures=target_signatures,
            mask=mask,
            deadline=deadline,
            state_budget=remaining_budget,
        )
        search_states += result.search_states
        if result.budget_exceeded:
            analysis_budget_exceeded = True
            return None
        cost = _completion_cost(result, target_signatures)
        completion_cost_cache[cache_key] = cost
        return cost

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
        if slot.gate not in planning_gates:
            continue
        known_signatures = [
            simulation.signal_by_output_port.get(graph.source_by_input[input_port])
            for input_port in slot.input_ports
            if input_port in graph.source_by_input
        ]
        if any(signature is None for signature in known_signatures):
            continue
        input_port = missing_inputs[0]
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
                candidate_cost = completion_cost_for(
                    available_signatures | {new_signature}
                )
                useful = _cost_rank(candidate_cost) < _cost_rank(
                    baseline_completion_cost
                )
                if useful:
                    break
            if not useful:
                continue
            for output_port in output_ports:
                if output_port in graph.connected_ports:
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
        for slot in sorted(
            graph.slots_by_id.values(),
            key=lambda item: (
                any(port in graph.connected_ports for port in item.output_ports),
                item.slot_id,
            ),
        ):
            if (
                slot.gate not in SUPPORTED_GATES
                or slot.slot_id in graph.blocked_slots
                or not slot.output_ports
            ):
                continue
            expected_inputs = 1 if slot.gate == "NOT" else 2
            if len(slot.input_ports) != expected_inputs:
                continue
            source_ports = tuple(
                graph.source_by_input.get(input_port) for input_port in slot.input_ports
            )
            if any(source_port is None for source_port in source_ports):
                continue
            if any(
                source_port not in simulation.signal_by_output_port
                for source_port in source_ports
                if source_port is not None
            ):
                continue
            operand_signatures = tuple(
                simulation.signal_by_output_port[source_port]
                for source_port in source_ports
                if source_port is not None
            )
            has_downstream_connections = any(
                port in graph.connected_ports for port in slot.output_ports
            )
            current_slot_cost: int | None = None
            base_signatures: set[int] = set()
            if not has_downstream_connections:
                slot_output_ports = frozenset(slot.output_ports)
                base_signatures = {
                    signature
                    for signature, ports in available_ports.items()
                    if any(port not in slot_output_ports for port in ports)
                }
                current_output_signature = apply_gate_signature(
                    slot.gate,
                    operand_signatures,
                    mask=mask,
                )
                without_slot_cost = completion_cost_for(base_signatures)
                current_slot_cost = completion_cost_for(
                    base_signatures | {current_output_signature}
                )
                if _cost_rank(current_slot_cost) < _cost_rank(without_slot_cost):
                    continue
            blocking_edge: CircuitEdge | None = None
            for input_index, input_port in enumerate(slot.input_ports):
                current_source_port = source_ports[input_index]
                if current_source_port is None:
                    continue
                for replacement_port, replacement_signature in sorted(
                    simulation.signal_by_output_port.items()
                ):
                    replacement = graph.ports_by_id.get(replacement_port)
                    if (
                        replacement is None
                        or replacement.role != "output"
                        or replacement.slot_id == slot.slot_id
                        or replacement_port == current_source_port
                        or replacement_port in graph.connected_ports
                    ):
                        continue
                    if has_downstream_connections:
                        replacement_graph = _graph_with_replaced_source(
                            graph,
                            input_port=input_port,
                            replacement_output_port=replacement_port,
                        )
                        if replacement_graph is None:
                            continue
                        replacement_simulation = simulate(replacement_graph, spec)
                        replacement_states = {
                            state.output_index: state
                            for state in replacement_simulation.output_states
                        }
                        if any(
                            replacement_states.get(output_index) is None
                            or replacement_states[output_index].status != "correct"
                            for output_index in preserved
                        ):
                            continue
                        alternative_cost = completion_cost_for(
                            set(replacement_simulation.signal_by_output_port.values())
                        )
                        if _cost_rank(alternative_cost) >= _cost_rank(
                            baseline_completion_cost
                        ):
                            continue
                    else:
                        alternative_operands = list(operand_signatures)
                        alternative_operands[input_index] = replacement_signature
                        alternative_signature = apply_gate_signature(
                            slot.gate,
                            tuple(alternative_operands),
                            mask=mask,
                        )
                        alternative_cost = completion_cost_for(
                            base_signatures | {alternative_signature}
                        )
                        if _cost_rank(alternative_cost) >= _cost_rank(
                            current_slot_cost
                        ):
                            continue
                    blocking_edge = CircuitEdge(
                        output_port=current_source_port,
                        input_port=input_port,
                    )
                    break
                if blocking_edge is not None:
                    break
            if blocking_edge is None:
                continue
            candidates.append(
                _candidate(
                    snapshot,
                    DisconnectPortsAction(
                        blocking_edge.output_port,
                        blocking_edge.input_port,
                    ),
                    (
                        1,
                        slot.slot_id,
                        0,
                        blocking_edge.output_port,
                        blocking_edge.input_port,
                    ),
                    (
                        "这条线让后续链路产生的结果偏离目标；换成另一条现有信号后能更接近目标，所以先拆掉它。"
                        if has_downstream_connections
                        else (
                            "这条线占用了现有逻辑积木的输入端，但产生的中间信号"
                            "不能帮助缩短到目标的完成路线；先拆掉它，再接入更合适的信号。"
                        )
                    ),
                )
            )
            break

    if analysis_budget_exceeded and not candidates:
        return CircuitPlan(
            candidates=(),
            preserved_output_indexes=preserved,
            search_states=search_states,
            elapsed_ms=(perf_counter() - started) * 1000.0,
            degraded_reason="search_budget_exceeded",
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
            for gate in spec.preferred_gate_order:
                if gate not in planning_gates or gate in first_gates:
                    continue
                operand_sets = (
                    ((signature,) for signature in available_signatures)
                    if gate == "NOT"
                    else combinations_with_replacement(available_signatures, 2)
                )
                for operands in operand_sets:
                    derived_signature = apply_gate_signature(
                        gate,
                        tuple(operands),
                        mask=mask,
                    )
                    candidate_cost = completion_cost_for(
                        available_signatures | {derived_signature}
                    )
                    if _cost_rank(candidate_cost) < _cost_rank(
                        baseline_completion_cost
                    ):
                        first_gates.add(gate)
                        break
            for gate in sorted(first_gates):
                candidates.append(
                    _candidate(
                        snapshot,
                        PlaceGateAction(empty_slot, gate),
                        (2, empty_slot, *_gate_priority(spec, gate)),
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
