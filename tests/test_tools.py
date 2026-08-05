import pytest
from pydantic import ValidationError

from tuco_ai_backend.tools import (
    ChooseCircuitActionArgs,
    CircuitCoachHighlightPortsArgs,
    HighlightPortsArgs,
    choose_circuit_action_tool,
    highlight_ports_tool,
)


def test_highlight_ports_normalizes_duplicate_ports() -> None:
    args = HighlightPortsArgs(
        ports=[8, 21, 8],
        duration_ms=3000,
        pattern="pulse",
        reason="与门缺少输入",
    )

    assert args.ports == [8, 21]


def test_highlight_ports_defaults_to_twenty_seconds_and_supports_two_blocks() -> None:
    args = HighlightPortsArgs(
        ports=list(range(8)),
        pattern="pulse",
        reason="标记两块积木",
    )

    assert args.duration_ms == 20000
    assert args.ports == list(range(8))


@pytest.mark.parametrize(
    "payload",
    [
        {"ports": [-1], "duration_ms": 3000, "pattern": "pulse", "reason": "bad"},
        {"ports": [64], "duration_ms": 3000, "pattern": "pulse", "reason": "bad"},
        {
            "ports": [1, 2, 3, 4, 5, 6, 7, 8, 9],
            "duration_ms": 3000,
            "pattern": "pulse",
            "reason": "bad",
        },
        {"ports": [1], "duration_ms": 499, "pattern": "pulse", "reason": "bad"},
        {"ports": [1], "duration_ms": 3000, "pattern": "flash", "reason": "bad"},
    ],
)
def test_highlight_ports_rejects_invalid_arguments(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HighlightPortsArgs.model_validate(payload)


def test_tool_schema_is_strict_and_disallows_extra_fields() -> None:
    tool = highlight_ports_tool()

    assert tool["function"]["strict"] is True
    assert tool["function"]["parameters"]["additionalProperties"] is False

    with pytest.raises(ValidationError):
        HighlightPortsArgs.model_validate(
            {
                "ports": [8],
                "duration_ms": 3000,
                "pattern": "blink",
                "reason": "test",
                "unknown": True,
            }
        )


def test_choose_circuit_action_only_accepts_revision_candidate_ids() -> None:
    args = ChooseCircuitActionArgs(candidate_id="rev31-action-2")

    assert args.candidate_id == "rev31-action-2"
    with pytest.raises(ValidationError):
        ChooseCircuitActionArgs(candidate_id="invented-action")

    tool = choose_circuit_action_tool()
    assert tool["function"]["name"] == "choose_circuit_action"
    assert tool["function"]["strict"] is True


def test_highlight_ports_defaults_to_connect() -> None:
    args = CircuitCoachHighlightPortsArgs(output_port=16, input_port=2)

    assert args.intent == "connect"
