from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PublicConfig(BaseModel):
    llm_base_url: str
    llm_model: str
    llm_api_key_configured: bool
    llm_timeout_seconds: float
    volc_api_key_configured: bool
    volc_asr_resource_id: str
    volc_tts_resource_id: str
    volc_tts_voice_type: str


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    volc_api_key: str | None = None
    volc_asr_resource_id: str | None = None
    volc_tts_resource_id: str | None = None
    volc_tts_voice_type: str | None = None

    @field_validator(
        "llm_base_url",
        "llm_model",
        "volc_asr_resource_id",
        "volc_tts_resource_id",
        "volc_tts_voice_type",
    )
    @classmethod
    def strip_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class CircuitSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = Field(default=1, ge=1)
    play_active: bool = False
    generation: int = Field(default=0, ge=0)
    topology_revision: int = Field(ge=0)
    completed_ir_scans: int = Field(default=0, ge=0)
    completed_i2c_scans: int = Field(default=0, ge=0)
    level: LevelContext | None = None
    slots: list[dict[str, Any]] = Field(default_factory=list)
    port_roles: list[int] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    link_count: int = Field(default=0, ge=0)
    ignored_link_count: int = Field(default=0, ge=0)
    invalid_link_count: int = Field(default=0, ge=0)
    link_overflow: bool = False

    # Legacy browser snapshots remain accepted during the migration.
    valid_links: list[dict[str, Any]] = Field(default_factory=list)
    invalid_links: list[dict[str, Any]] = Field(default_factory=list)
    scan: dict[str, Any] = Field(default_factory=dict)


class LevelContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    level_id: int = Field(ge=1, le=9999)
    short_goal: str = Field(min_length=1, max_length=160)
    input_names: str = Field(default="", max_length=120)
    output_names: str = Field(default="", max_length=120)
    input_count: int = Field(ge=0, le=4)
    output_count: int = Field(ge=0, le=4)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    circuit: CircuitSnapshot


class ToolCall(BaseModel):
    call_id: str
    name: str
    arguments: Any


class DecisionResponse(BaseModel):
    assistant_text: str | None = None
    tool_call: ToolCall | None = None
    topology_revision: int


class CircuitCoachV2Port(BaseModel):
    port_id: int = Field(ge=0, le=255)
    side: str = Field(min_length=1, max_length=16)
    role: str = Field(min_length=1, max_length=16)

    @model_validator(mode="before")
    @classmethod
    def decode_compact_record(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        if len(value) != 3:
            raise ValueError("v2 port record must contain port_id, side, and role")
        return {"port_id": value[0], "side": value[1], "role": value[2]}


class CircuitCoachV2Slot(BaseModel):
    slot_id: int = Field(ge=0, le=255)
    row: int = Field(ge=0, le=255)
    column: int = Field(ge=0, le=255)
    state: Literal["empty", "unidentified", "present"]
    gate: str | int | None = None
    ports: list[CircuitCoachV2Port] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def decode_compact_record(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        if len(value) != 6:
            raise ValueError("v2 slot record must contain six fields")
        return {
            "slot_id": value[0],
            "row": value[1],
            "column": value[2],
            "state": value[3],
            "gate": value[4],
            "ports": value[5],
        }


class CircuitCoachV2Edge(BaseModel):
    port_a: int = Field(ge=0, le=255)
    port_b: int = Field(ge=0, le=255)
    status: str = Field(min_length=1, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def decode_compact_record(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        if len(value) != 3:
            raise ValueError("v2 edge record must contain port_a, port_b, and status")
        return {"port_a": value[0], "port_b": value[1], "status": value[2]}


class CircuitCoachV2Level(BaseModel):
    id: int = Field(ge=1, le=9999)
    goal: str = Field(default="", max_length=240)
    inputs: str = Field(default="", max_length=160)
    outputs: str = Field(default="", max_length=160)
    input_count: int = Field(default=0, ge=0, le=8)
    output_count: int = Field(default=0, ge=0, le=8)


class CircuitCoachV2Board(BaseModel):
    topology_revision: int = Field(ge=0)
    link_count: int = Field(default=0, ge=0)
    invalid_link_count: int = Field(default=0, ge=0)
    ignored_link_count: int = Field(default=0, ge=0)
    link_overflow: bool = False
    slots: list[CircuitCoachV2Slot] = Field(default_factory=list)
    edges: list[CircuitCoachV2Edge] = Field(default_factory=list)


class CircuitCoachV2Snapshot(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_name: Literal["tuco_circuit_v2"] = Field(alias="schema")
    level: CircuitCoachV2Level
    unlocked_gates: list[str] = Field(default_factory=list)
    gate_templates: list[list[Any]] = Field(default_factory=list)
    board: CircuitCoachV2Board


class CircuitCoachDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128)
    user_text: str = Field(min_length=1, max_length=2000)
    circuit_snapshot: CircuitCoachV2Snapshot
