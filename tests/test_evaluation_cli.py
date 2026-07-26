from pathlib import Path

import pytest

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
    args = parse_args(["--circuit-setup", "placed-io"])

    assert args.circuit_setup == "placed-io"


def test_parse_args_accepts_circuit_v2_protocol() -> None:
    args = parse_args(["--protocol", "circuit-v2"])

    assert args.protocol == "circuit-v2"


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
