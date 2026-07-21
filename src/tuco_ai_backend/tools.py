from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PortNumber = Annotated[int, Field(ge=0, le=63)]


class HighlightPortsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ports: list[PortNumber] = Field(min_length=1, max_length=4)
    duration_ms: int = Field(ge=500, le=10000)
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
            "description": "让设备提示区域的指定端口闪烁或呼吸，用于指出接线位置。",
            "strict": True,
            "parameters": HighlightPortsArgs.model_json_schema(),
        },
    }


def available_tools() -> list[dict[str, Any]]:
    return [highlight_ports_tool()]
