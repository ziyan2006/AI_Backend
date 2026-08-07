from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tuco_ai_backend.models import CircuitCoachV2Snapshot


class ConversationScenarioTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_text: str = Field(min_length=1, max_length=2000)
    interaction_intent: Literal["auto", "act"] = "auto"
    direct_hint_requested: bool = False
    snapshot: CircuitCoachV2Snapshot
    note: str | None = Field(default=None, max_length=500)


class ConversationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["tuco_conversation_scenario_v1"] = Field(alias="schema")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    level_id: int = Field(ge=1, le=9999)
    tags: list[str] = Field(default_factory=list)
    turns: list[ConversationScenarioTurn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_turn_levels(self) -> Self:
        for turn in self.turns:
            if turn.snapshot.level.id != self.level_id:
                raise ValueError(
                    f"snapshot level {turn.snapshot.level.id} does not match "
                    f"scenario level {self.level_id}"
                )
        return self


@dataclass(frozen=True)
class LoadedConversationScenario:
    scenario: ConversationScenario
    source_path: Path
    warnings: tuple[str, ...] = ()


@dataclass
class ScenarioTurnEvaluationResult:
    turn_index: int
    trace_id: str
    user_text: str
    interaction_intent: Literal["auto", "act"]
    direct_hint_requested: bool
    note: str | None
    snapshot: dict[str, Any]
    history_before_turn: list[dict[str, str]]
    duration_ms: int
    assistant_text: str | None = None
    tool_call: dict[str, Any] | None = None
    topology_revision: int | None = None
    route_mode: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    error_stack: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error_type is None


@dataclass
class ScenarioEvaluationResult:
    session_id: str
    name: str
    description: str
    level_id: int
    tags: list[str]
    source_path: str
    warnings: list[str]
    duration_ms: int
    turns: list[ScenarioTurnEvaluationResult]

    @property
    def succeeded(self) -> bool:
        return all(turn.succeeded for turn in self.turns)


@dataclass
class ConversationEvaluationReport:
    run_id: str
    model: str
    concurrency: int
    started_at: str
    completed_at: str
    duration_ms: int
    scenarios: list[ScenarioEvaluationResult]

    @property
    def total_turns(self) -> int:
        return sum(len(scenario.turns) for scenario in self.scenarios)

    @property
    def failed_turns(self) -> int:
        return sum(
            not turn.succeeded
            for scenario in self.scenarios
            for turn in scenario.turns
        )

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        payload = asdict(self)
        payload["summary"] = {
            "scenario_count": len(self.scenarios),
            "total_turns": self.total_turns,
            "failed_turns": self.failed_turns,
        }
        return payload

def _structure_fingerprint(snapshot: CircuitCoachV2Snapshot) -> str:
    payload = {
        "slots": [slot.model_dump(mode="json") for slot in snapshot.board.slots],
        "edges": [edge.model_dump(mode="json") for edge in snapshot.board.edges],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _slot_records(snapshot: dict[str, Any]) -> dict[int, Any]:
    records: dict[int, Any] = {}
    for slot in snapshot.get("board", {}).get("slots", []):
        slot_id = slot[0] if isinstance(slot, list) else slot.get("slot_id")
        if isinstance(slot_id, int):
            records[slot_id] = slot
    return records


def _edge_records(snapshot: dict[str, Any]) -> set[tuple[int, int]]:
    records: set[tuple[int, int]] = set()
    for edge in snapshot.get("board", {}).get("edges", []):
        if isinstance(edge, list):
            first, second = edge[0], edge[1]
        else:
            first, second = edge.get("port_a"), edge.get("port_b")
        if isinstance(first, int) and isinstance(second, int):
            records.add(tuple(sorted((first, second))))
    return records


def summarize_snapshot_change(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, list[Any]]:
    previous_slots = _slot_records(previous)
    current_slots = _slot_records(current)
    previous_edges = _edge_records(previous)
    current_edges = _edge_records(current)
    return {
        "added_slots": sorted(current_slots.keys() - previous_slots.keys()),
        "removed_slots": sorted(previous_slots.keys() - current_slots.keys()),
        "changed_slots": sorted(
            slot_id
            for slot_id in previous_slots.keys() & current_slots.keys()
            if previous_slots[slot_id] != current_slots[slot_id]
        ),
        "added_edges": [list(edge) for edge in sorted(current_edges - previous_edges)],
        "removed_edges": [list(edge) for edge in sorted(previous_edges - current_edges)],
    }


def _scenario_warnings(scenario: ConversationScenario) -> tuple[str, ...]:
    warnings: list[str] = []
    for turn_index in range(1, len(scenario.turns)):
        previous = scenario.turns[turn_index - 1].snapshot
        current = scenario.turns[turn_index].snapshot
        if (
            _structure_fingerprint(previous) != _structure_fingerprint(current)
            and previous.board.topology_revision == current.board.topology_revision
        ):
            warnings.append(
                f"turn {turn_index + 1} changes circuit structure without changing "
                f"topology_revision {current.board.topology_revision}"
            )
    return tuple(warnings)


def load_conversation_scenarios(
    paths: Sequence[Path],
) -> tuple[LoadedConversationScenario, ...]:
    from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES

    known_level_ids = {case.level_id for case in LEVEL_EVAL_CASES}
    loaded: list[LoadedConversationScenario] = []
    names: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise ValueError(f"scenario file does not exist: {path}")
        try:
            scenario = ConversationScenario.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid scenario file {path}: {exc}") from exc
        if scenario.name in names:
            raise ValueError(f"duplicate scenario name: {scenario.name}")
        if scenario.level_id not in known_level_ids:
            raise ValueError(f"unknown scenario level: {scenario.level_id}")
        names.add(scenario.name)
        loaded.append(
            LoadedConversationScenario(
                scenario=scenario,
                source_path=path,
                warnings=_scenario_warnings(scenario),
            )
        )
    return tuple(loaded)
