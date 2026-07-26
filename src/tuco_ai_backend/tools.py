from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PortNumber = Annotated[int, Field(ge=0, le=63)]
CircuitCoachPortNumber = Annotated[int, Field(ge=0, le=255)]


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
                "每次只能传入一条接线的两个端口，顺序为输出端、输入端；不能传入整块积木的全部端口。"
            ),
            "strict": True,
            "parameters": HighlightPortsArgs.model_json_schema(),
        },
    }


def available_tools() -> list[dict[str, Any]]:
    return [highlight_ports_tool()]


class CircuitCoachHighlightPortsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_port: CircuitCoachPortNumber
    input_port: CircuitCoachPortNumber


class HighlightEmptySlotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: int = Field(ge=0, le=255)
    gate: str = Field(min_length=1, max_length=32)


def circuit_coach_highlight_ports_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "highlight_ports",
            "description": (
                "只高亮一条实际可连接的线：一个未连接的输出端到另一个槽位的未连接输入端。"
                "调用时正文必须为空。"
            ),
            "strict": True,
            "parameters": CircuitCoachHighlightPortsArgs.model_json_schema(),
        },
    }


def highlight_empty_slot_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "highlight_empty_slot",
            "description": (
                "当还不能直接接线且玩家明确索取下一步时，指出一个空槽位和当前已解锁、"
                "应放置的积木类型。调用时正文必须为空。"
            ),
            "strict": True,
            "parameters": HighlightEmptySlotArgs.model_json_schema(),
        },
    }


def available_circuit_coach_v2_tools() -> list[dict[str, Any]]:
    return [circuit_coach_highlight_ports_tool(), highlight_empty_slot_tool()]
