from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from tuco_ai_backend.models import CircuitSnapshot


@dataclass
class DeviceConnectionState:
    connection_id: str = field(default_factory=lambda: uuid4().hex)
    ready: bool = False
    session_id: str | None = None
    recording: bool = False
    audio_bytes: int = 0
    circuit: CircuitSnapshot | None = None


async def send_error(
    websocket: WebSocket,
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
    await websocket.send_json(payload)


async def device_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    state = DeviceConnectionState()
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
                        "protocol.invalid_message",
                        "JSON message could not be decoded",
                        session_id=state.session_id,
                    )
                    continue
                await _handle_message(websocket, state, message)
    except WebSocketDisconnect:
        return


async def _handle_audio(
    websocket: WebSocket,
    state: DeviceConnectionState,
    audio: bytes,
) -> None:
    if not state.recording:
        await send_error(
            websocket,
            "protocol.invalid_state",
            "binary audio is only accepted while recording",
            session_id=state.session_id,
        )
        return
    state.audio_bytes += len(audio)


async def _handle_message(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
) -> None:
    message_type = message.get("type")
    if message_type == "device.hello":
        await _handle_hello(websocket, state, message)
    elif message_type == "session.start":
        await _handle_session_start(websocket, state, message)
    elif message_type == "circuit.snapshot":
        await _handle_snapshot(websocket, state, message)
    elif message_type == "input_audio.start":
        await _handle_audio_start(websocket, state, message)
    elif message_type == "input_audio.commit":
        await _handle_audio_commit(websocket, state, message)
    elif message_type == "heartbeat":
        await websocket.send_json(message)
    elif message_type == "device.command.result":
        await websocket.send_json(
            {
                "type": "device.command.result.accepted",
                "session_id": message.get("session_id"),
                "call_id": message.get("call_id"),
            }
        )
    else:
        await send_error(
            websocket,
            "protocol.invalid_message",
            f"unsupported message type: {message_type}",
            session_id=state.session_id,
        )


async def _handle_hello(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
) -> None:
    if message.get("protocol_version") != 2:
        await send_error(
            websocket,
            "protocol.unsupported_version",
            "protocol_version must be 2",
            retryable=False,
        )
        return
    state.ready = True
    await websocket.send_json(
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
            "protocol.invalid_state",
            "device.hello and a non-empty session_id are required",
        )
        return
    state.session_id = session_id
    state.recording = False
    state.audio_bytes = 0
    state.circuit = None
    await websocket.send_json({"type": "session.ready", "session_id": session_id})


async def _handle_snapshot(
    websocket: WebSocket,
    state: DeviceConnectionState,
    message: dict[str, Any],
) -> None:
    if not _session_matches(state, message):
        await send_error(
            websocket,
            "protocol.invalid_state",
            "circuit snapshot does not match the active session",
            session_id=state.session_id,
        )
        return
    try:
        state.circuit = CircuitSnapshot.model_validate(message)
    except ValidationError as exc:
        await send_error(
            websocket,
            "protocol.invalid_message",
            str(exc),
            session_id=state.session_id,
        )
        return
    await websocket.send_json(
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
            "protocol.invalid_state",
            "an active session and circuit snapshot are required",
            session_id=state.session_id,
        )
        return
    if not valid_format:
        await send_error(
            websocket,
            "audio.invalid_format",
            "expected PCM S16LE, 16000 Hz, mono",
            session_id=state.session_id,
        )
        return
    state.recording = True
    state.audio_bytes = 0
    await websocket.send_json(
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
            "protocol.invalid_state",
            "no matching audio recording is active",
            session_id=state.session_id,
        )
        return
    state.recording = False
    await websocket.send_json(
        {
            "type": "input_audio.committed",
            "session_id": state.session_id,
            "audio_bytes": state.audio_bytes,
        }
    )


def _session_matches(state: DeviceConnectionState, message: dict[str, Any]) -> bool:
    return state.session_id is not None and message.get("session_id") == state.session_id
