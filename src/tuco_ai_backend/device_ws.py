from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from tuco_ai_backend.models import CircuitSnapshot
from tuco_ai_backend.session_store import GLOBAL_SESSION_STORE, classify_trace_module

LOGGER = logging.getLogger(__name__)


class WebSocketLogHandler(logging.Handler):
    def __init__(
        self,
        websocket: WebSocket,
        trace_id: str,
        loop: asyncio.AbstractEventLoop,
        session_id: str | None = None,
    ) -> None:
        super().__init__()
        self.websocket = websocket
        self.trace_id = trace_id
        self.loop = loop
        self.session_id = session_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if self.trace_id in msg:
                module = classify_trace_module(msg)
                
                # 记录日志到全局 SessionStore
                GLOBAL_SESSION_STORE.add_log(
                    trace_id=self.trace_id,
                    module=module,
                    level=record.levelname,
                    message=msg,
                    session_id=self.session_id,
                )

                payload = {
                    "type": "trace.log",
                    "trace_id": self.trace_id,
                    "level": record.levelname,
                    "module": module,
                    "message": msg
                }
                asyncio.run_coroutine_threadsafe(
                    self.websocket.send_json(payload), self.loop
                )
        except Exception:
            pass


DEFAULT_MAX_AUDIO_BYTES = 512 * 1024


@dataclass
class DeviceConnectionState:
    connection_id: str = field(default_factory=lambda: uuid4().hex)
    ready: bool = False
    session_id: str | None = None
    recording: bool = False
    audio_bytes: int = 0
    audio_buffer: bytearray = field(default_factory=bytearray)
    circuit: CircuitSnapshot | None = None
    pipeline: Any | None = None
    response_task: asyncio.Task | None = None
    pending_tools: dict[str, asyncio.Future] = field(default_factory=dict)
    playback_waiter: asyncio.Future | None = None
    playback_finished_session: str | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    device_id: str = "unknown"
    current_level_id: int | None = None
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES
    pipeline_timeout_seconds: float = 120.0
    audio_capture: Any | None = None


async def _send_json(
    websocket: WebSocket,
    state: DeviceConnectionState,
    payload: dict[str, Any],
) -> None:
    async with state.send_lock:
        await websocket.send_json(payload)


async def _send_bytes(
    websocket: WebSocket,
    state: DeviceConnectionState,
    payload: bytes,
) -> None:
    async with state.send_lock:
        await websocket.send_bytes(payload)


async def send_error(
    websocket: WebSocket,
    state: DeviceConnectionState,
    code: str,
    message: str,
    *,
    session_id: str | None = None,
    retryable: bool = True,
) -> None:
    payload: dict[str, Any] = {
        "type": "error",
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    await _send_json(websocket, state, payload)


async def device_websocket(
    websocket: WebSocket,
    pipeline: Any | None = None,
    *,
    expected_device_token: str | None = None,
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
    pipeline_timeout_seconds: float = 120.0,
    audio_capture: Any | None = None,
) -> None:
    await websocket.accept()
    state = DeviceConnectionState(
        pipeline=pipeline,
        max_audio_bytes=max_audio_bytes,
        pipeline_timeout_seconds=pipeline_timeout_seconds,
        audio_capture=audio_capture,
    )
    try:
        while True:
            frame = await websocket.receive()
            if frame["type"] == "websocket.disconnect":
                break
            if frame.get("bytes") is not None:
                await _handle_audio(websocket, state, frame["bytes"])
                continue
            if frame.get("text") is not None:
                try:
                    message = json.loads(frame["text"])
                    if not isinstance(message, dict):
                        raise ValueError("message must be an object")
                except (json.JSONDecodeError, ValueError, TypeError):
                    await send_error(
                        websocket,
                        state,
                        "protocol.invalid_message",
                        "JSON message could not be decoded",
                        session_id=state.session_id,
                    )
                    continue
                await _handle_message(websocket, state, message, expected_device_token)
    except WebSocketDisconnect:
        pass
    finally:
        if state.response_task is not None:
            state.response_task.cancel()
        for future in state.pending_tools.values():
            if not future.done():
                future.cancel()


async def _handle_audio(
    websocket: WebSocket,
    state: DeviceConnectionState,
    audio: bytes,
) -> None:
    if not state.recording:
        await send_error(
            websocket,
            state,
            "protocol.invalid_state",
            "binary audio is only accepted while recording",
            session_id=state.session_id,
        )
        return
    if len(audio) > state.max_audio_bytes - state.audio_bytes:
        state.recording = False
        state.audio_bytes = 0
        state.audio_buffer.clear()
        await send_error(
            websocket,
            state,
            "audio.too_large",
            "audio exceeds the per-turn size limit",
            session_id=state.session_id,
            retryable=False,
        )
        return
    state.audio_bytes += len(audio)
    state.audio_buffer.extend(audio)



async def _handle_message(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
    expected_device_token: str | None,
) -> None:
    message_type = message.get("type")
    if message_type == "device.hello":
        await _handle_hello(websocket, state, message, expected_device_token)
    elif message_type == "session.start":
        await _handle_session_start(websocket, state, message)
    elif message_type == "circuit.snapshot":
        await _handle_snapshot(websocket, state, message)
    elif message_type == "input_audio.start":
        await _handle_audio_start(websocket, state, message)
    elif message_type == "input_audio.commit":
        await _handle_audio_commit(websocket, state, message)
    elif message_type == "heartbeat":
        await _send_json(websocket, state, message)
    elif message_type == "device.command.result":
        if not _session_matches(state, message):
            await send_error(
                websocket,
                state,
                "protocol.invalid_state",
                "device command result does not match the active session",
                session_id=state.session_id,
            )
            return
        call_id = message.get("call_id")
        future = state.pending_tools.pop(call_id, None)
        if future is not None and not future.done():
            future.set_result(
                {
                    "ok": bool(message.get("ok")),
                    "message": str(message.get("message") or ""),
                }
            )
        await _send_json(
            websocket,
            state,
            {
                "type": "device.command.result.accepted",
                "session_id": message.get("session_id"),
                "call_id": message.get("call_id"),
            }
        )
    elif message_type == "response.audio.played":
        if not _session_matches(state, message):
            await send_error(
                websocket,
                state,
                "protocol.invalid_state",
                "playback confirmation does not match the active session",
                session_id=state.session_id,
            )
            return
        state.playback_finished_session = state.session_id
        if state.playback_waiter is not None and not state.playback_waiter.done():
            state.playback_waiter.set_result(None)
    else:
        await send_error(
            websocket,
            state,
            "protocol.invalid_message",
            f"unsupported message type: {message_type}",
            session_id=state.session_id,
        )


async def _handle_hello(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
    expected_device_token: str | None,
) -> None:
    if message.get("protocol_version") != 2:
        await send_error(
            websocket,
            state,
            "protocol.unsupported_version",
            "protocol_version must be 2",
            retryable=False,
        )
        return
    supplied_token = message.get("device_token")
    if expected_device_token is not None and (
        not isinstance(supplied_token, str) or
        not hmac.compare_digest(supplied_token, expected_device_token)
    ):
        await send_error(
            websocket,
            state,
            "device.authentication_failed",
            "device authentication failed",
            retryable=False,
        )
        await websocket.close(code=1008)
        return
    device_id = message.get("device_id")
    state.device_id = device_id if isinstance(device_id, str) and device_id else "unknown"
    state.ready = True
    LOGGER.info("device ready: connection=%s device=%s", state.connection_id, state.device_id)
    await _send_json(
        websocket,
        state,
        {
            "type": "device.ready",
            "protocol_version": 2,
            "connection_id": state.connection_id,
        }
    )


async def _handle_session_start(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
) -> None:
    session_id = message.get("session_id")
    if not state.ready or not isinstance(session_id, str) or not session_id:
        await send_error(
            websocket,
            state,
            "protocol.invalid_state",
            "device.hello and a non-empty session_id are required",
        )
        return
    state.session_id = session_id
    state.recording = False
    state.audio_bytes = 0
    state.audio_buffer.clear()
    state.circuit = None
    state.playback_finished_session = None
    await _send_json(
        websocket,
        state,
        {"type": "session.ready", "session_id": session_id},
    )


async def _handle_snapshot(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
) -> None:
    if not _session_matches(state, message):
        await send_error(
            websocket,
            state,
            "protocol.invalid_state",
            "circuit snapshot does not match the active session",
            session_id=state.session_id,
        )
        return
    try:
        circuit = CircuitSnapshot.model_validate(message)
        state.circuit = circuit
        if circuit.level is not None:
            level_title = getattr(circuit.level, "title", None) or f"关卡 {circuit.level.level_id}"
            GLOBAL_SESSION_STORE.create_session(
                level_id=circuit.level.level_id,
                level_title=level_title,
                session_id=state.session_id,
            )
    except ValidationError as exc:
        await send_error(
            websocket,
            state,
            "protocol.invalid_message",
            str(exc),
            session_id=state.session_id,
        )
        return
    new_level_id = state.circuit.level.level_id if state.circuit.level else None
    if new_level_id != state.current_level_id:
        state.conversation_history.clear()
        state.current_level_id = new_level_id
    await _send_json(
        websocket,
        state,
        {
            "type": "circuit.snapshot.accepted",
            "session_id": state.session_id,
            "topology_revision": state.circuit.topology_revision,
        }
    )


async def _handle_audio_start(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
) -> None:
    valid_format = (
        message.get("format") == "pcm_s16le"
        and message.get("sample_rate_hz") == 16000
        and message.get("channels") == 1
    )
    if not _session_matches(state, message) or state.circuit is None:
        await send_error(
            websocket,
            state,
            "protocol.invalid_state",
            "an active session and circuit snapshot are required",
            session_id=state.session_id,
        )
        return
    if not valid_format:
        await send_error(
            websocket,
            state,
            "audio.invalid_format",
            "expected PCM S16LE, 16000 Hz, mono",
            session_id=state.session_id,
        )
        return
    state.recording = True
    state.audio_bytes = 0
    state.audio_buffer.clear()
    await _send_json(
        websocket,
        state,
        {"type": "input_audio.started", "session_id": state.session_id}
    )


async def _handle_audio_commit(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
) -> None:
    if not state.recording or not _session_matches(state, message):
        await send_error(
            websocket,
            state,
            "protocol.invalid_state",
            "no matching audio recording is active",
            session_id=state.session_id,
        )
        return
    state.recording = False
    await _send_json(
        websocket,
        state,
        {
            "type": "input_audio.committed",
            "session_id": state.session_id,
            "audio_bytes": state.audio_bytes,
        }
    )
    LOGGER.info("audio committed: session=%s device=%s bytes=%d",
                state.session_id, state.device_id, state.audio_bytes)
    pipeline = state.pipeline() if callable(state.pipeline) else state.pipeline
    if pipeline is None:
        await send_error(
            websocket,
            state,
            "voice.not_configured",
            "GPT API Key and Volcengine API Key are required",
            session_id=state.session_id,
            retryable=False,
        )
        return
    if state.circuit is not None:
        pcm = bytes(state.audio_buffer)
        state.audio_buffer.clear()
        if state.audio_capture is not None:
            await state.audio_capture.capture_pcm(
                direction="input", session_id=state.session_id, pcm=pcm
            )
        if state.response_task is not None and not state.response_task.done():
            state.response_task.cancel()
        state.response_task = asyncio.create_task(
            _run_pipeline(websocket, state, pcm, pipeline), name=f"voice-{state.session_id}"
        )


def _session_matches(state: DeviceConnectionState, message: dict[str, Any]) -> bool:
    return state.session_id is not None and message.get("session_id") == state.session_id


async def _run_pipeline(
    websocket: WebSocket,
    state: DeviceConnectionState,
    pcm: bytes,
    pipeline: Any,
) -> None:
    current_turn: dict[str, str] = {}
    trace_id = f"tr_{int(time.time())}_{uuid4().hex[:6]}"
    loop = asyncio.get_running_loop()

    # 注册本链路专属的全链路日志监控 Handler
    handler = WebSocketLogHandler(websocket, trace_id, loop, session_id=state.session_id)
    root_logger = logging.getLogger("tuco_ai_backend")
    root_logger.addHandler(handler)

    async def send_json(payload: dict[str, Any]) -> None:
        if payload.get("type") == "asr.result" and payload.get("text"):
            current_turn["user"] = str(payload["text"])
        elif payload.get("type") == "response.audio.start" and payload.get("text"):
            current_turn["assistant"] = str(payload["text"])
        await _send_json(websocket, state, payload)

    async def send_audio(payload: bytes) -> None:
        await _send_bytes(websocket, state, payload)

    async def execute_tool(tool_call: Any, topology_revision: int) -> dict[str, Any]:
        future = loop.create_future()
        state.pending_tools[tool_call.call_id] = future
        try:
            return await asyncio.wait_for(future, timeout=15)
        finally:
            state.pending_tools.pop(tool_call.call_id, None)

    async def wait_for_playback(session_id: str) -> None:
        if state.playback_finished_session == session_id:
            state.playback_finished_session = None
            return
        future = loop.create_future()
        state.playback_waiter = future
        try:
            await asyncio.wait_for(future, timeout=45)
        finally:
            if state.playback_waiter is future:
                state.playback_waiter = None

    try:
        try:
            await asyncio.wait_for(
                pipeline.run(
                    session_id=state.session_id,
                    pcm=pcm,
                    circuit=state.circuit,
                    send_json=send_json,
                    send_audio=send_audio,
                    execute_tool=execute_tool,
                    wait_for_playback=wait_for_playback,
                    history=state.conversation_history,
                    trace_id=trace_id,
                ),
                timeout=state.pipeline_timeout_seconds,
            )
        except TypeError:
            try:
                await asyncio.wait_for(
                    pipeline.run(
                        session_id=state.session_id,
                        pcm=pcm,
                        circuit=state.circuit,
                        send_json=send_json,
                        send_audio=send_audio,
                        execute_tool=execute_tool,
                        wait_for_playback=wait_for_playback,
                        history=state.conversation_history,
                    ),
                    timeout=state.pipeline_timeout_seconds,
                )
            except TypeError:
                try:
                    await asyncio.wait_for(
                        pipeline.run(
                            session_id=state.session_id,
                            pcm=pcm,
                            circuit=state.circuit,
                            send_json=send_json,
                            send_audio=send_audio,
                            execute_tool=execute_tool,
                            wait_for_playback=wait_for_playback,
                        ),
                        timeout=state.pipeline_timeout_seconds,
                    )
                except TypeError:
                    await asyncio.wait_for(
                        pipeline.run(
                            session_id=state.session_id,
                            pcm=pcm,
                            circuit=state.circuit,
                            send_json=send_json,
                            send_audio=send_audio,
                            execute_tool=execute_tool,
                        ),
                        timeout=state.pipeline_timeout_seconds,
                    )
        if "user" in current_turn and "assistant" in current_turn:
            state.conversation_history.append(
                {"role": "user", "content": current_turn["user"]}
            )
            state.conversation_history.append(
                {"role": "assistant", "content": current_turn["assistant"]}
            )
            if len(state.conversation_history) > 10:
                state.conversation_history = state.conversation_history[-10:]
        LOGGER.info(
            "[%s] voice response complete: session=%s device=%s",
            trace_id,
            state.session_id,
            state.device_id,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        LOGGER.warning(
            "[%s] voice response timed out: session=%s device=%s",
            trace_id,
            state.session_id,
            state.device_id,
        )
        await send_error(
            websocket,
            state,
            "voice.timeout",
            "voice response timed out",
            session_id=state.session_id,
            retryable=True,
        )
    except Exception as exc:
        code = "voice.pipeline_error"
        name = type(exc).__name__
        if name == "AsrNoSpeechError":
            code = "asr.no_speech"
        elif name == "AsrAuthenticationError":
            code = "asr.authentication_failed"
        elif name.startswith("Asr"):
            code = "asr.provider_error"
        elif name.startswith("Tts"):
            code = "tts.provider_error"
        await send_error(
            websocket,
            state,
            code,
            str(exc),
            session_id=state.session_id,
            retryable=True,
        )
        LOGGER.warning(
            "[%s] voice response failed: session=%s device=%s error=%s",
            trace_id,
            state.session_id,
            state.device_id,
            type(exc).__name__,
        )
    finally:
        root_logger.removeHandler(handler)
