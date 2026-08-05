from __future__ import annotations

import pytest

from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES
from tuco_ai_backend.evaluation_presets import load_conversation_presets


def test_502_guidance_quality_preset_contains_complete_six_turn_flow() -> None:
    loaded = load_conversation_presets(["502-guidance-quality"])

    assert len(loaded) == 1
    scenario = loaded[0].scenario
    assert scenario.name == "502-guidance-quality"
    assert scenario.level_id == 502
    assert len(scenario.turns) == 6
    assert all(len(turn.snapshot.board.slots) == 16 for turn in scenario.turns)
    assert not any(
        slot.state == "present" for slot in scenario.turns[0].snapshot.board.slots
    )
    assert scenario.turns[3].snapshot.board.edges[-1].port_a == 16
    assert scenario.turns[3].snapshot.board.edges[-1].port_b == 2
    assert any(
        slot.gate == "OR" and slot.state == "present"
        for slot in scenario.turns[5].snapshot.board.slots
    )


def test_unknown_conversation_preset_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown conversation preset"):
        load_conversation_presets(["missing-preset"])


def test_all_other_guidance_quality_preset_expands_to_every_other_level() -> None:
    loaded = load_conversation_presets(["all-other-guidance-quality"])

    expected_level_ids = {
        case.level_id for case in LEVEL_EVAL_CASES if case.level_id != 502
    }
    assert len(loaded) == len(expected_level_ids)
    assert {item.scenario.level_id for item in loaded} == expected_level_ids
    assert len({item.scenario.name for item in loaded}) == len(loaded)
    assert all(len(item.scenario.turns) == 5 for item in loaded)
    assert all(
        len(turn.snapshot.board.slots) == 16
        for item in loaded
        for turn in item.scenario.turns
    )

    for item in loaded:
        turns = item.scenario.turns
        assert turns[2].snapshot.board.topology_revision > turns[1].snapshot.board.topology_revision
        assert turns[3].snapshot.board.edges
        error_edge = turns[3].snapshot.board.edges[-1]
        if item.scenario.level_id == 101:
            assert [error_edge.port_a, error_edge.port_b] == [0, 1]
        else:
            assert [error_edge.port_a, error_edge.port_b] == [0, 48]
