from __future__ import annotations

from dataclasses import dataclass

from tuco_ai_backend.circuit_graph import CircuitEdge, build_circuit_graph
from tuco_ai_backend.circuit_planner import (
    CircuitPlan,
    DisconnectPortsAction,
    plan_circuit_actions,
)
from tuco_ai_backend.circuit_simulator import simulate
from tuco_ai_backend.level_logic import LevelLogicSpec
from tuco_ai_backend.models import CircuitCoachV2Snapshot

_GATE_NAMES = {
    "INPUT": "输入",
    "OUTPUT": "输出",
    "NOT": "非门",
    "AND": "与门",
    "OR": "或门",
    "NAND": "与非门",
    "NOR": "或非门",
    "XOR": "异或门",
    "XNOR": "同或门",
}


@dataclass(frozen=True)
class CircuitDiagnosis:
    facts: tuple[str, ...]
    connection_facts: tuple[str, ...]
    disconnect_edges: tuple[CircuitEdge, ...]


def _gate_name(gate: str | None) -> str:
    if gate is None:
        return "未知"
    return _GATE_NAMES.get(gate.upper(), gate)


def diagnose_circuit(
    snapshot: CircuitCoachV2Snapshot,
    spec: LevelLogicSpec,
    plan: CircuitPlan | None = None,
) -> CircuitDiagnosis:
    graph = build_circuit_graph(snapshot)
    simulation = simulate(graph, spec)
    facts: list[str] = []
    connection_facts: list[str] = []
    disconnect_edges: list[CircuitEdge] = []

    for invalid_edge in graph.invalid_edges:
        reason = {
            "unknown_port": "有一条线连接到了无法识别的光点",
            "same_slot": "有一条线接在了同一块积木内部",
            "direction": "有一条线没有从输出接到输入",
            "multiple_sources": "有一个输入同时接了两路信号",
        }.get(invalid_edge.reason, "有一条线的连接方式不正确")
        fact = f"{reason}，这条线需要先调整。"
        facts.append(fact)
        connection_facts.append(fact)
        if invalid_edge.reason in {"same_slot", "direction", "multiple_sources"}:
            port_a = graph.ports_by_id.get(invalid_edge.port_a)
            port_b = graph.ports_by_id.get(invalid_edge.port_b)
            if port_a is not None and port_b is not None:
                if port_a.role == "output" and port_b.role == "input":
                    edge = CircuitEdge(
                        output_port=port_a.port_id,
                        input_port=port_b.port_id,
                    )
                elif port_b.role == "output" and port_a.role == "input":
                    edge = CircuitEdge(
                        output_port=port_b.port_id,
                        input_port=port_a.port_id,
                    )
                else:
                    edge = CircuitEdge(
                        output_port=invalid_edge.port_a,
                        input_port=invalid_edge.port_b,
                    )
                if edge not in disconnect_edges:
                    disconnect_edges.append(edge)

    if graph.cycles:
        fact = "当前电路形成了信号绕回去的环路，需要先拆掉环路中的一条线。"
        facts.append(fact)
        connection_facts.append(fact)

    for output_state in simulation.output_states:
        if output_state.status != "wrong":
            continue
        output_slot = graph.slots_by_id.get(output_state.slot_id)
        if output_slot is None or not output_slot.input_ports:
            continue
        input_port = output_slot.input_ports[0]
        source_port = graph.source_by_input.get(input_port)
        source = graph.ports_by_id.get(source_port) if source_port is not None else None
        if source is None:
            continue
        source_slot = graph.slots_by_id.get(source.slot_id)
        if source_slot is None:
            continue
        edge = CircuitEdge(output_port=source_port, input_port=input_port)
        if edge not in disconnect_edges:
            disconnect_edges.append(edge)
        facts.append(
            f"最终输出现在直接来自一块{_gate_name(source_slot.gate)}积木，"
            "它只覆盖了部分输入情况，不能代表本关的完整结果；这条线需要先调整。"
        )

    if not disconnect_edges:
        semantic_plan = plan or plan_circuit_actions(snapshot, spec)
        for candidate in semantic_plan.candidates:
            if not isinstance(candidate.action, DisconnectPortsAction):
                continue
            if not candidate.score or candidate.score[0] != 1:
                continue
            edge = CircuitEdge(
                output_port=candidate.action.output_port,
                input_port=candidate.action.input_port,
            )
            disconnect_edges.append(edge)
            facts.extend(candidate.child_facts)
            connection_facts.extend(candidate.child_facts)
            break

    return CircuitDiagnosis(
        facts=tuple(dict.fromkeys(facts)),
        connection_facts=tuple(dict.fromkeys(connection_facts)),
        disconnect_edges=tuple(disconnect_edges),
    )
