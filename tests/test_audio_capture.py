import subprocess

from tuco_ai_backend.audio_capture import AudioCaptureStore


async def test_audio_capture_encodes_pcm_to_mp3(tmp_path, monkeypatch) -> None:
    def fake_run(command, **kwargs):
        assert kwargs["input"] == b"\x01\x02" * 160
        assert command[:2] == ["ffmpeg", "-hide_banner"]
        output = command[-1]
        with open(output, "wb") as stream:
            stream.write(b"fake mp3")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    store = AudioCaptureStore(
        enabled=True,
        directory=str(tmp_path),
        retention_days=7,
        max_files=5,
    )

    await store.capture_pcm(direction="input", session_id="session-1", pcm=b"\x01\x02" * 160)

    captures = store.list_captures()
    assert len(captures) == 1
    assert "_input_session-1.mp3" in captures[0]["name"]
    assert store.resolve_capture(captures[0]["name"]) is not None
    assert store.resolve_capture("../private.mp3") is None


async def test_disabled_audio_capture_does_not_write_files(tmp_path) -> None:
    store = AudioCaptureStore(
        enabled=False,
        directory=str(tmp_path),
        retention_days=7,
        max_files=5,
    )

    await store.capture_pcm(direction="input", session_id="session-1", pcm=b"\x00\x00")

    assert store.list_captures() == []
