from __future__ import annotations

import pytest

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
