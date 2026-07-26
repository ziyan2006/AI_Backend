from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.evaluation import (
    LEVEL_EVAL_CASES,
    LevelEvalCase,
    run_concurrent_evaluation,
    write_evaluation_report,
)
from tuco_ai_backend.providers.openai_compatible import OpenAICompatibleClient

DEFAULT_QUESTION = "这关要做什么？"
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
        description="并发评测全部关卡的文字 LLM 链路，并生成 JSON 和 Markdown 报告。"
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
        "--levels",
        help="只评测指定关卡，使用逗号分隔，例如 301,302；默认评测全部关卡。",
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
    return build_parser().parse_args(argv)


def select_level_cases(levels: str | None) -> tuple[LevelEvalCase, ...]:
    if levels is None or not levels.strip():
        return LEVEL_EVAL_CASES

    requested_ids: set[int] = set()
    for raw_level_id in levels.split(","):
        value = raw_level_id.strip()
        if not value:
            continue
        try:
            requested_ids.add(int(value))
        except ValueError as exc:
            raise ValueError(f"无效关卡编号：{value}") from exc

    if not requested_ids:
        raise ValueError("至少需要指定一个关卡编号")

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
    try:
        cases = select_level_cases(args.levels)
    except ValueError as exc:
        print_fn(f"参数错误：{exc}")
        return 2

    questions = args.questions or [DEFAULT_QUESTION]
    settings = Settings(_env_file=args.env_file)
    config = RuntimeConfigStore(settings, env_path=args.env_file)
    client = client_factory(config)

    print_fn(
        f"开始评测 {len(cases)} 个关卡，共 {len(questions)} 轮问题，"
        f"并发数 {args.concurrency}，模型 {config.model}。"
    )
    try:
        report = await run_concurrent_evaluation(
            client,
            cases=cases,
            questions=questions,
            concurrency=args.concurrency,
            model=config.model,
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
