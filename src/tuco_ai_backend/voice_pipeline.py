import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from tuco_ai_backend.models import CircuitSnapshot, DecisionRequest, ToolCall

LOGGER = logging.getLogger(__name__)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]
ExecuteTool = Callable[[ToolCall, int], Awaitable[dict[str, Any]]]
WaitForPlayback = Callable[[str], Awaitable[None]]


class VoicePipeline:
    def __init__(self, asr: Any, llm: Any, tts: Any, *, audio_capture: Any | None = None) -> None:
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.audio_capture = audio_capture

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
        history: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
    ) -> None:
        tr_id = trace_id or f"tr_{int(time.time())}"
        LOGGER.info("[%s] [ASR] 开始对输入音频进行语音识别 transcribe...", tr_id)
        text = await self.asr.transcribe(pcm)
        LOGGER.info("[%s] [ASR] 语音识别完成。识别结果: \"%s\"", tr_id, text)

        await send_json(
            {"type": "asr.result", "session_id": session_id, "text": text, "is_final": True}
        )
        request = DecisionRequest(question=text, circuit=circuit)
        
        try:
            decision = await self.llm.decide(request, history=history, trace_id=tr_id)
        except TypeError:
            try:
                decision = await self.llm.decide(request, trace_id=tr_id)
            except TypeError:
                try:
                    decision = await self.llm.decide(request, history=history)
                except TypeError:
                    decision = await self.llm.decide(request)

        await send_json({"type": "response.started", "session_id": session_id})

        final_text = decision.assistant_text
        if decision.tool_call is not None:
            if not final_text:
                final_text = "好呀，我为你亮灯提示。"
                LOGGER.warning(
                    "[%s] [FALLBACK] 触发兜底: 模型 Content 为空但包含 ToolCall, 选用兜底文案: %s",
                    tr_id,
                    final_text,
                )
            
            await self._speak(
                session_id=session_id,
                text=final_text,
                send_json=send_json,
                send_audio=send_audio,
                await_playback=True,
                trace_id=tr_id,
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
            try:
                await execute_tool(decision.tool_call, decision.topology_revision)
            except TimeoutError:
                pass
            await send_json({"type": "response.done", "session_id": session_id})
            return

        if not final_text:
            final_text = "请检查当前电路连接。"
            LOGGER.warning(
                "[%s] [FALLBACK] 触发兜底: 模型未返回回复文本且无 ToolCall, 选用兜底文案: %s",
                tr_id,
                final_text,
            )

        await self._speak(
            session_id=session_id,
            text=final_text,
            send_json=send_json,
            send_audio=send_audio,
            await_playback=False,
            trace_id=tr_id,
        )

    async def _speak(
        self,
        *,
        session_id: str,
        text: str,
        send_json: SendJson,
        send_audio: SendAudio,
        await_playback: bool,
        trace_id: str,
    ) -> None:
        LOGGER.info("[%s] [TTS] 开始请求 TTS 音频合成。播报文本: \"%s\"", trace_id, text)
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
        captured_audio = bytearray()
        async for chunk in self.tts.synthesize(text):
            audio_bytes += len(chunk)
            if self.audio_capture is not None:
                captured_audio.extend(chunk)
            await send_audio(chunk)
        
        if self.audio_capture is not None:
            await self.audio_capture.capture_pcm(
                direction="output", session_id=session_id, pcm=bytes(captured_audio)
            )
        
        LOGGER.info("[%s] [TTS] TTS 音频合成与传输完成。音频大小: %d 字节", trace_id, audio_bytes)
        await send_json(
            {
                "type": "response.audio.done",
                "session_id": session_id,
                "audio_bytes": audio_bytes,
                "await_playback": await_playback,
            }
        )
