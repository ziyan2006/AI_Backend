from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

LOGGER = logging.getLogger(__name__)
_CAPTURE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.mp3$")


class AudioCaptureStore:
    """Stores selected protocol PCM payloads as short-lived, admin-only MP3 files."""

    def __init__(
        self,
        *,
        enabled: bool,
        directory: str,
        retention_days: int,
        max_files: int,
        ffmpeg_binary: str = "ffmpeg",
    ) -> None:
        self.enabled = enabled
        self.directory = Path(directory).resolve()
        self.retention_days = retention_days
        self.max_files = max_files
        self.ffmpeg_binary = ffmpeg_binary

    async def capture_pcm(self, *, direction: str, session_id: str, pcm: bytes) -> None:
        if not self.enabled or not pcm:
            return
        await asyncio.to_thread(self._capture_pcm, direction, session_id, pcm)

    def list_captures(self) -> list[dict[str, int | str]]:
        if not self.directory.exists():
            return []
        files = sorted(
            (path for path in self.directory.glob("*.mp3") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "created_at_unix": int(path.stat().st_mtime),
            }
            for path in files
        ]

    def resolve_capture(self, name: str) -> Path | None:
        if not _CAPTURE_NAME.fullmatch(name):
            return None
        path = (self.directory / name).resolve()
        if path.parent != self.directory or not path.is_file():
            return None
        return path

    def _capture_pcm(self, direction: str, session_id: str, pcm: bytes) -> None:
        if direction not in {"input", "output"}:
            raise ValueError("unsupported capture direction")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._cleanup()
            stem = self._safe_stem(session_id)
            name = f"{int(time.time() * 1000)}_{direction}_{stem}.mp3"
            destination = self.directory / name
            temporary = self.directory / f".{name}.{uuid4().hex}.tmp"
            result = subprocess.run(
                [
                    self.ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-i",
                    "pipe:0",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "64k",
                    "-f",
                    "mp3",
                    "-y",
                    str(temporary),
                ],
                input=pcm,
                capture_output=True,
                check=False,
                timeout=20,
            )
            if result.returncode != 0 or not temporary.is_file():
                detail = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(detail or "ffmpeg did not create an MP3 file")
            temporary.replace(destination)
            LOGGER.info(
                "audio capture saved: direction=%s session=%s bytes=%d", direction, stem, len(pcm)
            )
        except Exception as exc:
            LOGGER.warning(
                "audio capture failed: direction=%s error=%s", direction, type(exc).__name__
            )
        finally:
            if "temporary" in locals() and temporary.exists():
                temporary.unlink(missing_ok=True)

    def _cleanup(self) -> None:
        cutoff = time.time() - self.retention_days * 24 * 60 * 60
        files = sorted(
            (path for path in self.directory.glob("*.mp3") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in files:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        for path in files[self.max_files - 1 :]:
            path.unlink(missing_ok=True)

    @staticmethod
    def _safe_stem(session_id: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80]
        return cleaned or "unknown"
