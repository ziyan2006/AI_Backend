from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

from tuco_ai_backend.level_logic import LevelLogicSpec, load_level_logic_specs

GENERATED_HEADER = "/* Generated from backend level logic specs. Do not edit. */"


def _c_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_spec(spec: LevelLogicSpec) -> str:
    outputs = ", ".join(str(value) for value in spec.expected_outputs)
    input_labels = " ".join(spec.input_labels)
    output_labels = " ".join(spec.output_labels)
    return (
        f"{{{spec.level_id}, {spec.rule_version}, {spec.input_count}, "
        f"{spec.output_count}, {len(spec.expected_outputs)}, {{{outputs}}}, "
        f"{_c_string(spec.short_goal)}, {_c_string(input_labels)}, "
        f"{_c_string(output_labels)}}},"
    )


def render_firmware_rules(specs: Iterable[LevelLogicSpec]) -> str:
    ordered_specs = sorted(specs, key=lambda spec: (spec.level_id, spec.rule_version))
    lines = [GENERATED_HEADER, ""]
    lines.extend(_render_spec(spec) for spec in ordered_specs)
    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate firmware level rules from backend truth tables."
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    content = render_firmware_rules(load_level_logic_specs().values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
