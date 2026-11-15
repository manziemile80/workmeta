"""Training media: screen recordings and audio instructions.

Two capabilities, both optional and both detected at runtime:

* **Video frames** - a screen recording is turned into still frames and each
  frame is read with the OCR engine that already serves the knowledge
  pipeline. Two frame extractors are available: the system ``ffmpeg``
  binary, and **PyAV**, which ships the codec libraries in a wheel and needs
  nothing installed on the machine.
* **Audio transcription** - an audio instruction is transcribed with the AI
  provider.

Neither capability is assumed: when the library or the credential is missing
the material is stored, the API says exactly what is missing, and the client is
told what to upload instead. A material is never silently marked as analysed.
"""

from __future__ import annotations

import asyncio
import io
import re
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

# ``showinfo`` prints the time of each frame it passes, which is how the
# frames of a recording are placed on a timeline.
_SHOWINFO_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


class MediaCapabilityUnavailable(Exception):
    """The deployment cannot process this media kind right now."""


class VideoFrameExtractor(Protocol):
    name: str

    async def frames(self, data: bytes, *, mime_type: str) -> list[bytes]: ...


class TimestampedFrameExtractor(Protocol):
    """Frames with the time they were recorded at."""

    name: str

    async def timed_frames(
        self, data: bytes, *, mime_type: str
    ) -> list[tuple[float, bytes]]: ...


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


class FfmpegTimedFrameExtractor:
    """Extract frames from a recording *with the time they were recorded at*.

    The time is what turns a recording from a pile of pictures into a
    sequence: the difference between two frames is an action the person
    performed.
    """

    name = "ffmpeg"

    def __init__(self, *, binary: str, interval_seconds: float, max_frames: int):
        self.binary = binary
        self.interval_seconds = max(0.5, interval_seconds)
        self.max_frames = max(1, max_frames)

    async def timed_frames(
        self, data: bytes, *, mime_type: str
    ) -> list[tuple[float, bytes]]:
        if not data:
            return []
        command = [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            "pipe:0",
            "-vf",
            f"fps=1/{self.interval_seconds},showinfo",
            "-frames:v",
            str(self.max_frames),
            "-vsync",
            "vfr",
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
        frames = _split_png_frames(stdout)
        times = _parse_frame_times(stderr)
        return _pair_frames_with_times(frames, times, self.interval_seconds)


def _parse_frame_times(stderr: bytes) -> list[float]:
    """The time of each frame, in the order ffmpeg printed them."""
    text = (stderr or b"").decode("utf-8", "replace")
    times: list[float] = []
    for line in text.splitlines():
        if "showinfo" not in line:
            continue
        match = _SHOWINFO_TIME.search(line)
        if match:
            times.append(float(match.group(1)))
    return times


def _pair_frames_with_times(
    frames: list[bytes], times: list[float], interval_seconds: float
) -> list[tuple[float, bytes]]:
    """Attach a time to each frame.

    ffmpeg reports one line per frame it passes, so the times line up with
    the frames. When the report is short or missing, the sampling interval
    is used instead: an approximate time is better than no time, and the
    order of the frames is what the analysis actually relies on.
    """
    paired: list[tuple[float, bytes]] = []
    for index, frame in enumerate(frames):
        if index < len(times):
            seconds = times[index]
        else:
            seconds = index * interval_seconds
        paired.append((seconds, frame))
    return paired


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


def pyav_available() -> bool:
    """Whether PyAV — and the imaging library it needs — is installed."""
    import importlib.util

    return (
        importlib.util.find_spec("av") is not None
        and importlib.util.find_spec("PIL") is not None
    )


class AvFrameExtractor:
    """Extract frames from a recording with PyAV.

    PyAV ships the codec libraries inside its wheel, so a deployment can read
    a screen recording without installing ``ffmpeg`` on the machine. The time
    of each frame comes from the container itself, which is more exact than
    the sampling interval the ``ffmpeg`` command line has to assume.
    """

    name = "pyav"

    def __init__(self, *, interval_seconds: float, max_frames: int):
        self.interval_seconds = max(0.5, interval_seconds)
        self.max_frames = max(1, max_frames)

    def _extract_sync(self, data: bytes) -> list[tuple[float, bytes]]:
        import av

        frames: list[tuple[float, bytes]] = []
        last_kept = -self.interval_seconds
        with av.open(io.BytesIO(data)) as container:
            stream = container.streams.video[0]
            time_base = float(stream.time_base) if stream.time_base else 0.0
            for frame in container.decode(video=0):
                seconds = frame.time
                if seconds is None and frame.pts is not None and time_base:
                    seconds = frame.pts * time_base
                if seconds is None:
                    seconds = last_kept + self.interval_seconds
                if seconds - last_kept < self.interval_seconds:
                    continue
                image = frame.to_image()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                frames.append((float(seconds), buffer.getvalue()))
                last_kept = float(seconds)
                if len(frames) >= self.max_frames:
                    break
        return frames

    async def timed_frames(
        self, data: bytes, *, mime_type: str
    ) -> list[tuple[float, bytes]]:
        if not data:
            return []
        try:
            return await asyncio.to_thread(self._extract_sync, data)
        except Exception as exc:  # noqa: BLE001 - report, never leak the payload
            raise MediaCapabilityUnavailable(
                "The screen recording could not be processed."
            ) from exc

    async def frames(self, data: bytes, *, mime_type: str) -> list[bytes]:
        return [frame for _, frame in await self.timed_frames(data, mime_type=mime_type)]


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
    if binary:
        return FfmpegFrameExtractor(
            binary=binary,
            interval_seconds=settings.media_video_frame_interval_seconds,
            max_frames=settings.media_video_max_frames,
        )
    if pyav_available():
        return AvFrameExtractor(
            interval_seconds=settings.media_video_frame_interval_seconds,
            max_frames=settings.media_video_max_frames,
        )
    return None


def build_timed_frame_extractor(
    settings: Settings | None = None,
) -> TimestampedFrameExtractor | None:
    """The extractor that also reports when each frame was recorded.

    The system ``ffmpeg`` is used when it is installed. Otherwise PyAV is
    used, which needs nothing on the machine. When neither is available the
    deployment cannot read a recording, and it says so rather than pretending
    it did.
    """
    settings = settings or get_settings()
    binary = ffmpeg_path()
    if binary:
        return FfmpegTimedFrameExtractor(
            binary=binary,
            interval_seconds=settings.media_video_frame_interval_seconds,
            max_frames=settings.media_video_max_frames,
        )
    if pyav_available():
        return AvFrameExtractor(
            interval_seconds=settings.media_video_frame_interval_seconds,
            max_frames=settings.media_video_max_frames,
        )
    return None


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
