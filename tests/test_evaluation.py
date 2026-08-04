import asyncio

import pytest

from tuco_ai_backend.evaluation import (
    CIRCUIT_PROTOCOLS,
    LEARNING_ACTIVITY_SETUPS,
    LEVEL_EVAL_CASES,
    build_circuit_coach_v2,
    build_empty_circuit,
    build_learning_activity_context,
    build_required_io_circuit,
    render_markdown_report,
    run_concurrent_evaluation,
)
from tuco_ai_backend.models import DecisionResponse


class RecordingDecisionClient:
    def __init__(self, *, failing_level: int | None = None) -> None:
        self.failing_level = failing_level
        self.active_calls = 0
        self.max_active_calls = 0
        self.seen: list[tuple[int, str, list[dict[str, str]], str | None]] = []

    async def decide(self, request, history=None, trace_id=None):
        level_id = request.circuit.level.level_id
        copied_history = [dict(message) for message in history or []]
        self.seen.append((level_id, request.question, copied_history, trace_id))
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0.01)
            if level_id == self.failing_level:
                raise RuntimeError(f"level {level_id} failed")
            return DecisionResponse(
                assistant_text=f"第{level_id}关回答：{request.question}",
                topology_revision=request.circuit.topology_revision,
            )
        finally:
            self.active_calls -= 1


class V2RecordingDecisionClient:
    def __init__(self) -> None:
        self.seen: list[tuple[int, str, list[dict[str, str]], str | None]] = []

    async def decide(self, request, history=None, trace_id=None):
        level_id = request.circuit_snapshot.level.id
        self.seen.append((level_id, request.user_text, list(history or []), trace_id))
        return DecisionResponse(
            assistant_text=f"第{level_id}关 v2 回答：{request.user_text}",
            topology_revision=request.circuit_snapshot.board.topology_revision,
        )


def test_build_empty_circuit_matches_text_debug_snapshot() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 301)

    circuit = build_empty_circuit(level)

    assert circuit.schema_version == 3
    assert circuit.play_active is True
    assert circuit.topology_revision == 0
    assert circuit.level is not None
    assert circuit.level.level_id == 301
    assert circuit.level.input_names == "密码开关 A, B"
    assert circuit.level.output_names == "星门钥匙 Y"
    assert circuit.slots == [{"slot": slot, "present": False} for slot in range(16)]
    assert circuit.port_roles == [0] * 64
    assert circuit.links == []


def test_build_required_io_circuit_matches_frontend_port_roles() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 403)

    circuit = build_required_io_circuit(level)

    assert circuit.topology_revision == 4
    assert circuit.slots[:4] == [
        {"slot": 0, "present": True, "id_valid": True, "raw_id": 0xF0, "gate": 0},
        {"slot": 1, "present": True, "id_valid": True, "raw_id": 0xF0, "gate": 0},
        {"slot": 2, "present": True, "id_valid": True, "raw_id": 0xF1, "gate": 1},
        {"slot": 3, "present": True, "id_valid": True, "raw_id": 0xF1, "gate": 1},
    ]
    assert circuit.slots[4:] == [{"slot": slot, "present": False} for slot in range(4, 16)]
    assert circuit.port_roles[:13] == [2] * 8 + [0, 0, 1, 0, 1]
    assert circuit.port_roles[13:] == [0] * 51
    assert circuit.links == []


def test_build_circuit_coach_v2_matches_firmware_compact_snapshot() -> None:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 403)

    circuit = build_circuit_coach_v2(level, "placed-io")

    assert "legacy" in CIRCUIT_PROTOCOLS
    assert "circuit-v2" in CIRCUIT_PROTOCOLS
    assert circuit.schema_name == "tuco_circuit_v2"
    assert circuit.level.id == 403
    assert circuit.board.topology_revision == 4
    assert [slot.gate for slot in circuit.board.slots[:4]] == [
        "INPUT",
        "INPUT",
        "OUTPUT",
        "OUTPUT",
    ]
    assert all(slot.state == "empty" for slot in circuit.board.slots[4:])


def test_build_learning_activity_context_matches_firmware_states() -> None:
    activity = build_learning_activity_context(401, "near-solved")

    assert "near-solved" in LEARNING_ACTIVITY_SETUPS
    assert activity is not None
    assert activity.slot_roles == ["8", "4", "2", "1"]
    assert activity.slot_bits == [0, 1, 1, 1]
    assert activity.target_bits == [0, 1, 1, 0]
    assert activity.current_decimal == 7

    parity = build_learning_activity_context(501, "near-solved")

    assert parity is not None
    assert parity.kind == "three_input_parity"
    assert parity.slot_roles == ["A", "B", "进位输入"]
    assert parity.target_decimal == 1
    assert parity.current_decimal == 2
    assert not parity.solved

    with pytest.raises(ValueError, match="does not have"):
        build_learning_activity_context(101, "unsolved")


@pytest.mark.asyncio
async def test_concurrent_evaluation_limits_calls_and_isolates_level_history() -> None:
    client = RecordingDecisionClient()
    cases = LEVEL_EVAL_CASES[:3]
    questions = ("这关要做什么？", "接下来怎么做？")

    report = await run_concurrent_evaluation(
        client,
        cases=cases,
        questions=questions,
        concurrency=2,
        model="test-model",
        run_id="test-run",
    )

    assert client.max_active_calls == 2
    assert [result.level_id for result in report.levels] == [101, 102, 103]
    assert report.total_turns == 6
    assert report.successful_turns == 6
    assert report.failed_turns == 0

    for case in cases:
        level_calls = [call for call in client.seen if call[0] == case.level_id]
        assert level_calls[0][2] == []
        assert level_calls[1][2] == [
            {"role": "user", "content": questions[0]},
            {
                "role": "assistant",
                "content": f"第{case.level_id}关回答：{questions[0]}",
            },
        ]
        assert all(call[3].startswith(f"test-run-l{case.level_id}-") for call in level_calls)


@pytest.mark.asyncio
async def test_concurrent_evaluation_keeps_other_levels_when_one_level_fails() -> None:
    client = RecordingDecisionClient(failing_level=102)

    report = await run_concurrent_evaluation(
        client,
        cases=LEVEL_EVAL_CASES[:3],
        questions=("这关要做什么？",),
        concurrency=3,
        model="test-model",
        run_id="error-run",
    )

    assert report.successful_turns == 2
    assert report.failed_turns == 1
    failed_level = next(result for result in report.levels if result.level_id == 102)
    assert failed_level.turns[0].assistant_text is None
    assert failed_level.turns[0].error_type == "RuntimeError"
    assert failed_level.turns[0].error_message == "level 102 failed"

    markdown = render_markdown_report(report)
    assert "# LLM 全关卡并发评测" in markdown
    assert "第 101 关：启动飞船" in markdown
    assert "第 102 关：与非门" in markdown
    assert "RuntimeError: level 102 failed" in markdown


@pytest.mark.asyncio
async def test_concurrent_evaluation_uses_selected_circuit_setup() -> None:
    client = RecordingDecisionClient()
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)

    report = await run_concurrent_evaluation(
        client,
        cases=(level,),
        questions=("接下来应该怎么做？给我点提示",),
        circuit_setup="placed-io",
        run_id="placed-io-run",
    )

    assert report.circuit_setup == "placed-io"
    assert report.to_dict()["circuit_setup"] == "placed-io"


@pytest.mark.asyncio
async def test_concurrent_evaluation_uses_v2_requests_when_protocol_is_selected() -> None:
    client = V2RecordingDecisionClient()
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)

    report = await run_concurrent_evaluation(
        client,
        cases=(level,),
        questions=("接下来应该怎么做？给我点提示",),
        circuit_setup="placed-io",
        circuit_protocol="circuit-v2",
        run_id="v2-run",
    )

    assert report.circuit_protocol == "circuit-v2"
    assert report.to_dict()["circuit_protocol"] == "circuit-v2"
    assert client.seen == [(101, "接下来应该怎么做？给我点提示", [], "v2-run-l101-t1")]


def test_level_catalog_contains_every_playable_level() -> None:
    assert [case.level_id for case in LEVEL_EVAL_CASES] == [
        101,
        102,
        103,
        201,
        202,
        203,
        301,
        302,
        401,
        402,
        403,
        501,
        502,
        503,
        504,
        601,
        602,
    ]
