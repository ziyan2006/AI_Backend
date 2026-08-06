from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from tuco_ai_backend.evaluation import (
    LEVEL_EVAL_CASES,
    LevelEvalCase,
    build_circuit_coach_v2,
)
from tuco_ai_backend.evaluation_scenarios import (
    ConversationScenario,
    LoadedConversationScenario,
)
from tuco_ai_backend.models import CircuitCoachV2Snapshot

PresetBuilder = Callable[[], tuple[ConversationScenario, ...]]

_SPOKEN_GATE_NAMES = {
    "AND": "与门",
    "OR": "或门",
    "NOT": "非门",
    "NAND": "与非门",
    "NOR": "或非门",
    "XOR": "异或门",
    "XNOR": "同或门",
}


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


def _with_board_state(
    snapshot: CircuitCoachV2Snapshot,
    topology_revision: int,
    edges: list[list[object]] | None = None,
) -> CircuitCoachV2Snapshot:
    payload = snapshot.model_dump(mode="json", by_alias=True)
    payload["board"]["topology_revision"] = topology_revision
    if edges is not None:
        payload["board"]["edges"] = edges
    return CircuitCoachV2Snapshot.model_validate(payload)


def _build_502_guidance_quality() -> tuple[ConversationScenario, ...]:
    case = next(item for item in LEVEL_EVAL_CASES if item.level_id == 502)
    empty = build_circuit_coach_v2(case, "empty")
    placed = build_circuit_coach_v2(case, "placed-io")
    progress = _full_502_progress_snapshot(placed, 31)
    wrong = _full_502_progress_snapshot(placed, 32, wrong_direct_output=True)
    partial_or = _full_502_progress_snapshot(placed, 33, partial_or=True)
    return (
        ConversationScenario.model_validate(
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
                        "user_text": (
                            "我把三组两两组合的与门都接好了，但我有点不会了，给我一点提示。"
                        ),
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
                        "user_text": (
                            "我已经拆掉错误的线，放好了或门，也接进去一组结果，接下来做什么？"
                        ),
                        "note": "已放或门并接入第一路与门结果",
                        "snapshot": partial_or.model_dump(mode="json", by_alias=True),
                    },
                ],
            }
        ),
    )


def _build_other_guidance_quality_for_case(
    case: LevelEvalCase,
) -> ConversationScenario:
    empty = build_circuit_coach_v2(case, "empty")
    placed = build_circuit_coach_v2(case, "placed-io")
    progress_setup = "placed-io" if case.level_id == 101 else "actionable-logic"
    progress = build_circuit_coach_v2(case, progress_setup)
    progress = _with_board_state(
        progress,
        placed.board.topology_revision + 1,
    )
    progress_gate = next(
        (
            slot.gate
            for slot in progress.board.slots
            if slot.state == "present" and slot.gate not in {None, "INPUT", "OUTPUT"}
        ),
        None,
    )
    progress_user_text = "输入和输出积木都放好了，但我有点不会了，给我一点提示。"
    if isinstance(progress_gate, str):
        progress_user_text = (
            f"我现在放了一块{_SPOKEN_GATE_NAMES[progress_gate]}积木，但还没接线。"
            "我有点不会了，给我一点提示。"
        )
    error_port_b = 1 if case.level_id == 101 else 48
    error_edges = [[0, error_port_b, "valid"]]
    wrong = _with_board_state(
        progress,
        progress.board.topology_revision + 1,
        error_edges,
    )
    return ConversationScenario.model_validate(
        {
            "schema": "tuco_conversation_scenario_v1",
            "name": f"{case.level_id}-guidance-quality",
            "description": "覆盖空板、输入输出、求助、错误接线和原因解释",
            "level_id": case.level_id,
            "tags": ["quality", "multi-turn", "diagnosis", "preset"],
            "turns": [
                {
                    "user_text": "这关要做什么？",
                    "note": "没有放置任何积木",
                    "snapshot": empty.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "我已经把输入和输出积木放好了，接下来应该怎么做？",
                    "note": "仅放好输入和输出积木",
                    "snapshot": placed.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": progress_user_text,
                    "note": "已放一块当前可用积木或保持输入输出状态",
                    "snapshot": progress.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "我把这两个光点连起来了，这样接对了吗？哪里有问题？",
                    "note": "制造一个物理上不合理的连接",
                    "snapshot": wrong.model_dump(mode="json", by_alias=True),
                },
                {
                    "user_text": "为什么这两个光点不能这样连接？",
                    "note": "保持错误快照并追问连接原则",
                    "snapshot": wrong.model_dump(mode="json", by_alias=True),
                },
            ],
        }
    )


def _build_all_other_guidance_quality() -> tuple[ConversationScenario, ...]:
    return tuple(
        _build_other_guidance_quality_for_case(case)
        for case in LEVEL_EVAL_CASES
        if case.level_id != 502
    )


_PRESET_BUILDERS: dict[str, PresetBuilder] = {
    "502-guidance-quality": _build_502_guidance_quality,
    "all-other-guidance-quality": _build_all_other_guidance_quality,
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
        scenarios = builder()
        for scenario in scenarios:
            source_path = Path("preset") / name
            if len(scenarios) > 1:
                source_path /= str(scenario.level_id)
            loaded.append(
                LoadedConversationScenario(
                    scenario=scenario,
                    source_path=source_path,
                )
            )
    return tuple(loaded)
