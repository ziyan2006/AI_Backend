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
    help_seeking: bool
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
            "description": (
                "判断孩子是否在求助，并选择本轮响应模式。help_seeking 只表示用户是否想获得"
                "具体帮助；是否执行 act 还必须服从直接提示开关和安全候选约束。"
            ),
            "strict": True,
            "parameters": AssistantTurnDecision.model_json_schema(),
        },
    }
