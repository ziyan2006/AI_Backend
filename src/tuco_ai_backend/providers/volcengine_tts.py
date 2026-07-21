from __future__ import annotations

import json
from uuid import uuid4

from websockets.asyncio.client import connect

from tuco_ai_backend.providers.volcengine_protocol import EventType, Message, MessageType


class TtsError(RuntimeError):
    pass


class VolcengineTtsClient:
    def __init__(
        self,
        *,
        api_key: str,
        voice_type: str = "zh_female_vv_uranus_bigtts",
        resource_id: str = "seed-tts-2.0",
        endpoint: str = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream",
        sample_rate: int = 16000,
        timeout_seconds: float = 30,
    ) -> None:
        self.api_key = api_key
        self.voice_type = voice_type
        self.resource_id = resource_id
        self.endpoint = endpoint
        self.sample_rate = sample_rate
        self.timeout_seconds = timeout_seconds

    async def synthesize(self, text: str):
        request = {
            "user": {"uid": str(uuid4())},
            "req_params": {
                "speaker": self.voice_type,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "enable_timestamp": False,
                },
                "text": text,
                "additions": json.dumps({"disable_markdown_filter": False}),
            },
        }
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid4()),
            "X-Api-Connect-Id": str(uuid4()),
        }
        received_audio = False
        async with connect(
            self.endpoint,
            additional_headers=headers,
            open_timeout=self.timeout_seconds,
            close_timeout=3,
            max_size=10 * 1024 * 1024,
        ) as websocket:
            message = Message(
                message_type=MessageType.FULL_CLIENT_REQUEST,
                payload=json.dumps(request, ensure_ascii=False).encode(),
            )
            await websocket.send(message.to_bytes())
            while True:
                raw = await websocket.recv()
                if not isinstance(raw, bytes):
                    continue
                response = Message.from_bytes(raw)
                if response.message_type == MessageType.AUDIO_ONLY_SERVER:
                    received_audio = True
                    yield response.payload
                elif response.message_type == MessageType.ERROR:
                    raise TtsError(
                        f"TTS error {response.error_code}: "
                        f"{response.payload.decode(errors='replace')}"
                    )
                elif (
                    response.message_type == MessageType.FULL_SERVER_RESPONSE
                    and response.event == EventType.SESSION_FINISHED
                ):
                    break
        if not received_audio:
            raise TtsError("TTS did not return audio")
