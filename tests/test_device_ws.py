import json

from fastapi.testclient import TestClient

from tuco_ai_backend.config import Settings
from tuco_ai_backend.device_ws import DeviceConnectionState, _handle_audio_commit
from tuco_ai_backend.main import create_app
from tuco_ai_backend.models import ToolCall
from tuco_ai_backend.tools import HighlightPortsArgs


class FakeVoicePipeline:
    async def run(
        self,
        *,
        session_id,
        pcm,
        circuit,
        send_json,
        send_audio,
        execute_tool,
    ) -> None:
        assert session_id == "session-1"
        assert pcm == b"\x00\x01" * 160
        assert circuit.topology_revision == 3
        await send_json(
            {"type": "asr.result", "session_id": session_id, "text": "为什么灯不亮"}
        )
        await send_json({"type": "response.started", "session_id": session_id})
        tool_call = ToolCall(
            call_id="call-1",
            name="highlight_ports",
            arguments=HighlightPortsArgs(
                ports=[8, 21],
                duration_ms=1000,
                pattern="pulse",
                reason="检查接线",
            ),
        )
        await send_json(
            {
                "type": "device.command",
                "session_id": session_id,
                "call_id": tool_call.call_id,
                "name": tool_call.name,
                "arguments": tool_call.arguments.model_dump(),
                "topology_revision": circuit.topology_revision,
            }
        )
        result = await execute_tool(tool_call, circuit.topology_revision)
        assert result == {"ok": True, "message": "highlighted"}
        await send_json({"type": "response.audio.start", "session_id": session_id})
        await send_audio(b"\x01\x02\x03\x04")
        await send_json({"type": "response.audio.done", "session_id": session_id})


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent_json = []

    async def send_json(self, payload) -> None:
        self.sent_json.append(payload)


def test_device_websocket_handshake_and_pcm_commit() -> None:
    app = create_app(Settings())

    with TestClient(app) as client:
        with client.websocket_connect("/ws/device") as websocket:
            websocket.send_json(
                {
                    "type": "device.hello",
                    "protocol_version": 2,
                    "device_id": "simulator-001",
                }
            )
            ready = websocket.receive_json()
            assert ready["type"] == "device.ready"
            assert ready["protocol_version"] == 2

            websocket.send_json({"type": "session.start", "session_id": "session-1"})
            assert websocket.receive_json() == {
                "type": "session.ready",
                "session_id": "session-1",
            }

            websocket.send_json(
                {
                    "type": "circuit.snapshot",
                    "schema_version": 1,
                    "session_id": "session-1",
                    "topology_revision": 3,
                    "slots": [],
                    "valid_links": [],
                    "invalid_links": [],
                    "scan": {},
                }
            )
            assert websocket.receive_json()["type"] == "circuit.snapshot.accepted"

            websocket.send_json(
                {
                    "type": "input_audio.start",
                    "session_id": "session-1",
                    "format": "pcm_s16le",
                    "sample_rate_hz": 16000,
                    "channels": 1,
                }
            )
            assert websocket.receive_json()["type"] == "input_audio.started"

            websocket.send_bytes(b"\x00\x01" * 160)
            websocket.send_json({"type": "input_audio.commit", "session_id": "session-1"})
            committed = websocket.receive_json()

    assert committed == {
        "type": "input_audio.committed",
        "session_id": "session-1",
        "audio_bytes": 320,
    }


async def test_audio_commit_reports_missing_voice_configuration() -> None:
    websocket = RecordingWebSocket()
    state = DeviceConnectionState(
        session_id="session-1",
        recording=True,
        audio_bytes=320,
    )

    await _handle_audio_commit(
        websocket,
        state,
        {"type": "input_audio.commit", "session_id": "session-1"},
    )

    assert websocket.sent_json == [
        {
            "type": "input_audio.committed",
            "session_id": "session-1",
            "audio_bytes": 320,
        },
        {
            "type": "error",
            "code": "voice.not_configured",
            "message": "GPT API Key and Volcengine API Key are required",
            "retryable": False,
            "session_id": "session-1",
        },
    ]


def test_device_websocket_rejects_binary_before_recording() -> None:
    app = create_app(Settings())

    with TestClient(app) as client:
        with client.websocket_connect("/ws/device") as websocket:
            websocket.send_bytes(b"\x00\x00")
            error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "protocol.invalid_state"


def test_device_websocket_rejects_invalid_device_token() -> None:
    app = create_app(Settings(device_token="device-test-token"))

    with TestClient(app) as client:
        with client.websocket_connect("/ws/device") as websocket:
            websocket.send_json(
                {
                    "type": "device.hello",
                    "protocol_version": 2,
                    "device_id": "simulator-001",
                    "device_token": "wrong-token",
                }
            )
            error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "device.authentication_failed"


def test_device_websocket_runs_pipeline_and_accepts_tool_result() -> None:
    app = create_app(Settings(), voice_pipeline=FakeVoicePipeline())

    with TestClient(app) as client:
        with client.websocket_connect("/ws/device") as websocket:
            websocket.send_json(
                {
                    "type": "device.hello",
                    "protocol_version": 2,
                    "device_id": "simulator-001",
                }
            )
            assert websocket.receive_json()["type"] == "device.ready"
            websocket.send_json({"type": "session.start", "session_id": "session-1"})
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json(
                {
                    "type": "circuit.snapshot",
                    "schema_version": 1,
                    "session_id": "session-1",
                    "topology_revision": 3,
                    "slots": [],
                    "valid_links": [],
                    "invalid_links": [],
                    "scan": {},
                }
            )
            assert websocket.receive_json()["type"] == "circuit.snapshot.accepted"
            websocket.send_json(
                {
                    "type": "input_audio.start",
                    "session_id": "session-1",
                    "format": "pcm_s16le",
                    "sample_rate_hz": 16000,
                    "channels": 1,
                }
            )
            assert websocket.receive_json()["type"] == "input_audio.started"
            websocket.send_bytes(b"\x00\x01" * 160)
            websocket.send_json({"type": "input_audio.commit", "session_id": "session-1"})
            assert websocket.receive_json()["type"] == "input_audio.committed"
            assert websocket.receive_json()["type"] == "asr.result"
            assert websocket.receive_json()["type"] == "response.started"
            command = websocket.receive_json()
            assert command["type"] == "device.command"
            websocket.send_json(
                {
                    "type": "device.command.result",
                    "session_id": "session-1",
                    "call_id": command["call_id"],
                    "ok": True,
                    "message": "highlighted",
                }
            )

            message_types = set()
            audio_frames = []
            for _ in range(4):
                frame = websocket.receive()
                if frame.get("text") is not None:
                    message_types.add(json.loads(frame["text"])["type"])
                elif frame.get("bytes") is not None:
                    audio_frames.append(frame["bytes"])

    assert message_types == {
        "device.command.result.accepted",
        "response.audio.start",
        "response.audio.done",
    }
    assert audio_frames == [b"\x01\x02\x03\x04"]
