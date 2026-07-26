from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from tuco_ai_backend.models import CircuitSnapshot, DecisionRequest, DecisionResponse, LevelContext


@dataclass(frozen=True)
class LevelEvalCase:
    level_id: int
    title: str
    short_goal: str
    input_names: str
    output_names: str
    input_count: int
    output_count: int

    def level_context(self) -> LevelContext:
        return LevelContext(
            level_id=self.level_id,
            short_goal=self.short_goal,
            input_names=self.input_names,
            output_names=self.output_names,
            input_count=self.input_count,
            output_count=self.output_count,
        )


LEVEL_EVAL_CASES = (
    LevelEvalCase(
        101,
        "启动飞船",
        "总电门只需要把输入 A 直连到输出 Y 即可恢复主控台供电（直连导通）。",
        "总电门 A",
        "主控台供电 Y",
        1,
        1,
    ),
    LevelEvalCase(
        102,
        "与非门",
        "合成与非门（NAND）：只有两个输入同为 1 时才会切断输出。",
        "通道开关 A, B",
        "主电网输出 Y",
        2,
        1,
    ),
    LevelEvalCase(
        103,
        "反向激光",
        "合成非门（NOT）：输入 0 时输出 1 发射激光击碎陨石。",
        "发射按键 A",
        "激光炮输出 Y",
        1,
        1,
    ),
    LevelEvalCase(
        201,
        "双重密码",
        "合成与门（AND）：只有两人同时按下开锁指纹（输入同为 1）时大门才会打开。",
        "指纹开关 A, B",
        "驾驶舱大门 Y",
        2,
        1,
    ),
    LevelEvalCase(
        202,
        "备用线路",
        "合成或门（OR）：主管道或备用管道任意一条通电（任意输入为 1）即可供氧。",
        "主管道 A, 备用管道 B",
        "应急供氧 Y",
        2,
        1,
    ),
    LevelEvalCase(
        203,
        "能量护罩",
        "合成或非门（NOR）：只有两个方向都没有危险（输入同为 0）时，护罩才维持开启。",
        "左/右威胁传感器",
        "能量护罩 Y",
        2,
        1,
    ),
    LevelEvalCase(
        301,
        "互斥钥匙",
        "合成异或门（XOR）：只有两个输入电平互斥（一开一关，输入不同）时才开放密码钥匙。",
        "密码开关 A, B",
        "星门钥匙 Y",
        2,
        1,
    ),
    LevelEvalCase(
        302,
        "雷达对接",
        "合成同或门（XNOR）：两侧天线信号完全同步时才能完成对接。",
        "天线射频 A, B",
        "对接锁定 Y",
        2,
        1,
    ),
    LevelEvalCase(
        401,
        "求和电平",
        "计算二进制加法个位求和（异或门逻辑）。",
        "加数 A, B",
        "个位求和 Sum",
        2,
        1,
    ),
    LevelEvalCase(
        402,
        "进位警报",
        "只有两个加数同为 1 时，才产生向高位的进位 1（与门逻辑）。",
        "加数 A, B",
        "高位进位 Carry",
        2,
        1,
    ),
    LevelEvalCase(
        403,
        "半加引擎",
        "组合个位求和与高位进位，实现完整的半加器电路。",
        "加数 A, B",
        "Sum, Carry",
        2,
        2,
    ),
    LevelEvalCase(
        501,
        "三路求和",
        "全加器个位求和：输入 1 的个数为奇数个时个位输出 1。",
        "加数 A, B, C",
        "个位 Sum",
        3,
        1,
    ),
    LevelEvalCase(
        502,
        "局部进位",
        "任意 2 个或 2 个以上输入同为 1 时，产生局部进位。",
        "加数 A, B, C",
        "局部进位",
        3,
        1,
    ),
    LevelEvalCase(
        503,
        "进位汇聚",
        "只要有任意一路局部溢出，最终进位输出 1。",
        "局部进位 A, B, C",
        "最终 Carry",
        3,
        1,
    ),
    LevelEvalCase(
        504,
        "图灵主控",
        "组合三路求和与进位汇聚，实现三位二进制全加器。",
        "加数 A, B, 进位 C",
        "Sum, Carry",
        3,
        2,
    ),
    LevelEvalCase(
        601,
        "信号分流",
        "信号选择器：控制信号选择选通 A 频道或 B 频道。",
        "Select, A, B",
        "选通输出 Y",
        3,
        1,
    ),
    LevelEvalCase(
        602,
        "指令翻译",
        "二转四译码器：2 位二进制选择独热选通 4 个舱室之一。",
        "指令 A, B",
        "舱室 1, 2, 3, 4",
        2,
        4,
    ),
)


class DecisionClient(Protocol):
    async def decide(
        self,
        request: DecisionRequest,
        history: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
    ) -> DecisionResponse: ...


@dataclass
class TurnEvaluationResult:
    question: str
    trace_id: str
    duration_ms: int
    assistant_text: str | None = None
    tool_call: dict[str, Any] | None = None
    topology_revision: int | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error_type is None


@dataclass
class LevelEvaluationResult:
    session_id: str
    level_id: int
    title: str
    duration_ms: int
    turns: list[TurnEvaluationResult]

    @property
    def succeeded(self) -> bool:
        return all(turn.succeeded for turn in self.turns)


@dataclass
class EvaluationReport:
    run_id: str
    model: str
    concurrency: int
    questions: list[str]
    started_at: str
    completed_at: str
    duration_ms: int
    levels: list[LevelEvaluationResult]

    @property
    def total_turns(self) -> int:
        return sum(len(level.turns) for level in self.levels)

    @property
    def successful_turns(self) -> int:
        return sum(turn.succeeded for level in self.levels for turn in level.turns)

    @property
    def failed_turns(self) -> int:
        return self.total_turns - self.successful_turns

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["summary"] = {
            "level_count": len(self.levels),
            "total_turns": self.total_turns,
            "successful_turns": self.successful_turns,
            "failed_turns": self.failed_turns,
        }
        return payload


def build_empty_circuit(case: LevelEvalCase) -> CircuitSnapshot:
    return CircuitSnapshot(
        schema_version=3,
        play_active=True,
        generation=0,
        topology_revision=0,
        level=case.level_context(),
        slots=[{"slot": slot, "present": False} for slot in range(16)],
        port_roles=[0] * 64,
        links=[],
        link_count=0,
        ignored_link_count=0,
        invalid_link_count=0,
        link_overflow=False,
    )


async def _evaluate_level(
    client: DecisionClient,
    case: LevelEvalCase,
    questions: Sequence[str],
    semaphore: asyncio.Semaphore,
    run_id: str,
) -> LevelEvaluationResult:
    session_id = f"{run_id}-level-{case.level_id}"
    level_started = perf_counter()
    history: list[dict[str, str]] = []
    turns: list[TurnEvaluationResult] = []
    circuit = build_empty_circuit(case)

    async with semaphore:
        for turn_index, question in enumerate(questions, start=1):
            trace_id = f"{run_id}-l{case.level_id}-t{turn_index}"
            turn_started = perf_counter()
            try:
                decision = await client.decide(
                    DecisionRequest(question=question, circuit=circuit),
                    history=[dict(message) for message in history],
                    trace_id=trace_id,
                )
                assistant_text = decision.assistant_text
                turns.append(
                    TurnEvaluationResult(
                        question=question,
                        trace_id=trace_id,
                        duration_ms=round((perf_counter() - turn_started) * 1000),
                        assistant_text=assistant_text,
                        tool_call=(
                            decision.tool_call.model_dump(mode="json")
                            if decision.tool_call is not None
                            else None
                        ),
                        topology_revision=decision.topology_revision,
                    )
                )
                if assistant_text:
                    history.extend(
                        [
                            {"role": "user", "content": question},
                            {"role": "assistant", "content": assistant_text},
                        ]
                    )
                    history = history[-10:]
            except Exception as exc:
                turns.append(
                    TurnEvaluationResult(
                        question=question,
                        trace_id=trace_id,
                        duration_ms=round((perf_counter() - turn_started) * 1000),
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                )

    return LevelEvaluationResult(
        session_id=session_id,
        level_id=case.level_id,
        title=case.title,
        duration_ms=round((perf_counter() - level_started) * 1000),
        turns=turns,
    )


async def run_concurrent_evaluation(
    client: DecisionClient,
    *,
    cases: Sequence[LevelEvalCase] = LEVEL_EVAL_CASES,
    questions: Sequence[str] = ("这关要做什么？",),
    concurrency: int = 4,
    model: str = "unknown",
    run_id: str | None = None,
) -> EvaluationReport:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    cleaned_questions = tuple(question.strip() for question in questions if question.strip())
    if not cleaned_questions:
        raise ValueError("at least one non-empty question is required")

    active_run_id = run_id or datetime.now(UTC).strftime("eval-%Y%m%dT%H%M%SZ")
    started_at = datetime.now(UTC)
    started = perf_counter()
    semaphore = asyncio.Semaphore(concurrency)
    levels = await asyncio.gather(
        *(
            _evaluate_level(client, case, cleaned_questions, semaphore, active_run_id)
            for case in cases
        )
    )
    completed_at = datetime.now(UTC)
    return EvaluationReport(
        run_id=active_run_id,
        model=model,
        concurrency=concurrency,
        questions=list(cleaned_questions),
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_ms=round((perf_counter() - started) * 1000),
        levels=sorted(levels, key=lambda level: level.level_id),
    )


def _markdown_text(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def render_markdown_report(report: EvaluationReport) -> str:
    lines = [
        "# LLM 全关卡并发评测",
        "",
        f"- 运行 ID：`{report.run_id}`",
        f"- 模型：`{report.model}`",
        f"- 并发数：`{report.concurrency}`",
        f"- 关卡数：`{len(report.levels)}`",
        f"- 成功轮次：`{report.successful_turns}/{report.total_turns}`",
        f"- 总耗时：`{report.duration_ms} ms`",
        "",
        "## 概览",
        "",
        "| 关卡 | 标题 | 状态 | 耗时 | 首条回复 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for level in report.levels:
        first_turn = level.turns[0] if level.turns else None
        first_response = first_turn.assistant_text if first_turn else None
        status = "成功" if level.succeeded else "失败"
        lines.append(
            f"| {level.level_id} | {level.title} | {status} | {level.duration_ms} ms | "
            f"{_markdown_text(first_response)} |"
        )

    for level in report.levels:
        lines.extend(["", f"## 第 {level.level_id} 关：{level.title}", ""])
        for turn_index, turn in enumerate(level.turns, start=1):
            lines.extend(
                [
                    f"### 第 {turn_index} 轮",
                    "",
                    f"- 提问：{turn.question}",
                    f"- Trace：`{turn.trace_id}`",
                    f"- 耗时：`{turn.duration_ms} ms`",
                ]
            )
            if turn.succeeded:
                lines.extend(
                    [
                        "",
                        "**模型回复**",
                        "",
                        turn.assistant_text or "（无文本回复）",
                    ]
                )
                if turn.tool_call is not None:
                    lines.extend(
                        [
                            "",
                            "**工具调用**",
                            "",
                            "```json",
                            json.dumps(turn.tool_call, ensure_ascii=False, indent=2),
                            "```",
                        ]
                    )
            else:
                lines.extend(
                    [
                        "",
                        f"**错误：** `{turn.error_type}: {turn.error_message}`",
                    ]
                )
    return "\n".join(lines) + "\n"


def write_evaluation_report(
    report: EvaluationReport, output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    markdown_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, markdown_path
