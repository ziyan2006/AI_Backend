from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tuco_ai_backend.evaluation import (
    run_conversation_scenarios,
    write_conversation_evaluation_report,
)
from tuco_ai_backend.evaluation_scenarios import (
    load_conversation_scenarios,
    summarize_snapshot_change,
)
from tuco_ai_backend.evaluation_tracing import EvaluationTraceCollector
from tuco_ai_backend.models import DecisionResponse


def _snapshot(*, level_id: int = 502, revision: int = 1, edges=None) -> dict:
    return {
        "schema": "tuco_circuit_v2",
        "level": {"id": level_id, "rule_version": 1},
        "unlocked_gates": ["INPUT", "OUTPUT", "AND", "OR"],
        "board": {
            "topology_revision": revision,
            "slots": [
                [0, 0, 0, "present", "INPUT", [[0, "right", "output"]]],
                [1, 0, 1, "present", "OUTPUT", [[4, "left", "input"]]],
            ],
            "edges": edges or [],
        },
    }


def _write_scenario(path: Path, *, name: str = "scenario", turns=None) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "tuco_conversation_scenario_v1",
                "name": name,
                "description": "测试场景",
                "level_id": 502,
                "tags": ["test"],
                "turns": turns
                or [{"user_text": "下一步呢", "snapshot": _snapshot(), "note": "初始状态"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_load_conversation_scenario_keeps_source_and_metadata(tmp_path: Path) -> None:
    path = _write_scenario(tmp_path / "scenario.json")

    loaded = load_conversation_scenarios([path])

    assert len(loaded) == 1
    assert loaded[0].source_path == path
    assert loaded[0].scenario.name == "scenario"
    assert loaded[0].scenario.turns[0].snapshot.level.id == 502
    assert loaded[0].warnings == ()


def test_scenario_requires_matching_level_and_non_empty_turns(tmp_path: Path) -> None:
    path = _write_scenario(
        tmp_path / "invalid.json",
        turns=[{"user_text": "下一步呢", "snapshot": _snapshot(level_id=403)}],
    )

    with pytest.raises(
        ValueError, match="snapshot level 403 does not match scenario level 502"
    ):
        load_conversation_scenarios([path])


def test_duplicate_scenario_names_are_rejected(tmp_path: Path) -> None:
    first = _write_scenario(tmp_path / "first.json", name="duplicate")
    second = _write_scenario(tmp_path / "second.json", name="duplicate")

    with pytest.raises(ValueError, match="duplicate scenario name: duplicate"):
        load_conversation_scenarios([first, second])


def test_structure_change_without_revision_change_adds_warning(tmp_path: Path) -> None:
    path = _write_scenario(
        tmp_path / "warning.json",
        turns=[
            {"user_text": "第一轮", "snapshot": _snapshot(revision=5)},
            {
                "user_text": "第二轮",
                "snapshot": _snapshot(revision=5, edges=[[0, 4, "valid"]]),
            },
        ],
    )

    loaded = load_conversation_scenarios([path])

    assert loaded[0].warnings == (
        "turn 2 changes circuit structure without changing topology_revision 5",
    )


class RecordingScenarioClient:
    def __init__(self) -> None:
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def decide(self, request, history=None, trace_id=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(
            {
                "text": request.user_text,
                "revision": request.circuit_snapshot.board.topology_revision,
                "history": [dict(item) for item in history or []],
                "trace_id": trace_id,
            }
        )
        try:
            await asyncio.sleep(0.01)
            if request.user_text == "失败轮":
                raise RuntimeError("simulated failure")
            return DecisionResponse(
                assistant_text=f"回复：{request.user_text}",
                topology_revision=request.circuit_snapshot.board.topology_revision,
            )
        finally:
            self.active -= 1


class TracedScenarioClient:
    def __init__(self, collector: EvaluationTraceCollector) -> None:
        self.collector = collector

    async def decide(self, request, history=None, trace_id=None):
        assert trace_id is not None
        self.collector.record(
            trace_id,
            "provider_request",
            {"messages": history or [], "user_text": request.user_text},
        )
        self.collector.record(
            trace_id,
            "normalized_decision",
            {"assistant_text": f"回复：{request.user_text}"},
        )
        return DecisionResponse(
            assistant_text=f"回复：{request.user_text}",
            topology_revision=request.circuit_snapshot.board.topology_revision,
        )


@pytest.mark.asyncio
async def test_scenario_turns_share_history_but_use_each_full_snapshot(
    tmp_path: Path,
) -> None:
    path = _write_scenario(
        tmp_path / "multi.json",
        turns=[
            {"user_text": "第一轮", "snapshot": _snapshot(revision=10)},
            {"user_text": "第二轮", "snapshot": _snapshot(revision=11)},
            {"user_text": "第三轮", "snapshot": _snapshot(revision=12)},
        ],
    )
    client = RecordingScenarioClient()

    report = await run_conversation_scenarios(
        client,
        scenarios=load_conversation_scenarios([path]),
        concurrency=1,
        model="fake",
        run_id="multi-turn",
    )

    assert [item["revision"] for item in client.calls] == [10, 11, 12]
    assert client.calls[0]["history"] == []
    assert client.calls[1]["history"] == [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "回复：第一轮"},
    ]
    assert report.scenarios[0].turns[2].history_before_turn == client.calls[2]["history"]


@pytest.mark.asyncio
async def test_scenario_turns_take_trace_events_into_report(tmp_path: Path) -> None:
    path = _write_scenario(
        tmp_path / "traced.json",
        turns=[{"user_text": "第一轮", "snapshot": _snapshot(revision=15)}],
    )
    collector = EvaluationTraceCollector()

    report = await run_conversation_scenarios(
        TracedScenarioClient(collector),
        scenarios=load_conversation_scenarios([path]),
        concurrency=1,
        model="fake",
        run_id="traced",
        trace_collector=collector,
    )

    assert report.scenarios[0].turns[0].trace == {
        "provider_request": {"messages": [], "user_text": "第一轮"},
        "normalized_decision": {"assistant_text": "回复：第一轮"},
    }
    assert collector.take("traced-s1-t1") == {}


@pytest.mark.asyncio
async def test_failed_turn_does_not_stop_scenario_or_pollute_history(
    tmp_path: Path,
) -> None:
    path = _write_scenario(
        tmp_path / "failure.json",
        turns=[
            {"user_text": "第一轮", "snapshot": _snapshot(revision=20)},
            {"user_text": "失败轮", "snapshot": _snapshot(revision=21)},
            {"user_text": "第三轮", "snapshot": _snapshot(revision=22)},
        ],
    )
    client = RecordingScenarioClient()

    report = await run_conversation_scenarios(
        client,
        scenarios=load_conversation_scenarios([path]),
        concurrency=1,
        model="fake",
        run_id="failure",
    )

    assert len(client.calls) == 3
    assert report.scenarios[0].turns[1].error_type == "RuntimeError"
    assert client.calls[2]["history"] == [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "回复：第一轮"},
    ]


@pytest.mark.asyncio
async def test_scenarios_run_concurrently_but_turns_remain_serial(tmp_path: Path) -> None:
    first = _write_scenario(tmp_path / "first.json", name="first")
    second = _write_scenario(tmp_path / "second.json", name="second")
    client = RecordingScenarioClient()

    await run_conversation_scenarios(
        client,
        scenarios=load_conversation_scenarios([first, second]),
        concurrency=2,
        model="fake",
        run_id="concurrent",
    )

    assert client.max_active == 2


def test_snapshot_change_summary_detects_slots_and_order_independent_edges() -> None:
    previous = _snapshot(revision=1)
    current = _snapshot(revision=2, edges=[[4, 0, "valid"]])
    current["board"]["slots"][1][4] = "AND"

    summary = summarize_snapshot_change(previous, current)

    assert summary["changed_slots"] == [1]
    assert summary["added_edges"] == [[0, 4]]
    assert summary["removed_edges"] == []


@pytest.mark.asyncio
async def test_conversation_report_keeps_full_turn_data_and_writes_summary(
    tmp_path: Path,
) -> None:
    path = _write_scenario(
        tmp_path / "report.json",
        turns=[
            {"user_text": "第一轮", "snapshot": _snapshot(revision=30)},
            {
                "user_text": "第二轮",
                "snapshot": _snapshot(revision=31, edges=[[0, 4, "valid"]]),
            },
        ],
    )
    report = await run_conversation_scenarios(
        RecordingScenarioClient(),
        scenarios=load_conversation_scenarios([path]),
        concurrency=1,
        model="fake",
        run_id="report",
    )
    report.scenarios[0].turns[0].trace = {
        "provider_request": {"messages": [{"role": "system", "content": "prompt"}]},
        "provider_response": {"choices": []},
    }

    json_path, markdown_path = write_conversation_evaluation_report(report, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    first_turn = payload["scenarios"][0]["turns"][0]
    assert first_turn["snapshot"]["schema"] == "tuco_circuit_v2"
    assert first_turn["history_before_turn"] == []
    assert first_turn["trace"]["provider_request"]["messages"]
    assert payload["summary"] == {"scenario_count": 1, "total_turns": 2, "failed_turns": 0}
    assert "第 2 轮" in markdown
    assert "新增连线：0 ↔ 4" in markdown
    assert "provider_request" not in markdown
