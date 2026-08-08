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


def test_redundant_internal_edge_is_reported_before_more_gates_are_added() -> None:
    snapshot = load_conversation_presets(["502-guidance-quality"])[
        0
    ].scenario.turns[2].snapshot
    wrong_snapshot = snapshot.model_copy(
        update={
            "board": snapshot.board.model_copy(
                update={
                    "edges": [
                        edge
                        for edge in snapshot.board.edges
                        if {edge.port_a, edge.port_b} != {29, 41}
                    ]
                    + [CircuitCoachV2Edge(port_a=16, port_b=41, status="valid")]
                }
            )
        }
    )

    diagnosis = diagnose_circuit(wrong_snapshot, get_level_logic_spec(502, 1))

    assert diagnosis.disconnect_edges == (CircuitEdge(output_port=16, input_port=41),)
    assert any("目标" in fact and "先拆掉" in fact for fact in diagnosis.facts)
    assert diagnosis.connection_facts == ()
    assert diagnosis.disconnect_kinds == ("semantic_blocking",)


def test_mux_branch_swap_is_reported_before_correct_final_output_edge() -> None:
    snapshot = CircuitCoachV2Snapshot.model_validate(
        {
            "schema": "tuco_circuit_v2",
            "level": {
                "id": 601,
                "rule_version": 1,
                "goal": "用选择端决定输出 A 或 B",
                "inputs": "A B 选择",
                "outputs": "Y",
                "input_count": 3,
                "output_count": 1,
            },
            "unlocked_gates": ["INPUT", "OUTPUT", "NOT", "AND", "OR"],
            "board": {
                "topology_revision": 95,
                "slots": [
                    [
                        0,
                        0,
                        3,
                        "present",
                        "OUTPUT",
                        [
                            [0, "right", "unused"],
                            [1, "down", "unused"],
                            [2, "left", "input"],
                            [3, "up", "unused"],
                        ],
                    ],
                    [
                        3,
                        1,
                        2,
                        "present",
                        "OR",
                        [
                            [12, "left", "unused"],
                            [13, "up", "input"],
                            [14, "right", "output"],
                            [15, "down", "input"],
                        ],
                    ],
                    [
                        4,
                        0,
                        1,
                        "present",
                        "AND",
                        [
                            [16, "right", "output"],
                            [17, "down", "input"],
                            [18, "left", "unused"],
                            [19, "up", "input"],
                        ],
                    ],
                    [
                        5,
                        1,
                        1,
                        "present",
                        "AND",
                        [
                            [20, "left", "unused"],
                            [21, "up", "input"],
                            [22, "right", "output"],
                            [23, "down", "input"],
                        ],
                    ],
                    [
                        6,
                        0,
                        0,
                        "present",
                        "INPUT",
                        [
                            [24, "right", "output"],
                            [25, "down", "output"],
                            [26, "left", "output"],
                            [27, "up", "output"],
                        ],
                    ],
                    [
                        7,
                        1,
                        0,
                        "present",
                        "INPUT",
                        [
                            [28, "left", "output"],
                            [29, "up", "output"],
                            [30, "right", "output"],
                            [31, "down", "output"],
                        ],
                    ],
                    [
                        8,
                        2,
                        0,
                        "present",
                        "INPUT",
                        [
                            [32, "right", "output"],
                            [33, "down", "output"],
                            [34, "left", "output"],
                            [35, "up", "output"],
                        ],
                    ],
                    [
                        10,
                        2,
                        1,
                        "present",
                        "NOT",
                        [
                            [40, "right", "output"],
                            [41, "down", "unused"],
                            [42, "left", "input"],
                            [43, "up", "unused"],
                        ],
                    ],
                ],
                "edges": [
                    [2, 14, "valid"],
                    [13, 16, "valid"],
                    [15, 22, "valid"],
                    [17, 33, "valid"],
                    [19, 25, "valid"],
                    [21, 30, "valid"],
                    [23, 40, "valid"],
                    [32, 42, "valid"],
                ],
            },
        }
    )

    diagnosis = diagnose_circuit(snapshot, get_level_logic_spec(601, 1))

    assert diagnosis.disconnect_edges == (CircuitEdge(output_port=30, input_port=21),)
    assert diagnosis.disconnect_kinds == ("semantic_blocking",)
    assert CircuitEdge(output_port=14, input_port=2) not in diagnosis.disconnect_edges


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
