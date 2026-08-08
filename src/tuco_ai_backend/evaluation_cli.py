from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.evaluation import (
    CIRCUIT_PROTOCOLS,
    CIRCUIT_SETUPS,
    LEARNING_ACTIVITY_SETUPS,
    LEVEL_EVAL_CASES,
    LevelEvalCase,
    run_concurrent_evaluation,
    run_conversation_scenarios,
    write_conversation_evaluation_report,
    write_evaluation_report,
)
from tuco_ai_backend.evaluation_presets import load_conversation_presets
from tuco_ai_backend.evaluation_scenarios import load_conversation_scenarios
from tuco_ai_backend.evaluation_tracing import EvaluationTraceCollector
from tuco_ai_backend.providers.circuit_coach_v2 import CircuitCoachV2Client
from tuco_ai_backend.providers.openai_compatible import OpenAICompatibleClient

DEFAULT_QUESTION = "这关要做什么？"
DEFAULT_PLACED_IO_QUESTION = "接下来应该怎么做？给我点提示"
DEFAULT_OUTPUT_DIR = Path("runtime/llm_evaluations")


class ClosableDecisionClient(Protocol):
    async def close(self) -> None: ...


ClientFactory = Callable[[RuntimeConfigStore], ClosableDecisionClient]


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须大于等于 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="并发评测指定关卡的文字 LLM 链路，并生成 JSON 和 Markdown 报告。"
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=4,
        help="同时评测的关卡数，默认 4。",
    )
    parser.add_argument(
        "--question",
        dest="questions",
        action="append",
        help="发送给每个关卡的问题，可重复传入；默认仅发送“这关要做什么？”。",
    )
    parser.add_argument(
        "--conversation-scenario",
        dest="conversation_scenarios",
        type=Path,
        action="append",
        help="运行标准多轮场景 JSON，可重复传入。",
    )
    parser.add_argument(
        "--conversation-preset",
        dest="conversation_presets",
        action="append",
        help="运行内置多轮评测预设，例如 502-guidance-quality，可重复传入。",
    )
    parser.add_argument(
        "--levels",
        help="指定关卡编号，使用逗号分隔，例如 301,302；可与 --level 合用。",
    )
    parser.add_argument(
        "--level",
        dest="level_ids",
        type=int,
        action="append",
        metavar="ID",
        help="指定一个关卡编号，可重复传入，例如 --level 301 --level 302。",
    )
    parser.add_argument(
        "--list-levels",
        action="store_true",
        help="列出可评测关卡后退出，不读取模型配置。",
    )
    parser.add_argument(
        "--circuit-setup",
        choices=CIRCUIT_SETUPS,
        default="empty",
        help=(
            "电路初始状态：empty 为未摆放积木，placed-io 为已摆好关卡要求的输入/输出积木，"
            "actionable-logic 为另放一块已解锁但未接线的逻辑门；"
            "默认 empty。"
        ),
    )
    parser.add_argument(
        "--protocol",
        choices=CIRCUIT_PROTOCOLS,
        default="legacy",
        help="快照与工具协议：legacy 为旧测试台协议，circuit-v2 为新固件协议。",
    )
    parser.add_argument(
        "--learning-activity",
        choices=LEARNING_ACTIVITY_SETUPS,
        default="none",
        help=(
            "概念练习状态：none 为普通电路评测；其它值模拟活动页的未完成、接近完成或已完成状态。"
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="LLM 配置文件路径，默认 .env。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"报告输出目录，默认 {DEFAULT_OUTPUT_DIR.as_posix()}。",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_argv)
    args.explicit_options = frozenset(
        item.split("=", 1)[0] for item in raw_argv if item.startswith("--")
    )
    return args


def select_level_cases(
    levels: str | None,
    level_ids: Sequence[int] | None = None,
) -> tuple[LevelEvalCase, ...]:
    requested_ids = set(level_ids or [])
    if levels is not None and levels.strip():
        for raw_level_id in levels.split(","):
            value = raw_level_id.strip()
            if not value:
                continue
            try:
                requested_ids.add(int(value))
            except ValueError as exc:
                raise ValueError(f"无效关卡编号：{value}") from exc
    elif levels is not None and not requested_ids:
        raise ValueError("至少需要指定一个关卡编号")

    if not requested_ids:
        return LEVEL_EVAL_CASES

    known_ids = {case.level_id for case in LEVEL_EVAL_CASES}
    unknown_ids = sorted(requested_ids - known_ids)
    if unknown_ids:
        unknown_text = ", ".join(str(level_id) for level_id in unknown_ids)
        raise ValueError(f"未知关卡编号：{unknown_text}")

    return tuple(case for case in LEVEL_EVAL_CASES if case.level_id in requested_ids)


async def run_cli(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = OpenAICompatibleClient,
    print_fn: Callable[[str], None] = print,
) -> int:
    args = parse_args(argv)
    if args.list_levels:
        print_fn("可评测关卡：")
        for case in LEVEL_EVAL_CASES:
            print_fn(f"{case.level_id}：{case.title}")
        return 0

    if args.conversation_scenarios or args.conversation_presets:
        conflicting_options = sorted(
            args.explicit_options
            & {
                "--level",
                "--levels",
                "--question",
                "--circuit-setup",
                "--protocol",
                "--learning-activity",
            }
        )
        if conflicting_options:
            print_fn(
                "参数错误：--conversation-scenario 不能与 "
                + "、".join(conflicting_options)
                + " 同时使用"
            )
            return 2
        try:
            scenarios = (
                *load_conversation_scenarios(args.conversation_scenarios or []),
                *load_conversation_presets(args.conversation_presets or []),
            )
            names = [loaded.scenario.name for loaded in scenarios]
            if len(names) != len(set(names)):
                raise ValueError("duplicate conversation scenario name")
        except ValueError as exc:
            print_fn(f"参数错误：{exc}")
            return 2

        settings = Settings(_env_file=args.env_file)
        config = RuntimeConfigStore(settings, env_path=args.env_file)
        trace_collector = EvaluationTraceCollector()
        client = (
            CircuitCoachV2Client(config, trace_sink=trace_collector)
            if client_factory is OpenAICompatibleClient
            else client_factory(config)
        )
        print_fn(
            f"开始评测 {len(scenarios)} 个多轮场景，并发数 {args.concurrency}，"
            f"协议 circuit-v2，模型 {config.model}。"
        )
        try:
            report = await run_conversation_scenarios(
                client,
                scenarios=scenarios,
                concurrency=args.concurrency,
                model=config.model,
                trace_collector=trace_collector,
            )
            json_path, markdown_path = write_conversation_evaluation_report(
                report,
                args.output_dir,
            )
        finally:
            await client.close()

        successful_turns = report.total_turns - report.failed_turns
        print_fn(
            f"评测完成：成功 {successful_turns}，失败 {report.failed_turns}，"
            f"耗时 {report.duration_ms} ms。"
        )
        print_fn(f"JSON 报告：{json_path.resolve()}")
        print_fn(f"Markdown 报告：{markdown_path.resolve()}")
        return 1 if report.failed_turns else 0

    try:
        cases = select_level_cases(args.levels, args.level_ids)
        if args.learning_activity != "none":
            activity_level_ids = {401, 403, 501, 502, 503}
            unsupported = [
                case.level_id for case in cases if case.level_id not in activity_level_ids
            ]
            if unsupported:
                raise ValueError("学习活动仅支持关卡：401、403、501、502、503")
            if args.protocol != "circuit-v2":
                raise ValueError("学习活动评测必须使用 circuit-v2 协议")
        if args.circuit_setup == "actionable-logic" and args.protocol != "circuit-v2":
            raise ValueError("actionable-logic 评测必须使用 circuit-v2 协议")
    except ValueError as exc:
        print_fn(f"参数错误：{exc}")
        return 2

    questions = args.questions or [
        DEFAULT_PLACED_IO_QUESTION if args.circuit_setup != "empty" else DEFAULT_QUESTION
    ]
    settings = Settings(_env_file=args.env_file)
    config = RuntimeConfigStore(settings, env_path=args.env_file)
    trace_collector = (
        EvaluationTraceCollector() if args.protocol == "circuit-v2" else None
    )
    client = (
        CircuitCoachV2Client(config, trace_sink=trace_collector)
        if args.protocol == "circuit-v2" and client_factory is OpenAICompatibleClient
        else client_factory(config)
    )

    print_fn(
        f"开始评测 {len(cases)} 个关卡，共 {len(questions)} 轮问题，"
        f"并发数 {args.concurrency}，电路状态 {args.circuit_setup}，协议 {args.protocol}，"
        f"活动状态 {args.learning_activity}，模型 {config.model}。"
    )
    try:
        report = await run_concurrent_evaluation(
            client,
            cases=cases,
            questions=questions,
            concurrency=args.concurrency,
            circuit_setup=args.circuit_setup,
            circuit_protocol=args.protocol,
            learning_activity_setup=args.learning_activity,
            model=config.model,
            trace_collector=trace_collector,
        )
        json_path, markdown_path = write_evaluation_report(report, args.output_dir)
    finally:
        await client.close()

    print_fn(
        f"评测完成：成功 {report.successful_turns}，失败 {report.failed_turns}，"
        f"耗时 {report.duration_ms} ms。"
    )
    print_fn(f"JSON 报告：{json_path.resolve()}")
    print_fn(f"Markdown 报告：{markdown_path.resolve()}")
    return 1 if report.failed_turns else 0


def main() -> None:
    raise SystemExit(asyncio.run(run_cli()))


if __name__ == "__main__":
    main()
