from __future__ import annotations

import asyncio
import gzip
import json
import struct
from dataclasses import dataclass
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


class AsrError(RuntimeError):
    pass


class AsrNoSpeechError(AsrError):
    pass


class AsrAuthenticationError(AsrError):
    pass


@dataclass
class AsrResponse:
    code: int = 0
    sequence: int = 0
    is_final: bool = False
    text: str = ""
    payload: dict | None = None


def build_full_request(sequence: int) -> bytes:
    payload = {
        "user": {"uid": "tuco-device"},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
            "language": "zh-CN",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": False,
        },
    }
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
    return (
        bytes([0x11, 0x11, 0x11, 0x00])
        + struct.pack(">iI", sequence, len(compressed))
        + compressed
    )


def build_audio_request(sequence: int, pcm: bytes, *, is_last: bool) -> bytes:
    compressed = gzip.compress(pcm)
    if is_last:
        header = bytes([0x11, 0x23, 0x01, 0x00])
        sequence = -sequence
    else:
        header = bytes([0x11, 0x21, 0x01, 0x00])
    return header + struct.pack(">iI", sequence, len(compressed)) + compressed


def parse_asr_response(data: bytes) -> AsrResponse:
    if len(data) < 8:
        raise AsrError("ASR response frame is too short")
    header_size = (data[0] & 0x0F) * 4
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = header_size
    response = AsrResponse()
    if flags & 0x01:
        response.sequence = struct.unpack(">i", data[offset : offset + 4])[0]
        offset += 4
        response.is_final = response.sequence < 0 or flags == 0x03
    if message_type == 0x0F:
        response.code = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
    payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    payload = data[offset : offset + payload_size]
    if compression == 1 and payload:
        payload = gzip.decompress(payload)
    if serialization == 1 and payload:
        response.payload = json.loads(payload.decode())
        response.text = str((response.payload.get("result") or {}).get("text") or "").strip()
    if response.code:
        message = response.payload or payload.decode(errors="replace")
        raise AsrError(f"ASR error {response.code}: {message}")
    return response


class VolcengineAsrClient:
    def __init__(
        self,
        *,
        api_key: str,
        resource_id: str = "volc.bigasr.sauc.duration",
        endpoint: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        chunk_bytes: int = 6400,
        timeout_seconds: float = 30,
    ) -> None:
        self.api_key = api_key
        self.resource_id = resource_id
        self.endpoint = endpoint
        self.chunk_bytes = chunk_bytes
        self.timeout_seconds = timeout_seconds

    async def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            raise AsrNoSpeechError("没有收到音频数据")
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid4()),
            "X-Api-Connect-Id": str(uuid4()),
        }
        try:
            final_text = await self._transcribe(pcm, headers)
        except InvalidStatus as exc:
            status_code = exc.response.status_code
            if status_code == 403:
                raise AsrAuthenticationError(
                    "火山 ASR 鉴权失败：请填写新版 API Key，并确认资源 "
                    f"{self.resource_id} 已开通"
                ) from exc
            raise AsrError(f"火山 ASR WebSocket 握手失败：HTTP {status_code}") from exc
        if not final_text:
            raise AsrNoSpeechError("没有识别到有效语音")
        return final_text

    async def _transcribe(self, pcm: bytes, headers: dict[str, str]) -> str:
        final_text = ""
        async with connect(
            self.endpoint,
            additional_headers=headers,
            open_timeout=self.timeout_seconds,
            close_timeout=3,
            max_size=4 * 1024 * 1024,
        ) as websocket:
            await websocket.send(build_full_request(1))
            try:
                first = await asyncio.wait_for(
                    websocket.recv(), timeout=self.timeout_seconds
                )
            except TimeoutError as exc:
                raise AsrError("火山 ASR 响应超时") from exc
            if isinstance(first, bytes):
                parsed = parse_asr_response(first)
                final_text = parsed.text or final_text
            chunks = [
                pcm[index : index + self.chunk_bytes]
                for index in range(0, len(pcm), self.chunk_bytes)
            ]
            for index, chunk in enumerate(chunks, start=2):
                await websocket.send(
                    build_audio_request(index, chunk, is_last=index == len(chunks) + 1)
                )
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.recv(), timeout=self.timeout_seconds
                    )
                except TimeoutError as exc:
                    raise AsrError("火山 ASR 响应超时") from exc
                if not isinstance(raw, bytes):
                    continue
                response = parse_asr_response(raw)
                final_text = response.text or final_text
                if response.is_final:
                    break
        return final_text

