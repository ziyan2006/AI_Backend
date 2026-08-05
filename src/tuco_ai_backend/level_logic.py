from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LevelLogicSpec:
    level_id: int
    rule_version: int
    input_count: int
    output_count: int
    short_goal: str
    input_labels: tuple[str, ...]
    output_labels: tuple[str, ...]
    expected_outputs: tuple[int, ...]


def _require_int(item: dict[str, Any], field: str) -> int:
    value = item[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_string(item: dict[str, Any], field: str) -> str:
    value = item[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_string_tuple(item: dict[str, Any], field: str) -> tuple[str, ...]:
    value = item[field]
    if not isinstance(value, list) or not all(
        isinstance(label, str) and label.strip() for label in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return tuple(value)


def _require_int_tuple(item: dict[str, Any], field: str) -> tuple[int, ...]:
    value = item[field]
    if not isinstance(value, list) or not all(
        isinstance(entry, int) and not isinstance(entry, bool) for entry in value
    ):
        raise ValueError(f"{field} must be a list of integers")
    return tuple(value)


def _validate_spec(spec: LevelLogicSpec) -> None:
    if spec.level_id <= 0:
        raise ValueError("level_id must be positive")
    if spec.rule_version <= 0:
        raise ValueError("rule_version must be positive")
    if not 1 <= spec.input_count <= 4:
        raise ValueError("input_count must be between 1 and 4")
    if not 1 <= spec.output_count <= 4:
        raise ValueError("output_count must be between 1 and 4")
    if len(spec.input_labels) != spec.input_count:
        raise ValueError(
            f"level {spec.level_id} input label count does not match input_count"
        )
    if len(spec.output_labels) != spec.output_count:
        raise ValueError(
            f"level {spec.level_id} output label count does not match output_count"
        )
    expected_row_count = 1 << spec.input_count
    if len(spec.expected_outputs) != expected_row_count:
        raise ValueError(
            f"level {spec.level_id} truth table must contain {expected_row_count} rows"
        )
    output_limit = 1 << spec.output_count
    if any(value < 0 or value >= output_limit for value in spec.expected_outputs):
        raise ValueError(
            f"level {spec.level_id} truth table values must be between 0 and "
            f"{output_limit - 1}"
        )


def _parse_spec(item: object) -> LevelLogicSpec:
    if not isinstance(item, dict):
        raise ValueError("each level logic spec must be an object")
    try:
        spec = LevelLogicSpec(
            level_id=_require_int(item, "level_id"),
            rule_version=_require_int(item, "rule_version"),
            input_count=_require_int(item, "input_count"),
            output_count=_require_int(item, "output_count"),
            short_goal=_require_string(item, "short_goal"),
            input_labels=_require_string_tuple(item, "input_labels"),
            output_labels=_require_string_tuple(item, "output_labels"),
            expected_outputs=_require_int_tuple(item, "expected_outputs"),
        )
    except KeyError as exc:
        raise ValueError(f"missing level logic field: {exc.args[0]}") from exc
    _validate_spec(spec)
    return spec


@lru_cache(maxsize=1)
def load_level_logic_specs() -> dict[tuple[int, int], LevelLogicSpec]:
    path = Path(__file__).with_name("data") / "level_logic_specs.json"
    raw_items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_items, list):
        raise ValueError("level logic specs must be a list")

    specs: dict[tuple[int, int], LevelLogicSpec] = {}
    for item in raw_items:
        spec = _parse_spec(item)
        key = (spec.level_id, spec.rule_version)
        if key in specs:
            raise ValueError(f"duplicate level logic spec: {key}")
        specs[key] = spec
    return specs


def get_level_logic_spec(level_id: int, rule_version: int) -> LevelLogicSpec:
    return load_level_logic_specs()[(level_id, rule_version)]
