from __future__ import annotations

from tuco_ai_backend.circuit_diagnostics import diagnose_circuit
from tuco_ai_backend.circuit_graph import CircuitEdge
from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES, build_circuit_coach_v2
from tuco_ai_backend.evaluation_presets import load_conversation_presets
from tuco_ai_backend.level_logic import get_level_logic_spec
from tuco_ai_backend.models import (
    CircuitCoachV2Edge,
    CircuitCoachV2Port,
    CircuitCoachV2Snapshot,
)


def test_wrong_direct_output_edge_is_reported_as_disconnectable() -> None:
    wrong_snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[3].snapshot

    diagnosis = diagnose_circuit(wrong_snapshot, get_level_logic_spec(502, 1))

    assert diagnosis.disconnect_edges == (CircuitEdge(output_port=16, input_port=2),)
    assert any("与门" in fact and "最终输出" in fact for fact in diagnosis.facts)
    assert any("这条线" in fact for fact in diagnosis.facts)


def test_correct_direct_output_does_not_produce_disconnect_diagnosis() -> None:
    case = next(item for item in LEVEL_EVAL_CASES if item.level_id == 101)
    snapshot = build_circuit_coach_v2(case, "placed-io")
    payload = snapshot.model_dump(mode="json", by_alias=True)
    payload["board"]["topology_revision"] = 3
    payload["board"]["edges"] = [[0, 4, "valid"]]
    snapshot = CircuitCoachV2Snapshot.model_validate(payload)

    diagnosis = diagnose_circuit(snapshot, get_level_logic_spec(101, 1))

    assert diagnosis.disconnect_edges == ()
    assert not diagnosis.facts


def test_wrong_port_roles_are_reported_as_disconnectable_real_edge() -> None:
    case = next(item for item in LEVEL_EVAL_CASES if item.level_id == 403)
    snapshot = build_circuit_coach_v2(case, "placed-io")
    snapshot.board.topology_revision = 6
    snapshot.board.slots[12].state = "present"
    snapshot.board.slots[12].gate = "AND"
    snapshot.board.slots[12].ports = [
        CircuitCoachV2Port(port_id=48, side="right", role="output"),
        CircuitCoachV2Port(port_id=49, side="down", role="input"),
        CircuitCoachV2Port(port_id=51, side="up", role="input"),
    ]
    snapshot.board.edges = [
        CircuitCoachV2Edge(port_a=0, port_b=48, status="valid")
    ]

    diagnosis = diagnose_circuit(snapshot, get_level_logic_spec(403, 1))

    assert diagnosis.disconnect_edges == (CircuitEdge(output_port=0, input_port=48),)
    assert any("没有从输出接到输入" in fact for fact in diagnosis.connection_facts)
