from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PortNumber = Annotated[int, Field(ge=0, le=63)]


class HighlightPortsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ports: list[PortNumber] = Field(min_length=1, max_length=8)
    duration_ms: int = Field(default=20000, ge=500, le=60000)
    pattern: Literal["blink", "pulse"]
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("ports")
    @classmethod
    def unique_ports(cls, ports: list[int]) -> list[int]:
        return list(dict.fromkeys(ports))


def highlight_ports_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "highlight_ports",
            "description": (
                "在语音提示播完后点亮指定积木。玩家询问亮灯、位置或接线位置时必须调用；"
                "提到从一个积木接到另一个积木时，必须传入两块积木各四个端口。"
            ),
            "strict": True,
            "parameters": HighlightPortsArgs.model_json_schema(),
        },
    }


def available_tools() -> list[dict[str, Any]]:
    return [highlight_ports_tool()]
