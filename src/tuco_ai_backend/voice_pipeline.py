from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from tuco_ai_backend.models import CircuitSnapshot, DecisionRequest, ToolCall

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]
ExecuteTool = Callable[[ToolCall, int], Awaitable[dict[str, Any]]]
WaitForPlayback = Callable[[str], Awaitable[None]]


class VoicePipeline:
    def __init__(self, asr: Any, llm: Any, tts: Any) -> None:
        self.asr = asr
        self.llm = llm
        self.tts = tts

    async def run(
        self,
        *,
        session_id: str,
        pcm: bytes,
        circuit: CircuitSnapshot,
        send_json: SendJson,
        send_audio: SendAudio,
        execute_tool: ExecuteTool,
        wait_for_playback: WaitForPlayback,
    ) -> None:
        text = await self.asr.transcribe(pcm)
        await send_json(
            {"type": "asr.result", "session_id": session_id, "text": text, "is_final": True}
        )
        request = DecisionRequest(question=text, circuit=circuit)
        decision = await self.llm.decide(request)
        await send_json({"type": "response.started", "session_id": session_id})
        final_text = decision.assistant_text
        if decision.tool_call is not None:
            if not final_text:
                final_text = "好呀，我先说明接线方向，等说完就把对应的积木亮起来。"
            await self._speak(
                session_id=session_id,
                text=final_text,
                send_json=send_json,
                send_audio=send_audio,
                await_playback=True,
            )
            await wait_for_playback(session_id)
            await send_json(
                {
                    "type": "device.command",
                    "session_id": session_id,
                    "call_id": decision.tool_call.call_id,
                    "name": decision.tool_call.name,
                    "topology_revision": decision.topology_revision,
                    "arguments": decision.tool_call.arguments.model_dump(),
                }
            )
            await execute_tool(decision.tool_call, decision.topology_revision)
            await send_json({"type": "response.done", "session_id": session_id})
            return
        if not final_text:
            final_text = "请检查当前电路连接。"
        await self._speak(
            session_id=session_id,
            text=final_text,
            send_json=send_json,
            send_audio=send_audio,
            await_playback=False,
        )

    async def _speak(
        self,
        *,
        session_id: str,
        text: str,
        send_json: SendJson,
        send_audio: SendAudio,
        await_playback: bool,
    ) -> None:
        await send_json(
            {
                "type": "response.audio.start",
                "session_id": session_id,
                "format": "pcm_s16le",
                "sample_rate_hz": 16000,
                "channels": 1,
                "text": text,
            }
        )
        audio_bytes = 0
        async for chunk in self.tts.synthesize(text):
            audio_bytes += len(chunk)
            await send_audio(chunk)
        await send_json(
            {
                "type": "response.audio.done",
                "session_id": session_id,
                "audio_bytes": audio_bytes,
                "await_playback": await_playback,
            }
        )
