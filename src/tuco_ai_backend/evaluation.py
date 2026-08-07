from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol

from tuco_ai_backend.evaluation_scenarios import (
    ConversationEvaluationReport,
    LoadedConversationScenario,
    ScenarioEvaluationResult,
    ScenarioTurnEvaluationResult,
    summarize_snapshot_change,
)
from tuco_ai_backend.evaluation_tracing import EvaluationTraceCollector
from tuco_ai_backend.level_logic import get_level_logic_spec
from tuco_ai_backend.models import (
    CircuitCoachDecisionRequest,
    CircuitCoachV2Board,
    CircuitCoachV2Level,
    CircuitCoachV2Port,
    CircuitCoachV2Slot,
    CircuitCoachV2Snapshot,
    CircuitSnapshot,
    DecisionRequest,
    DecisionResponse,
    LearningActivityContext,
    LevelContext,
)
from tuco_ai_backend.providers.openai_compatible import available_gate_components_for_level


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


CircuitSetup = Literal["empty", "placed-io", "actionable-logic"]
CIRCUIT_SETUPS: tuple[CircuitSetup, ...] = ("empty", "placed-io", "actionable-logic")
CircuitProtocol = Literal["legacy", "circuit-v2"]
CIRCUIT_PROTOCOLS: tuple[CircuitProtocol, ...] = ("legacy", "circuit-v2")
LearningActivitySetup = Literal["none", "unsolved", "near-solved", "solved"]
LEARNING_ACTIVITY_SETUPS: tuple[LearningActivitySetup, ...] = (
    "none",
    "unsolved",
    "near-solved",
    "solved",
)


class DecisionClient(Protocol):
    async def decide(
        self,
        request: Any,
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
    route_mode: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error_type is None


def _route_mode_from_trace(trace: dict[str, Any]) -> str | None:
    route = trace.get("route_decision")
    if not isinstance(route, dict):
        return None
    mode = route.get("mode")
    return mode if isinstance(mode, str) else None


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
    circuit_setup: CircuitSetup
    circuit_protocol: CircuitProtocol
    learning_activity_setup: LearningActivitySetup
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


def build_required_io_circuit(case: LevelEvalCase) -> CircuitSnapshot:
    slots: list[dict[str, Any]] = []
    port_roles = [0] * 64
    component_slots = case.input_count + case.output_count

    for slot in range(16):
        if slot < case.input_count:
            slots.append(
                {"slot": slot, "present": True, "id_valid": True, "raw_id": 0xF0, "gate": 0}
            )
            port_roles[slot * 4 : slot * 4 + 4] = [2] * 4
        elif slot < component_slots:
            slots.append(
                {"slot": slot, "present": True, "id_valid": True, "raw_id": 0xF1, "gate": 1}
            )
            local_port = 2 if slot % 2 == 0 else 0
            port_roles[slot * 4 + local_port] = 1
        else:
            slots.append({"slot": slot, "present": False})

    return CircuitSnapshot(
        schema_version=3,
        play_active=True,
        generation=0,
        topology_revision=component_slots,
        level=case.level_context(),
        slots=slots,
        port_roles=port_roles,
        links=[],
        link_count=0,
        ignored_link_count=0,
        invalid_link_count=0,
        link_overflow=False,
    )


def build_circuit(case: LevelEvalCase, circuit_setup: CircuitSetup) -> CircuitSnapshot:
    if circuit_setup == "empty":
        return build_empty_circuit(case)
    if circuit_setup == "placed-io":
        return build_required_io_circuit(case)
    if circuit_setup == "actionable-logic":
        raise ValueError("actionable-logic is only available with circuit-v2")
    raise ValueError(f"unsupported circuit setup: {circuit_setup}")


def _firmware_gate_name(component: str) -> str:
    return component.removesuffix("_gate").upper()


def _actionable_gate_name(case: LevelEvalCase) -> str:
    for component in available_gate_components_for_level(case.level_id):
        if component not in {"input", "output"}:
            return _firmware_gate_name(component)
    raise ValueError(
        f"level {case.level_id} does not unlock a logic gate for actionable evaluation"
    )


def build_circuit_coach_v2(
    case: LevelEvalCase, circuit_setup: CircuitSetup
) -> CircuitCoachV2Snapshot:
    if circuit_setup not in CIRCUIT_SETUPS:
        raise ValueError(f"unsupported circuit setup: {circuit_setup}")

    rule_spec = get_level_logic_spec(case.level_id, 1)
    component_slots = case.input_count + case.output_count
    actionable_gate = (
        _actionable_gate_name(case) if circuit_setup == "actionable-logic" else None
    )
    slots: list[CircuitCoachV2Slot] = []
    for slot_id in range(16):
        state = "empty"
        gate: str | None = None
        ports = [
            CircuitCoachV2Port(port_id=slot_id * 4 + port, side="unused", role="unused")
            for port in range(4)
        ]
        if circuit_setup in {"placed-io", "actionable-logic"} and slot_id < case.input_count:
            state = "present"
            gate = "INPUT"
            ports[0] = CircuitCoachV2Port(port_id=slot_id * 4, side="right", role="output")
        elif circuit_setup in {"placed-io", "actionable-logic"} and slot_id < component_slots:
            state = "present"
            gate = "OUTPUT"
            ports[0] = CircuitCoachV2Port(port_id=slot_id * 4, side="left", role="input")
        elif circuit_setup == "actionable-logic" and slot_id == 12:
            state = "present"
            gate = actionable_gate
            ports[0] = CircuitCoachV2Port(port_id=slot_id * 4, side="right", role="output")
            ports[3] = CircuitCoachV2Port(port_id=slot_id * 4 + 3, side="up", role="input")
            if actionable_gate != "NOT":
                ports[1] = CircuitCoachV2Port(
                    port_id=slot_id * 4 + 1, side="down", role="input"
                )
        slots.append(
            CircuitCoachV2Slot(
                slot_id=slot_id,
                row=slot_id // 4,
                column=slot_id % 4,
                state=state,
                gate=gate,
                ports=ports,
            )
        )

    return CircuitCoachV2Snapshot(
        schema="tuco_circuit_v2",
        level=CircuitCoachV2Level(
            id=case.level_id,
            rule_version=rule_spec.rule_version,
            goal=case.short_goal,
            inputs=case.input_names,
            outputs=case.output_names,
            input_count=case.input_count,
            output_count=case.output_count,
        ),
        unlocked_gates=list(
            dict.fromkeys(
                [
                    "INPUT",
                    "OUTPUT",
                    *(
                        _firmware_gate_name(component)
                        for component in available_gate_components_for_level(case.level_id)
                    ),
                ]
            )
        ),
        board=CircuitCoachV2Board(
            topology_revision=(
                component_slots + 1 if circuit_setup == "actionable-logic" else component_slots
            )
            if circuit_setup == "placed-io"
            else 0,
            slots=slots,
            edges=[],
        ),
    )


def build_learning_activity_context(
    level_id: int, setup: LearningActivitySetup
) -> LearningActivityContext | None:
    if setup == "none":
        return None
    activity_definitions = {
        401: {
            "kind": "binary_slots",
            "slot_roles": ["8", "4", "2", "1"],
            "slot_weights": [8, 4, 2, 1],
            "target_bits": [0, 1, 1, 0],
            "target_decimal": 6,
        },
        403: {
            "kind": "half_adder",
            "slot_roles": ["A", "B", "个位", "进位"],
            "slot_weights": None,
            "target_bits": [0, 1, 1, 0],
            "target_decimal": None,
        },
        501: {
            "kind": "three_input_parity",
            "slot_roles": ["A", "B", "进位输入"],
            "slot_weights": None,
            "target_bits": [1, 0, 0],
            "target_decimal": 1,
        },
        502: {
            "kind": "three_input_carry",
            "slot_roles": ["A", "B", "进位输入"],
            "slot_weights": None,
            "target_bits": [1, 0, 0],
            "target_decimal": 0,
        },
        504: {
            "kind": "full_adder",
            "slot_roles": ["A", "B", "进位输入", "个位", "进位输出"],
            "slot_weights": None,
            "target_bits": [1, 0, 0, 1, 0],
            "target_decimal": None,
        },
    }
    definition = activity_definitions.get(level_id)
    if definition is None:
        raise ValueError(f"level {level_id} does not have a learning activity")
    target_bits = definition["target_bits"]
    if setup == "unsolved":
        slot_bits = [0] * len(target_bits)
    elif setup == "near-solved":
        slot_bits = list(target_bits)
        slot_bits[-1] = 1 - slot_bits[-1]
    else:
        slot_bits = list(target_bits)
    weights = definition["slot_weights"]
    current_decimal = (
        sum(weight * bit for weight, bit in zip(weights, slot_bits, strict=True))
        if weights is not None
        else sum(slot_bits)
        if definition["kind"] in {"three_input_parity", "three_input_carry"}
        else None
    )
    return LearningActivityContext(
        kind=definition["kind"],
        stage="practice",
        round_index=1,
        round_total=3,
        slot_roles=definition["slot_roles"],
        slot_weights=weights,
        slot_bits=slot_bits,
        target_bits=target_bits,
        target_decimal=definition["target_decimal"],
        current_decimal=current_decimal,
        solved=setup == "solved",
        complete=False,
    )


def _build_evaluation_request(
    case: LevelEvalCase,
    *,
    session_id: str,
    question: str,
    circuit_setup: CircuitSetup,
    circuit_protocol: CircuitProtocol,
    learning_activity_setup: LearningActivitySetup,
) -> DecisionRequest | CircuitCoachDecisionRequest:
    if learning_activity_setup != "none" and circuit_protocol != "circuit-v2":
        raise ValueError("learning activity evaluation requires the circuit-v2 protocol")
    if circuit_setup == "actionable-logic" and circuit_protocol != "circuit-v2":
        raise ValueError("actionable-logic evaluation requires the circuit-v2 protocol")
    if circuit_protocol == "legacy":
        return DecisionRequest(question=question, circuit=build_circuit(case, circuit_setup))
    return CircuitCoachDecisionRequest(
        session_id=session_id,
        user_text=question,
        circuit_snapshot=build_circuit_coach_v2(case, circuit_setup),
        learning_activity=build_learning_activity_context(case.level_id, learning_activity_setup),
    )


async def _evaluate_level(
    client: DecisionClient,
    case: LevelEvalCase,
    questions: Sequence[str],
    semaphore: asyncio.Semaphore,
    run_id: str,
    circuit_setup: CircuitSetup,
    circuit_protocol: CircuitProtocol,
    learning_activity_setup: LearningActivitySetup,
    trace_collector: EvaluationTraceCollector | None,
) -> LevelEvaluationResult:
    session_id = f"{run_id}-level-{case.level_id}"
    level_started = perf_counter()
    history: list[dict[str, str]] = []
    turns: list[TurnEvaluationResult] = []
    async with semaphore:
        for turn_index, question in enumerate(questions, start=1):
            trace_id = f"{run_id}-l{case.level_id}-t{turn_index}"
            turn_started = perf_counter()
            try:
                decision = await client.decide(
                    _build_evaluation_request(
                        case,
                        session_id=session_id,
                        question=question,
                        circuit_setup=circuit_setup,
                        circuit_protocol=circuit_protocol,
                        learning_activity_setup=learning_activity_setup,
                    ),
                    history=[dict(message) for message in history],
                    trace_id=trace_id,
                )
                assistant_text = decision.assistant_text
                trace = trace_collector.take(trace_id) if trace_collector is not None else {}
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
                        route_mode=_route_mode_from_trace(trace),
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
                trace = trace_collector.take(trace_id) if trace_collector is not None else {}
                turns.append(
                    TurnEvaluationResult(
                        question=question,
                        trace_id=trace_id,
                        duration_ms=round((perf_counter() - turn_started) * 1000),
                        route_mode=_route_mode_from_trace(trace),
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
    circuit_setup: CircuitSetup = "empty",
    circuit_protocol: CircuitProtocol = "legacy",
    learning_activity_setup: LearningActivitySetup = "none",
    model: str = "unknown",
    run_id: str | None = None,
    trace_collector: EvaluationTraceCollector | None = None,
) -> EvaluationReport:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if circuit_setup not in CIRCUIT_SETUPS:
        raise ValueError(f"unsupported circuit setup: {circuit_setup}")
    if circuit_protocol not in CIRCUIT_PROTOCOLS:
        raise ValueError(f"unsupported circuit protocol: {circuit_protocol}")
    if learning_activity_setup not in LEARNING_ACTIVITY_SETUPS:
        raise ValueError(f"unsupported learning activity setup: {learning_activity_setup}")
    if learning_activity_setup != "none" and circuit_protocol != "circuit-v2":
        raise ValueError("learning activity evaluation requires the circuit-v2 protocol")
    if circuit_setup == "actionable-logic" and circuit_protocol != "circuit-v2":
        raise ValueError("actionable-logic evaluation requires the circuit-v2 protocol")
    cleaned_questions = tuple(question.strip() for question in questions if question.strip())
    if not cleaned_questions:
        raise ValueError("at least one non-empty question is required")

    active_run_id = run_id or datetime.now(UTC).strftime("eval-%Y%m%dT%H%M%SZ")
    started_at = datetime.now(UTC)
    started = perf_counter()
    semaphore = asyncio.Semaphore(concurrency)
    levels = await asyncio.gather(
        *(
            _evaluate_level(
                client,
                case,
                cleaned_questions,
                semaphore,
                active_run_id,
                circuit_setup,
                circuit_protocol,
                learning_activity_setup,
                trace_collector,
            )
            for case in cases
        )
    )
    completed_at = datetime.now(UTC)
    return EvaluationReport(
        run_id=active_run_id,
        model=model,
        concurrency=concurrency,
        circuit_setup=circuit_setup,
        circuit_protocol=circuit_protocol,
        learning_activity_setup=learning_activity_setup,
        questions=list(cleaned_questions),
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_ms=round((perf_counter() - started) * 1000),
        levels=sorted(levels, key=lambda level: level.level_id),
    )


async def _evaluate_conversation_scenario(
    client: DecisionClient,
    loaded: LoadedConversationScenario,
    semaphore: asyncio.Semaphore,
    run_id: str,
    scenario_index: int,
    trace_collector: EvaluationTraceCollector | None,
) -> ScenarioEvaluationResult:
    scenario = loaded.scenario
    session_id = f"{run_id}-scenario-{scenario_index}-{scenario.level_id}"
    started = perf_counter()
    history: list[dict[str, str]] = []
    turns: list[ScenarioTurnEvaluationResult] = []
    async with semaphore:
        for turn_index, turn in enumerate(scenario.turns, start=1):
            trace_id = f"{run_id}-s{scenario_index}-t{turn_index}"
            turn_started = perf_counter()
            history_before_turn = [dict(item) for item in history]
            try:
                decision = await client.decide(
                    CircuitCoachDecisionRequest(
                        session_id=session_id,
                        user_text=turn.user_text,
                        interaction_intent=turn.interaction_intent,
                        direct_hint_requested=turn.direct_hint_requested,
                        circuit_snapshot=turn.snapshot,
                    ),
                    history=history_before_turn,
                    trace_id=trace_id,
                )
                trace = trace_collector.take(trace_id) if trace_collector is not None else {}
                turns.append(
                    ScenarioTurnEvaluationResult(
                        turn_index=turn_index,
                        trace_id=trace_id,
                        user_text=turn.user_text,
                        interaction_intent=turn.interaction_intent,
                        direct_hint_requested=turn.direct_hint_requested,
                        note=turn.note,
                        snapshot=turn.snapshot.model_dump(mode="json", by_alias=True),
                        history_before_turn=history_before_turn,
                        duration_ms=round((perf_counter() - turn_started) * 1000),
                        assistant_text=decision.assistant_text,
                        tool_call=(
                            decision.tool_call.model_dump(mode="json")
                            if decision.tool_call is not None
                            else None
                        ),
                        topology_revision=decision.topology_revision,
                        route_mode=_route_mode_from_trace(trace),
                        trace=trace,
                    )
                )
                if decision.assistant_text:
                    history.extend(
                        [
                            {"role": "user", "content": turn.user_text},
                            {"role": "assistant", "content": decision.assistant_text},
                        ]
                    )
            except Exception as exc:
                trace = trace_collector.take(trace_id) if trace_collector is not None else {}
                turns.append(
                    ScenarioTurnEvaluationResult(
                        turn_index=turn_index,
                        trace_id=trace_id,
                        user_text=turn.user_text,
                        interaction_intent=turn.interaction_intent,
                        direct_hint_requested=turn.direct_hint_requested,
                        note=turn.note,
                        snapshot=turn.snapshot.model_dump(mode="json", by_alias=True),
                        history_before_turn=history_before_turn,
                        duration_ms=round((perf_counter() - turn_started) * 1000),
                        route_mode=_route_mode_from_trace(trace),
                        trace=trace,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        error_stack=traceback.format_exc(),
                    )
                )
    return ScenarioEvaluationResult(
        session_id=session_id,
        name=scenario.name,
        description=scenario.description,
        level_id=scenario.level_id,
        tags=list(scenario.tags),
        source_path=str(loaded.source_path),
        warnings=list(loaded.warnings),
        duration_ms=round((perf_counter() - started) * 1000),
        turns=turns,
    )


async def run_conversation_scenarios(
    client: DecisionClient,
    *,
    scenarios: Sequence[LoadedConversationScenario],
    concurrency: int = 4,
    model: str = "unknown",
    run_id: str | None = None,
    trace_collector: EvaluationTraceCollector | None = None,
) -> ConversationEvaluationReport:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if not scenarios:
        raise ValueError("at least one conversation scenario is required")
    active_run_id = run_id or datetime.now(UTC).strftime("scenario-%Y%m%dT%H%M%SZ")
    started_at = datetime.now(UTC)
    started = perf_counter()
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *(
            _evaluate_conversation_scenario(
                client,
                loaded,
                semaphore,
                active_run_id,
                scenario_index,
                trace_collector,
            )
            for scenario_index, loaded in enumerate(scenarios, start=1)
        )
    )
    completed_at = datetime.now(UTC)
    return ConversationEvaluationReport(
        run_id=active_run_id,
        model=model,
        concurrency=concurrency,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_ms=round((perf_counter() - started) * 1000),
        scenarios=list(results),
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
        f"- 电路初始状态：`{report.circuit_setup}`",
        f"- 快照协议：`{report.circuit_protocol}`",
        f"- 学习活动状态：`{report.learning_activity_setup}`",
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
                    f"- 路由模式：`{turn.route_mode or '—'}`",
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


def _snapshot_change_text(previous: dict[str, Any], current: dict[str, Any]) -> str:
    summary = summarize_snapshot_change(previous, current)
    parts: list[str] = []
    for label, key in (
        ("新增槽位", "added_slots"),
        ("移除槽位", "removed_slots"),
        ("变化槽位", "changed_slots"),
    ):
        if summary[key]:
            parts.append(f"{label}：" + "、".join(str(item) for item in summary[key]))
    for label, key in (("新增连线", "added_edges"), ("移除连线", "removed_edges")):
        if summary[key]:
            parts.append(
                f"{label}："
                + "、".join(f"{edge[0]} ↔ {edge[1]}" for edge in summary[key])
            )
    return "；".join(parts) if parts else "电路结构无变化"


def render_conversation_markdown(report: ConversationEvaluationReport) -> str:
    lines = [
        "# LLM 多轮场景评测",
        "",
        f"- 运行 ID：`{report.run_id}`",
        f"- 模型：`{report.model}`",
        f"- 并发数：`{report.concurrency}`",
        f"- 场景数：`{len(report.scenarios)}`",
        f"- 失败轮次：`{report.failed_turns}/{report.total_turns}`",
        f"- 总耗时：`{report.duration_ms} ms`",
    ]
    for scenario in report.scenarios:
        lines.extend(
            [
                "",
                f"## {scenario.name}",
                "",
                f"- 关卡：`{scenario.level_id}`",
                f"- 来源：`{scenario.source_path}`",
                f"- 标签：{('、'.join(scenario.tags) if scenario.tags else '—')}",
            ]
        )
        previous_snapshot: dict[str, Any] | None = None
        for turn in scenario.turns:
            revision = turn.snapshot.get("board", {}).get("topology_revision", "?")
            change_text = (
                "初始快照"
                if previous_snapshot is None
                else _snapshot_change_text(previous_snapshot, turn.snapshot)
            )
            lines.extend(
                [
                    "",
                    f"### 第 {turn.turn_index} 轮",
                    "",
                    f"- Trace：`{turn.trace_id}`",
                    f"- 路由模式：`{turn.route_mode or '—'}`",
                    f"- 直接提示：`{'开启' if turn.direct_hint_requested else '关闭'}`",
                    f"- 拓扑修订：`{revision}`",
                    f"- 电路变化：{change_text}",
                    f"- 人工备注：{_markdown_text(turn.note)}",
                    f"- 耗时：`{turn.duration_ms} ms`",
                    "",
                    f"**用户**：{_markdown_text(turn.user_text)}",
                    "",
                    f"**助手**：{_markdown_text(turn.assistant_text)}",
                    "",
                    "**工具调用**："
                    + (
                        f"`{json.dumps(turn.tool_call, ensure_ascii=False)}`"
                        if turn.tool_call is not None
                        else "—"
                    ),
                ]
            )
            if turn.error_type:
                lines.append(f"- 异常：`{turn.error_type}` {_markdown_text(turn.error_message)}")
            previous_snapshot = turn.snapshot
    return "\n".join(lines).rstrip() + "\n"


def write_conversation_evaluation_report(
    report: ConversationEvaluationReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    markdown_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_conversation_markdown(report), encoding="utf-8")
    return json_path, markdown_path
