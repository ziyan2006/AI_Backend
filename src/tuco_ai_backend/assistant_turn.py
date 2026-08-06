from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AssistantTurnMode = Literal[
    "chat",
    "goal",
    "hint",
    "explain",
    "diagnose",
    "act",
    "clarify",
]


class AssistantTurnDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AssistantTurnMode
    assistant_text: str = Field(min_length=1, max_length=500)
    candidate_id: str | None = Field(default=None, pattern=r"^rev\d+-action-\d+$")

    @model_validator(mode="after")
    def validate_candidate_usage(self) -> "AssistantTurnDecision":
        if self.mode == "act" and self.candidate_id is None:
            raise ValueError("act mode requires candidate_id")
        if self.mode != "act" and self.candidate_id is not None:
            raise ValueError("non-act mode cannot include candidate_id")
        return self


def decide_circuit_turn_tool() -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "decide_circuit_turn",
            "description": "选择本轮响应模式；只有用户明确要求立即操作时才能选择 act。",
            "strict": True,
            "parameters": AssistantTurnDecision.model_json_schema(),
        },
    }
