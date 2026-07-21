from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PublicConfig(BaseModel):
    llm_base_url: str
    llm_model: str
    llm_api_key_configured: bool
    llm_timeout_seconds: float


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float | None = Field(default=None, gt=0, le=300)

    @field_validator("llm_base_url", "llm_model")
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
    topology_revision: int = Field(ge=0)
    slots: list[dict[str, Any]] = Field(default_factory=list)
    valid_links: list[dict[str, Any]] = Field(default_factory=list)
    invalid_links: list[dict[str, Any]] = Field(default_factory=list)
    scan: dict[str, Any] = Field(default_factory=dict)


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

