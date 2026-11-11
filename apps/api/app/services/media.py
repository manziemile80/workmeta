"""Training media: screen recordings and audio instructions.

Two capabilities, both optional and both detected at runtime:

* **Video frames** - a screen recording is turned into still frames with
  ``ffmpeg`` (one frame every few seconds) and each frame is read with the
  OCR engine that already serves the knowledge pipeline.
* **Audio transcription** - an audio instruction is transcribed with the AI
  provider.

Neither capability is assumed: when the binary or the credential is missing
the material is stored, the API says exactly what is missing, and the client is
told what to upload instead. A material is never silently marked as analysed.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Protocol

from app.core.config import Settings, get_settings

VIDEO_MIME_TYPES = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/webm",
        "video/x-matroska",
    }
)

AUDIO_MIME_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/m4a",
    }
)


class MediaCapabilityUnavailable(Exception):
    """The deployment cannot process this media kind right now."""


class VideoFrameExtractor(Protocol):
    name: str

    async def frames(self, data: bytes, *, mime_type: str) -> list[bytes]: ...


class AudioTranscriber(Protocol):
    name: str

    async def transcribe(self, data: bytes, *, filename: str) -> str: ...


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


class FfmpegFrameExtractor:
    """Extract still frames from a screen recording with ffmpeg."""

    name = "ffmpeg"

    def __init__(self, *, binary: str, interval_seconds: float, max_frames: int):
        self.binary = binary
        self.interval_seconds = max(0.5, interval_seconds)
        self.max_frames = max(1, max_frames)

    async def frames(self, data: bytes, *, mime_type: str) -> list[bytes]:
        if not data:
            return []
        command = [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vf",
            f"fps=1/{self.interval_seconds}",
            "-frames:v",
            str(self.max_frames),
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate(data)
        except Exception as exc:  # noqa: BLE001 - never crash the request
            process.kill()
            raise MediaCapabilityUnavailable(
                "The screen recording could not be processed."
            ) from exc
        if process.returncode != 0:
            message = (stderr or b"").decode("utf-8", "replace").strip()
            raise MediaCapabilityUnavailable(
                "The screen recording could not be processed."
                + (f" ({message[:200]})" if message else "")
            )
        return _split_png_frames(stdout)


def _split_png_frames(data: bytes) -> list[bytes]:
    """Split a PNG stream into its individual images."""
    frames: list[bytes] = []
    marker = b"\x89PNG\r\n\x1a\n"
    position = 0
    while True:
        start = data.find(marker, position)
        if start == -1:
            break
        end = data.find(b"IEND", start)
        if end == -1:
            break
        frames.append(data[start : end + 8])
        position = end + 8
    return frames


class OpenAITranscriber:
    """Transcribe audio with the AI provider."""

    name = "openai"

    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def transcribe(self, data: bytes, *, filename: str) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.api_key, base_url=self.base_url, max_retries=0
        )
        try:
            result = await client.audio.transcriptions.create(
                model=self.model,
                file=(filename, data, "application/octet-stream"),
            )
        except Exception as exc:  # noqa: BLE001 - report, never leak the payload
            raise MediaCapabilityUnavailable(
                "The audio instruction could not be transcribed."
            ) from exc
        text = getattr(result, "text", "") or ""
        if not text.strip():
            raise MediaCapabilityUnavailable(
                "The audio instruction did not contain any speech."
            )
        return text


def build_video_extractor(
    settings: Settings | None = None,
) -> VideoFrameExtractor | None:
    settings = settings or get_settings()
    binary = ffmpeg_path()
    if not binary:
        return None
    return FfmpegFrameExtractor(
        binary=binary,
        interval_seconds=settings.media_video_frame_interval_seconds,
        max_frames=settings.media_video_max_frames,
    )


def build_audio_transcriber(
    settings: Settings | None = None,
) -> AudioTranscriber | None:
    settings = settings or get_settings()
    if not settings.ai_transcription_model or not settings.ai_api_key:
        return None
    return OpenAITranscriber(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_transcription_model,
    )
