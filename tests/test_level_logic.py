from __future__ import annotations

import pytest

from tools.generate_firmware_level_rules import render_firmware_rules
from tuco_ai_backend.level_logic import get_level_logic_spec, load_level_logic_specs


def test_level_502_truth_table_matches_majority_function() -> None:
    spec = get_level_logic_spec(502, 1)

    assert spec.input_count == 3
    assert spec.output_count == 1
    assert spec.expected_outputs == (0, 0, 0, 1, 0, 1, 1, 1)


def test_level_503_is_the_full_adder_and_level_504_is_removed() -> None:
    spec = get_level_logic_spec(503, 1)

    assert spec.input_count == 3
    assert spec.output_count == 2
    assert spec.expected_outputs == (0, 1, 1, 2, 1, 2, 2, 3)
    with pytest.raises(KeyError):
        get_level_logic_spec(504, 1)


def test_level_specs_expose_teaching_gate_priority() -> None:
    assert get_level_logic_spec(301, 1).preferred_gate_order[0] == "OR"
    assert get_level_logic_spec(403, 1).preferred_gate_order[0] == "XOR"
    assert get_level_logic_spec(503, 1).preferred_gate_order[0] == "XOR"
    assert get_level_logic_spec(602, 1).preferred_gate_order[0] == "NOT"


def test_all_specs_have_complete_truth_tables() -> None:
    specs = load_level_logic_specs()

    assert len(specs) == 16
    for spec in specs.values():
        assert len(spec.input_labels) == spec.input_count
        assert len(spec.output_labels) == spec.output_count
        assert len(spec.expected_outputs) == 1 << spec.input_count
        assert all(0 <= value < 1 << spec.output_count for value in spec.expected_outputs)


def test_firmware_rule_generation_is_deterministic() -> None:
    first = render_firmware_rules(load_level_logic_specs().values())
    second = render_firmware_rules(load_level_logic_specs().values())

    assert first == second
    assert first.startswith("/* Generated from backend level logic specs. Do not edit. */\n")
    assert (
        "{502, 1, 3, 1, 8, {0, 0, 0, 1, 0, 1, 1, 1}, "
        '"至少两个输入亮时产生进位", "A B C", "进位"},'
    ) in first
    assert (
        "{503, 1, 3, 2, 8, {0, 1, 1, 2, 1, 2, 2, 3}, "
        '"完成三输入全加运算", "A B C", "和 进位"},'
    ) in first
    assert "{504," not in first
