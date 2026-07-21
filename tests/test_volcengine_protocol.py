import json

from tuco_ai_backend.providers.volcengine_protocol import (
    EventType,
    Message,
    MessageFlag,
    MessageType,
)


def test_full_client_request_matches_official_wire_shape() -> None:
    message = Message(
        message_type=MessageType.FULL_CLIENT_REQUEST,
        flag=MessageFlag.NO_SEQUENCE,
        payload=b'{"hello":"world"}',
    )

    wire = message.to_bytes()

    assert wire[:4] == bytes([0x11, 0x10, 0x10, 0x00])
    assert int.from_bytes(wire[4:8], "big") == len(message.payload)
    assert wire[8:] == message.payload


def test_event_response_round_trip() -> None:
    payload = json.dumps({"status_code": 20000000, "message": "ok"}).encode()
    message = Message(
        message_type=MessageType.FULL_SERVER_RESPONSE,
        flag=MessageFlag.WITH_EVENT,
        event=EventType.SESSION_FINISHED,
        session_id="session-1",
        payload=payload,
    )

    decoded = Message.from_bytes(message.to_bytes())

    assert decoded.event == EventType.SESSION_FINISHED
    assert decoded.session_id == "session-1"
    assert decoded.payload == payload


def test_audio_server_payload_round_trip() -> None:
    message = Message(
        message_type=MessageType.AUDIO_ONLY_SERVER,
        payload=b"\x00\x01" * 128,
    )

    decoded = Message.from_bytes(message.to_bytes())

    assert decoded.message_type == MessageType.AUDIO_ONLY_SERVER
    assert decoded.payload == message.payload

