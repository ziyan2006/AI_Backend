import gzip
import json

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

import tuco_ai_backend.providers.volcengine_asr as asr_module
from tuco_ai_backend.providers.volcengine_asr import (
    AsrAuthenticationError,
    VolcengineAsrClient,
    build_audio_request,
    build_full_request,
    parse_asr_response,
)


def test_asr_full_request_uses_sequence_and_gzip_json() -> None:
    wire = build_full_request(sequence=1)

    assert wire[:4] == bytes([0x11, 0x11, 0x11, 0x00])
    assert int.from_bytes(wire[4:8], "big", signed=True) == 1
    size = int.from_bytes(wire[8:12], "big")
    payload = json.loads(gzip.decompress(wire[12 : 12 + size]))
    assert payload["audio"] == {
        "format": "pcm",
        "codec": "raw",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
        "language": "zh-CN",
    }


def test_asr_last_audio_request_uses_negative_sequence() -> None:
    wire = build_audio_request(sequence=7, pcm=b"\x00\x00" * 20, is_last=True)

    assert wire[:4] == bytes([0x11, 0x23, 0x01, 0x00])
    assert int.from_bytes(wire[4:8], "big", signed=True) == -7


def test_parse_asr_final_text() -> None:
    payload = gzip.compress(json.dumps({"result": {"text": "测试成功"}}).encode())
    wire = bytes([0x11, 0x93, 0x11, 0x00]) + (-2).to_bytes(4, "big", signed=True)
    wire += len(payload).to_bytes(4, "big") + payload

    response = parse_asr_response(wire)

    assert response.is_final is True
    assert response.text == "测试成功"


class RejectingConnection:
    async def __aenter__(self):
        response = Response(403, "Forbidden", Headers())
        raise InvalidStatus(response)

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


async def test_asr_maps_http_403_to_authentication_error(monkeypatch) -> None:
    monkeypatch.setattr(asr_module, "connect", lambda *args, **kwargs: RejectingConnection())
    client = VolcengineAsrClient(api_key="invalid-key")

    with pytest.raises(AsrAuthenticationError, match="火山 ASR 鉴权失败"):
        await client.transcribe(b"\x00\x00" * 160)
