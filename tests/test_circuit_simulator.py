from __future__ import annotations

import pytest

from tuco_ai_backend.circuit_graph import build_circuit_graph
from tuco_ai_backend.circuit_simulator import apply_gate_signature, simulate
from tuco_ai_backend.level_logic import get_level_logic_spec
from tuco_ai_backend.models import CircuitCoachV2Snapshot


@pytest.mark.parametrize(
    ("gate", "inputs", "expected"),
    [
        ("NOT", (0b0011,), 0b1100),
        ("AND", (0b0011, 0b0101), 0b0001),
        ("OR", (0b0011, 0b0101), 0b0111),
        ("NAND", (0b0011, 0b0101), 0b1110),
        ("NOR", (0b0011, 0b0101), 0b1000),
        ("XOR", (0b0011, 0b0101), 0b0110),
        ("XNOR", (0b0011, 0b0101), 0b1001),
    ],
)
def test_gate_signature(gate: str, inputs: tuple[int, ...], expected: int) -> None:
    assert apply_gate_signature(gate, inputs, mask=0b1111) == expected


def _snapshot(
    slots: list[list[object]], edges: list[list[object]]
) -> CircuitCoachV2Snapshot:
    return CircuitCoachV2Snapshot.model_validate(
        {
            "schema": "tuco_circuit_v2",
            "level": {"id": 201, "rule_version": 1},
            "board": {"topology_revision": 1, "slots": slots, "edges": edges},
        }
    )


def snapshot_nand_then_not() -> CircuitCoachV2Snapshot:
    return _snapshot(
        [
            [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
            [1, 0, 1, "present", "INPUT", [[4, "right", "output"]]],
            [
                2,
                0,
                2,
                "present",
                "NAND",
                [[8, "left", "input"], [9, "up", "input"], [10, "right", "output"]],
            ],
            [
                3,
                0,
                3,
                "present",
                "NOT",
                [[12, "left", "input"], [13, "right", "output"]],
            ],
            [4, 1, 0, "present", "OUTPUT", [[16, "left", "input"]]],
        ],
        [[0, 8, "valid"], [4, 9, "valid"], [10, 12, "valid"], [13, 16, "valid"]],
    )


def snapshot_with_one_empty_and_input() -> CircuitCoachV2Snapshot:
    return _snapshot(
        [
            [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
            [
                4,
                1,
                0,
                "present",
                "AND",
                [[16, "right", "output"], [17, "left", "input"], [18, "up", "input"]],
            ],
            [5, 1, 1, "present", "OUTPUT", [[20, "left", "input"]]],
        ],
        [[0, 17, "valid"], [16, 20, "valid"]],
    )


def test_nand_then_not_is_equivalent_to_and() -> None:
    result = simulate(
        build_circuit_graph(snapshot_nand_then_not()),
        get_level_logic_spec(201, 1),
    )

    assert result.output_states[0].status == "correct"


def test_incomplete_gate_has_no_output_signature() -> None:
    result = simulate(
        build_circuit_graph(snapshot_with_one_empty_and_input()),
        get_level_logic_spec(201, 1),
    )

    assert result.signal_by_output_port.get(16) is None
    assert result.unresolved_slots == frozenset({4})
