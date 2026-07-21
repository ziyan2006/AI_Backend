from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from tuco_ai_backend.models import CircuitSnapshot, DecisionRequest, ToolCall

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]
ExecuteTool = Callable[[ToolCall, int], Awaitable[dict[str, Any]]]


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
            result = await execute_tool(decision.tool_call, decision.topology_revision)
            final_text = await self.llm.complete_after_tool(request, decision, result)
        if not final_text:
            final_text = "请检查当前电路连接。"
        await send_json(
            {
                "type": "response.audio.start",
                "session_id": session_id,
                "format": "pcm_s16le",
                "sample_rate_hz": 16000,
                "channels": 1,
                "text": final_text,
            }
        )
        audio_bytes = 0
        async for chunk in self.tts.synthesize(final_text):
            audio_bytes += len(chunk)
            await send_audio(chunk)
        await send_json(
            {
                "type": "response.audio.done",
                "session_id": session_id,
                "audio_bytes": audio_bytes,
            }
        )
