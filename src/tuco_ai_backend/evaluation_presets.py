from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES, build_circuit_coach_v2
from tuco_ai_backend.evaluation_scenarios import (
    ConversationScenario,
    LoadedConversationScenario,
)
from tuco_ai_backend.models import CircuitCoachV2Snapshot

PresetBuilder = Callable[[], ConversationScenario]


def _full_502_progress_snapshot(
    base: CircuitCoachV2Snapshot,
    topology_revision: int,
    *,
    wrong_direct_output: bool = False,
    partial_or: bool = False,
) -> CircuitCoachV2Snapshot:
    payload = base.model_dump(mode="json", by_alias=True)
    slots: list[list[object]] = [
        [slot_id, slot_id // 4, slot_id % 4, "empty", None, []]
        for slot_id in range(16)
    ]
    slots[0] = [0, 0, 0, "present", "OUTPUT", [[2, "left", "input"]]]
    slots[4] = [
        4,
        0,
        2,
        "present",
        "AND",
        [[16, "right", "output"], [17, "down", "input"], [19, "up", "input"]],
    ]
    slots[5] = [
        5,
        1,
        2,
        "present",
        "AND",
        [[22, "right", "output"], [21, "down", "input"], [23, "up", "input"]],
    ]
    slots[6] = [
        6,
        0,
        0,
        "present",
        "INPUT",
        [[24, "right", "output"], [25, "down", "output"]],
    ]
    slots[7] = [
        7,
        1,
        0,
        "present",
        "INPUT",
        [[28, "right", "output"], [29, "down", "output"]],
    ]
    slots[8] = [
        8,
        2,
        0,
        "present",
        "INPUT",
        [[32, "right", "output"], [33, "down", "output"]],
    ]
    slots[10] = [
        10,
        2,
        2,
        "present",
        "AND",
        [[40, "right", "output"], [41, "down", "input"], [43, "up", "input"]],
    ]
    edges: list[list[object]] = [
        [24, 17, "valid"],
        [28, 19, "valid"],
        [25, 21, "valid"],
        [32, 23, "valid"],
        [29, 41, "valid"],
        [33, 43, "valid"],
    ]
    if wrong_direct_output:
        edges.append([16, 2, "valid"])
    if partial_or:
        slots[1] = [
            1,
            0,
            1,
            "present",
            "OR",
            [[4, "right", "output"], [5, "down", "input"], [7, "up", "input"]],
        ]
        edges.append([16, 5, "valid"])
    payload["board"] = {
        "topology_revision": topology_revision,
        "slots": slots,
        "edges": edges,
    }
    return CircuitCoachV2Snapshot.model_validate(payload)


def _build_502_guidance_quality() -> ConversationScenario:
    case = next(item for item in LEVEL_EVAL_CASES if item.level_id == 502)
    empty = build_circuit_coach_v2(case, "empty")
    placed = build_circuit_coach_v2(case, "placed-io")
    progress = _full_502_progress_snapshot(placed, 31)
    wrong = _full_502_progress_snapshot(placed, 32, wrong_direct_output=True)
    partial_or = _full_502_progress_snapshot(placed, 33, partial_or=True)
    return ConversationScenario.model_validate(
        {
            "schema": "tuco_conversation_scenario_v1",
            "name": "502-guidance-quality",
            "description": "覆盖空板、输入输出、求助、错误接线、原因解释和部分修正",
            "level_id": 502,
            "tags": ["quality", "multi-turn", "diagnosis", "preset"],
            "turns": [
                {
                    "user_text": "这关要做什么？",
                    "note": "没有放置任何积木",
                    "snapshot": empty.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "我已经把输入和输出积木放好了，接下来应该怎么做？",
                    "note": "仅放好3块输入和1块输出",
                    "snapshot": placed.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "我把三组两两组合的与门都接好了，但我有点不会了，给我一点提示。",
                    "note": "三路两两与已完成，尚未放或门",
                    "snapshot": progress.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "我这样接对了吗？哪里有问题？",
                    "note": "错误地把一块与门输出直接接到最终输出",
                    "snapshot": wrong.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "为什么不能把这个与门直接接到输出？为什么还需要别的积木？",
                    "note": "保持错误快照并追问原理",
                    "snapshot": wrong.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "我已经拆掉错误的线，放好了或门，也接进去一组结果，接下来做什么？",
                    "note": "已放或门并接入第一路与门结果",
                    "snapshot": partial_or.model_dump(mode="json", by_alias=True),
                },
            ],
        }
    )


_PRESET_BUILDERS: dict[str, PresetBuilder] = {
    "502-guidance-quality": _build_502_guidance_quality,
}


def load_conversation_presets(
    names: Sequence[str],
) -> tuple[LoadedConversationScenario, ...]:
    loaded: list[LoadedConversationScenario] = []
    seen: set[str] = set()
    for name in names:
        builder = _PRESET_BUILDERS.get(name)
        if builder is None:
            raise ValueError(f"unknown conversation preset: {name}")
        if name in seen:
            raise ValueError(f"duplicate conversation preset: {name}")
        seen.add(name)
        loaded.append(
            LoadedConversationScenario(
                scenario=builder(),
                source_path=Path("preset") / name,
            )
        )
    return tuple(loaded)
