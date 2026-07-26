from __future__ import annotations

import json

import httpx
import pytest

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES, build_circuit_coach_v2
from tuco_ai_backend.models import CircuitCoachDecisionRequest
from tuco_ai_backend.providers.circuit_coach_v2 import CircuitCoachV2Client


def test_circuit_coach_request_decodes_firmware_v2_compact_snapshot() -> None:
    request = CircuitCoachDecisionRequest.model_validate(
        {
            "session_id": "device-level-101",
            "user_text": "next step",
            "circuit_snapshot": {
                "schema": "tuco_circuit_v2",
                "level": {
                    "id": 101,
                    "goal": "direct",
                    "inputs": "input A",
                    "outputs": "output Y",
                    "input_count": 1,
                    "output_count": 1,
                },
                "unlocked_gates": ["INPUT", "OUTPUT"],
                "gate_templates": [["INPUT", ["up", "output"]]],
                "board": {
                    "topology_revision": 2,
                    "link_count": 0,
                    "invalid_link_count": 0,
                    "ignored_link_count": 0,
                    "link_overflow": False,
                    "slots": [
                        [0, 0, 0, "present", "INPUT", [[0, "left", "output"]]],
                        [1, 1, 0, "empty", None, [[4, "right", "unused"]]],
                    ],
                    "edges": [],
                },
            },
        }
    )

    assert request.user_text == "next step"
    assert request.circuit_snapshot.model_dump(by_alias=True)["schema"] == "tuco_circuit_v2"
    assert request.circuit_snapshot.level.id == 101
    assert request.circuit_snapshot.gate_templates == [["INPUT", ["up", "output"]]]
    assert request.circuit_snapshot.board.topology_revision == 2
    assert request.circuit_snapshot.board.slots[0].gate == "INPUT"
    assert request.circuit_snapshot.board.slots[0].ports[0].port_id == 0
    assert request.circuit_snapshot.board.slots[1].state == "empty"


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_accepts_empty_slot_tool_call() -> None:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 2:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "看一看亮起的位置，先放一块输入积木吧。"}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-slot",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_empty_slot",
                                        "arguments": json.dumps({"slot": 3, "gate": "INPUT"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    decision_request = CircuitCoachDecisionRequest(
        session_id="device-level-101",
        user_text="请亮灯指给我看，下一步该把积木放在哪里。",
        circuit_snapshot=build_circuit_coach_v2(level, "empty"),
    )
    store = RuntimeConfigStore(
        Settings(
            llm_base_url="https://relay.example/v1",
            llm_model="test-model",
            llm_api_key="secret",
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(
            decision_request
        )

    tool_names = [tool["function"]["name"] for tool in captured[0]["tools"]]
    assert tool_names == ["highlight_ports", "highlight_empty_slot"]
    assert "unlocked_gates" in captured[0]["messages"][-1]["content"]
    assert "tools" not in captured[1]
    assert decision.assistant_text == "看一看亮起的位置，先放一块输入积木吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.slot == 3


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_keeps_empty_slot_tool_when_io_ports_exist() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "看一看亮起的位置，把与非门放进去吧。"}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-gate-slot",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_empty_slot",
                                        "arguments": json.dumps({"slot": 5, "gate": "NAND"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    decision_request = CircuitCoachDecisionRequest(
        session_id="device-level-102",
        user_text="请亮灯指给我看，下一步该把积木放在哪里。",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(
            decision_request
        )

    assert decision.assistant_text == "看一看亮起的位置，把与非门放进去吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.name == "highlight_empty_slot"
    assert decision.tool_call.arguments.gate == "NAND"


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_preserves_text_when_a_tool_call_is_present() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "我来帮你亮一下这个位置。",
                            "tool_calls": [
                                {
                                    "id": "call-with-text",
                                    "type": "function",
                                    "function": {
                                        "name": "highlight_empty_slot",
                                        "arguments": json.dumps({"slot": 5, "gate": "NAND"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-with-text",
        user_text="请亮灯直接告诉我，下一步该在哪里放积木。",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "我来帮你亮一下这个位置。"
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "call-with-text"


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_generates_spoken_text_after_tool_only_response() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-tool-only",
                                        "type": "function",
                                        "function": {
                                            "name": "highlight_empty_slot",
                                            "arguments": json.dumps({"slot": 5, "gate": "NAND"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "看一看亮起的位置，把与非门放在那里吧。"}}]},
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 102)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-102-tool-only",
        user_text="请亮灯，直接告诉我下一步怎么接。",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert decision.assistant_text == "看一看亮起的位置，把与非门放在那里吧。"
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "call-tool-only"
    assert len(payloads) == 2
    assert "tools" not in payloads[1]
    assert "明确告诉孩子" in payloads[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_circuit_coach_v2_client_keeps_generic_hint_socratic_and_tool_free() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "先看看输入积木的信号要往哪里走。你觉得它下一站该找谁呢？"
                        }
                    }
                ]
            },
        )

    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    request = CircuitCoachDecisionRequest(
        session_id="device-level-101-socratic",
        user_text="接下来应该怎么做？给我点提示。",
        circuit_snapshot=build_circuit_coach_v2(level, "placed-io"),
    )
    store = RuntimeConfigStore(Settings(llm_api_key="secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        decision = await CircuitCoachV2Client(store, http_client=http_client).decide(request)

    assert len(payloads) == 1
    assert "tools" not in payloads[0]
    assert "本轮是启发式提示" in payloads[0]["messages"][-1]["content"]
    assert decision.assistant_text.endswith("谁呢？")
    assert decision.tool_call is None
