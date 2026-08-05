import json
from pathlib import Path

import pytest

import tuco_ai_backend.evaluation_cli as evaluation_cli_module
from tuco_ai_backend.evaluation_cli import parse_args, run_cli, select_level_cases
from tuco_ai_backend.models import DecisionResponse


class FakeDecisionClient:
    def __init__(self, *, failing_level: int | None = None) -> None:
        self.failing_level = failing_level
        self.closed = False
        self.requests = []

    async def decide(self, request, history=None, trace_id=None):
        self.requests.append(request)
        if hasattr(request, "circuit_snapshot"):
            level_id = request.circuit_snapshot.level.id
            question = request.user_text
            topology_revision = request.circuit_snapshot.board.topology_revision
        else:
            level_id = request.circuit.level.level_id
            question = request.question
            topology_revision = request.circuit.topology_revision
        if level_id == self.failing_level:
            raise RuntimeError("simulated failure")
        return DecisionResponse(
            assistant_text=f"第{level_id}关：{question}",
            topology_revision=topology_revision,
        )

    async def close(self) -> None:
        self.closed = True


def test_parse_args_accepts_repeated_questions_and_level_filter() -> None:
    args = parse_args(
        [
            "--concurrency",
            "2",
            "--levels",
            "301,302",
            "--question",
            "这关要做什么？",
            "--question",
            "接下来怎么做？",
        ]
    )

    assert args.concurrency == 2
    assert args.levels == "301,302"
    assert args.questions == ["这关要做什么？", "接下来怎么做？"]


def test_parse_args_accepts_repeated_level_flags() -> None:
    args = parse_args(["--level", "301", "--level", "302"])

    assert args.level_ids == [301, 302]


def test_parse_args_accepts_circuit_setup() -> None:
    args = parse_args(["--circuit-setup", "actionable-logic"])

    assert args.circuit_setup == "actionable-logic"


def test_parse_args_accepts_circuit_v2_protocol() -> None:
    args = parse_args(["--protocol", "circuit-v2"])

    assert args.protocol == "circuit-v2"


def test_parse_args_accepts_repeated_conversation_scenarios() -> None:
    args = parse_args(
        [
            "--conversation-scenario",
            "a.json",
            "--conversation-scenario",
            "b.json",
        ]
    )

    assert args.conversation_scenarios == [Path("a.json"), Path("b.json")]


def _write_conversation_scenario(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "tuco_conversation_scenario_v1",
                "name": "cli-scenario",
                "level_id": 502,
                "turns": [
                    {
                        "user_text": "第一轮",
                        "snapshot": {
                            "schema": "tuco_circuit_v2",
                            "level": {"id": 502, "rule_version": 1},
                            "unlocked_gates": ["INPUT", "OUTPUT", "AND"],
                            "board": {"topology_revision": 1, "slots": [], "edges": []},
                        },
                    },
                    {
                        "user_text": "第二轮",
                        "snapshot": {
                            "schema": "tuco_circuit_v2",
                            "level": {"id": 502, "rule_version": 1},
                            "unlocked_gates": ["INPUT", "OUTPUT", "AND"],
                            "board": {"topology_revision": 2, "slots": [], "edges": []},
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_scenario_mode_rejects_legacy_selection_flags(tmp_path: Path) -> None:
    scenario = _write_conversation_scenario(tmp_path / "scenario.json")
    messages: list[str] = []

    exit_code = await run_cli(
        [
            "--conversation-scenario",
            str(scenario),
            "--question",
            "冲突问题",
        ],
        client_factory=lambda _config: pytest.fail("参数冲突时不应创建客户端"),
        print_fn=messages.append,
    )

    assert exit_code == 2
    assert any("不能与" in message for message in messages)


@pytest.mark.asyncio
async def test_run_cli_executes_conversation_scenario_and_writes_reports(
    tmp_path: Path,
) -> None:
    scenario = _write_conversation_scenario(tmp_path / "scenario.json")
    env_path = tmp_path / ".env"
    env_path.write_text("TUCO_LLM_API_KEY=test-key\n", encoding="utf-8")
    output_dir = tmp_path / "reports"
    client = FakeDecisionClient()

    exit_code = await run_cli(
        [
            "--conversation-scenario",
            str(scenario),
            "--env-file",
            str(env_path),
            "--output-dir",
            str(output_dir),
            "--concurrency",
            "1",
        ],
        client_factory=lambda _config: client,
        print_fn=lambda _message: None,
    )

    assert exit_code == 0
    assert client.closed is True
    assert [request.user_text for request in client.requests] == ["第一轮", "第二轮"]
    assert len(list(output_dir.glob("*.json"))) == 1
    assert len(list(output_dir.glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_scenario_cli_injects_trace_collector_into_default_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _write_conversation_scenario(tmp_path / "scenario.json")
    env_path = tmp_path / ".env"
    env_path.write_text("TUCO_LLM_API_KEY=test-key\n", encoding="utf-8")
    output_dir = tmp_path / "reports"
    captured: dict[str, object] = {}

    class TraceAwareClient:
        def __init__(self, _config, *, trace_sink=None) -> None:
            captured["trace_sink"] = trace_sink
            self.trace_sink = trace_sink

        async def decide(self, request, history=None, trace_id=None):
            assert trace_id is not None
            self.trace_sink.record(
                trace_id,
                "provider_request",
                {"user_text": request.user_text, "history": history or []},
            )
            return DecisionResponse(
                assistant_text=f"回复：{request.user_text}",
                topology_revision=request.circuit_snapshot.board.topology_revision,
            )

        async def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(evaluation_cli_module, "CircuitCoachV2Client", TraceAwareClient)

    exit_code = await run_cli(
        [
            "--conversation-scenario",
            str(scenario),
            "--env-file",
            str(env_path),
            "--output-dir",
            str(output_dir),
        ],
        print_fn=lambda _message: None,
    )

    payload = json.loads(next(output_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured["trace_sink"] is not None
    assert captured["closed"] is True
    assert payload["scenarios"][0]["turns"][0]["trace"]["provider_request"] == {
        "user_text": "第一轮",
        "history": [],
    }


def test_select_level_cases_preserves_catalog_order_and_rejects_unknown_levels() -> None:
    assert [case.level_id for case in select_level_cases("302,101")] == [101, 302]
    assert [case.level_id for case in select_level_cases("302", [101, 403])] == [101, 302, 403]

    with pytest.raises(ValueError, match="999"):
        select_level_cases("101,999")


@pytest.mark.asyncio
async def test_run_cli_lists_levels_without_loading_llm_configuration() -> None:
    messages: list[str] = []

    exit_code = await run_cli(
        ["--list-levels"],
        client_factory=lambda _config: pytest.fail("不应创建 LLM 客户端"),
        print_fn=messages.append,
    )

    assert exit_code == 0
    assert messages[0] == "可评测关卡："
    assert "301：互斥钥匙" in messages


@pytest.mark.asyncio
async def test_run_cli_writes_reports_and_closes_client(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TUCO_LLM_BASE_URL=https://example.invalid/v1\n"
        "TUCO_LLM_MODEL=test-model\n"
        "TUCO_LLM_API_KEY=test-key\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "reports"
    client = FakeDecisionClient()
    messages: list[str] = []

    exit_code = await run_cli(
        [
            "--env-file",
            str(env_path),
            "--output-dir",
            str(output_dir),
            "--levels",
            "101,102",
            "--question",
            "这关要做什么？",
        ],
        client_factory=lambda _config: client,
        print_fn=messages.append,
    )

    assert exit_code == 0
    assert client.closed is True
    assert len(list(output_dir.glob("*.json"))) == 1
    assert len(list(output_dir.glob("*.md"))) == 1
    assert any("成功 2" in message for message in messages)


@pytest.mark.asyncio
async def test_run_cli_uses_ready_io_setup_and_next_step_default_question(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TUCO_LLM_API_KEY=test-key\n", encoding="utf-8")
    client = FakeDecisionClient()

    exit_code = await run_cli(
        [
            "--env-file",
            str(env_path),
            "--output-dir",
            str(tmp_path / "reports"),
            "--level",
            "101",
            "--circuit-setup",
            "placed-io",
        ],
        client_factory=lambda _config: client,
        print_fn=lambda _message: None,
    )

    assert exit_code == 0
    assert client.requests[0].question == "接下来应该怎么做？给我点提示"
    assert client.requests[0].circuit.slots[:2] == [
        {"slot": 0, "present": True, "id_valid": True, "raw_id": 0xF0, "gate": 0},
        {"slot": 1, "present": True, "id_valid": True, "raw_id": 0xF1, "gate": 1},
    ]


@pytest.mark.asyncio
async def test_run_cli_uses_v2_protocol_with_v2_request_shape(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TUCO_LLM_API_KEY=test-key\n", encoding="utf-8")
    client = FakeDecisionClient()

    exit_code = await run_cli(
        [
            "--env-file",
            str(env_path),
            "--output-dir",
            str(tmp_path / "reports"),
            "--level",
            "101",
            "--protocol",
            "circuit-v2",
        ],
        client_factory=lambda _config: client,
        print_fn=lambda _message: None,
    )

    assert exit_code == 0
    assert client.requests[0].circuit_snapshot.schema_name == "tuco_circuit_v2"


@pytest.mark.asyncio
async def test_run_cli_supports_three_input_parity_learning_activity(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TUCO_LLM_API_KEY=test-key\n", encoding="utf-8")
    client = FakeDecisionClient()

    exit_code = await run_cli(
        [
            "--env-file",
            str(env_path),
            "--output-dir",
            str(tmp_path / "reports"),
            "--level",
            "501",
            "--protocol",
            "circuit-v2",
            "--learning-activity",
            "near-solved",
        ],
        client_factory=lambda _config: client,
        print_fn=lambda _message: None,
    )

    assert exit_code == 0
    assert client.requests[0].learning_activity.kind == "three_input_parity"
    assert client.requests[0].learning_activity.current_decimal == 2


@pytest.mark.asyncio
async def test_run_cli_supports_three_input_carry_learning_activity(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TUCO_LLM_API_KEY=test-key\n", encoding="utf-8")
    client = FakeDecisionClient()

    exit_code = await run_cli(
        [
            "--env-file",
            str(env_path),
            "--output-dir",
            str(tmp_path / "reports"),
            "--level",
            "502",
            "--protocol",
            "circuit-v2",
            "--learning-activity",
            "near-solved",
        ],
        client_factory=lambda _config: client,
        print_fn=lambda _message: None,
    )

    assert exit_code == 0
    assert client.requests[0].learning_activity.kind == "three_input_carry"
    assert client.requests[0].learning_activity.current_decimal == 2


@pytest.mark.asyncio
async def test_run_cli_returns_nonzero_but_still_writes_report_on_turn_failure(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TUCO_LLM_MODEL=test-model\nTUCO_LLM_API_KEY=test-key\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "reports"
    client = FakeDecisionClient(failing_level=102)

    exit_code = await run_cli(
        [
            "--env-file",
            str(env_path),
            "--output-dir",
            str(output_dir),
            "--levels",
            "101,102",
        ],
        client_factory=lambda _config: client,
        print_fn=lambda _message: None,
    )

    assert exit_code == 1
    assert client.closed is True
    assert len(list(output_dir.glob("*.json"))) == 1
    assert len(list(output_dir.glob("*.md"))) == 1
