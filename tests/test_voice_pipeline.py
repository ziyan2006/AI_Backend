import asyncio

from tuco_ai_backend.models import CircuitSnapshot, DecisionResponse, ToolCall
from tuco_ai_backend.tools import HighlightPortsArgs
from tuco_ai_backend.voice_pipeline import VoicePipeline


class FakeAsr:
    async def transcribe(self, pcm: bytes) -> str:
        assert pcm == b"pcm"
        return "为什么灯不亮"


class FakeLlm:
    async def decide(self, request):
        return DecisionResponse(
            topology_revision=request.circuit.topology_revision,
            assistant_text="请把输入积木接到输出积木。",
            tool_call=ToolCall(
                call_id="call-1",
                name="highlight_ports",
                arguments=HighlightPortsArgs(
                    ports=[8, 21], duration_ms=1000, pattern="pulse", reason="检查接线"
                ),
            ),
        )

class FakeTts:
    async def synthesize(self, text: str):
        assert text == "请把输入积木接到输出积木。"
        yield b"audio-1"
        yield b"audio-2"



async def test_voice_pipeline_runs_asr_tool_and_tts() -> None:
    sent_json = []
    sent_audio = []
    tool_commands = []
    playback_waits = []

    async def send_json(payload):
        sent_json.append(payload)

    async def send_audio(payload):
        sent_audio.append(payload)

    async def execute_tool(tool_call, topology_revision):
        tool_commands.append((tool_call, topology_revision))
        return {"ok": True, "message": "done"}

    async def wait_for_playback(session_id):
        playback_waits.append(session_id)

    circuit = CircuitSnapshot(schema_version=1, topology_revision=42)
    pipeline = VoicePipeline(FakeAsr(), FakeLlm(), FakeTts())
    await pipeline.run(
        session_id="session-1",
        pcm=b"pcm",
        circuit=circuit,
        send_json=send_json,
        send_audio=send_audio,
        execute_tool=execute_tool,
        wait_for_playback=wait_for_playback,
    )

    assert [message["type"] for message in sent_json] == [
        "asr.result",
        "response.started",
        "response.audio.start",
        "response.audio.done",
        "device.command",
        "response.done",
    ]
    await asyncio.sleep(0)
    assert tool_commands[0][1] == 42
    assert playback_waits == ["session-1"]
    assert sent_audio == [b"audio-1", b"audio-2"]

