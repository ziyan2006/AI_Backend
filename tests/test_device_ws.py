from fastapi.testclient import TestClient

from tuco_ai_backend.config import Settings
from tuco_ai_backend.main import create_app


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


def test_device_websocket_rejects_binary_before_recording() -> None:
    app = create_app(Settings())

    with TestClient(app) as client:
        with client.websocket_connect("/ws/device") as websocket:
            websocket.send_bytes(b"\x00\x00")
            error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "protocol.invalid_state"
