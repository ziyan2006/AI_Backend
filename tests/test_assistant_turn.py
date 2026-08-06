import pytest
from pydantic import ValidationError

from tuco_ai_backend.assistant_turn import (
    AssistantTurnDecision,
    decide_circuit_turn_tool,
)


def test_act_requires_candidate_id() -> None:
    with pytest.raises(ValidationError):
        AssistantTurnDecision(mode="act", assistant_text="现在开始操作。")


def test_non_act_rejects_candidate_id() -> None:
    with pytest.raises(ValidationError):
        AssistantTurnDecision(
            mode="hint",
            assistant_text="先观察两个输入。",
            candidate_id="rev4-action-1",
        )


@pytest.mark.parametrize(
    "mode",
    ["chat", "goal", "hint", "explain", "diagnose", "clarify"],
)
def test_non_act_modes_accept_text_only(mode: str) -> None:
    decision = AssistantTurnDecision(mode=mode, assistant_text="我们先想一想。")

    assert decision.mode == mode
    assert decision.candidate_id is None


def test_candidate_id_must_match_topology_revision_format() -> None:
    with pytest.raises(ValidationError):
        AssistantTurnDecision(
            mode="act",
            assistant_text="现在亮起下一步。",
            candidate_id="action-1",
        )


def test_decide_circuit_turn_tool_uses_strict_schema() -> None:
    tool = decide_circuit_turn_tool()

    assert tool["type"] == "function"
    function = tool["function"]
    assert function["name"] == "decide_circuit_turn"
    assert function["strict"] is True
    assert function["parameters"]["additionalProperties"] is False
