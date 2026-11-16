"""Tests for reading a screen recording without anything installed.

These need the optional ``media`` extra (PyAV for the codec libraries, the
ONNX OCR engine for the text on the frames). They are skipped when it is not
installed, because a deployment without it simply cannot read a recording —
which the API says plainly.

Install the extra with::

    pip install -e ".[media]"
"""

from __future__ import annotations

import asyncio
import io

import pytest

from app.services.media import (
    AvFrameExtractor,
    pyav_available,
)
from app.services.ocr import rapidocr_available
from app.services.video_analysis import (
    group_states,
    read_recording,
    render_timeline,
)

pytestmark = pytest.mark.skipif(
    not pyav_available(),
    reason="the media extra is not installed (pip install -e '.[media]')",
)

# The screens of the recording below. Each one is shown for a few seconds.
SCREENS = [
    "EBM Login  Username  Password  Sign in",
    "EBM Sales  New receipt  Amount  Save",
    "EBM Receipt  Print  Total 1200",
]


def _recording(seconds_per_screen: float = 3.0, size=(640, 240)) -> bytes:
    """Build a small recording that shows a few screens in a row.

    The frames are drawn with Pillow so the OCR engine has real text to read.
    """
    import av
    from PIL import Image, ImageDraw

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        # x264 is not in every FFmpeg build, so the test falls back to the
        # encoder that always is.
        encoder = "libx264"
        try:
            stream = container.add_stream(encoder, rate=2)
        except Exception:  # noqa: BLE001 - the fallback is the point
            encoder = "mpeg4"
            stream = container.add_stream(encoder, rate=2)
        stream.width = size[0]
        stream.height = size[1]
        stream.pix_fmt = "yuv420p"

        for screen in SCREENS:
            image = Image.new("RGB", size, "white")
            draw = ImageDraw.Draw(image)
            draw.text((20, 100), screen, fill="black")
            picture = av.VideoFrame.from_image(image)
            for _ in range(int(seconds_per_screen * 2)):
                for packet in stream.encode(picture):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


def test_a_recording_is_read_into_a_timeline() -> None:
    """The whole point: a recording becomes a sequence of screens and changes."""
    recording = _recording()

    analysis = asyncio.run(
        read_recording(
            recording,
            mime_type="video/mp4",
            extractor=AvFrameExtractor(interval_seconds=2.0, max_frames=20),
            ocr_reader=_fake_ocr,
        )
    )

    assert analysis.frames_read > 0
    assert len(analysis.states) >= 2
    assert len(analysis.transitions) >= 1

    timeline = render_timeline(analysis)
    assert "SCREEN 1" in timeline
    assert "WHAT CHANGED BETWEEN THE SCREENS" in timeline
    # The screens are in the order the recording showed them.
    assert timeline.index("Login") < timeline.index("Sales")


async def _fake_ocr(frame: bytes, *, mime_type: str) -> str:
    """Read the text of a frame with the real OCR engine when it is there."""
    if rapidocr_available():
        from app.services.ocr import build_ocr_engine

        engine = build_ocr_engine()
        if engine is not None:
            return await engine.extract_image(frame, mime_type=mime_type)
    return ""


def test_the_frames_carry_the_time_they_were_recorded_at() -> None:
    extractor = AvFrameExtractor(interval_seconds=2.0, max_frames=20)
    frames = asyncio.run(extractor.timed_frames(_recording(), mime_type="video/mp4"))
    assert frames
    times = [seconds for seconds, _ in frames]
    assert times == sorted(times)
    assert times[0] >= 0.0


def test_the_deployment_reads_a_recording_without_a_system_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyAV carries the codec libraries, so nothing has to be installed."""
    import app.services.media as media

    monkeypatch.setattr(media, "ffmpeg_path", lambda: None)
    extractor = media.build_timed_frame_extractor()
    assert extractor is not None
    assert isinstance(extractor, AvFrameExtractor)


def test_the_screens_of_a_recording_are_grouped() -> None:
    """Two frames of the same screen count once, a different screen counts twice."""
    from app.services.video_analysis import FrameReading

    readings = [
        FrameReading(seconds=0.0, text=SCREENS[0]),
        FrameReading(seconds=1.0, text=SCREENS[0]),
        FrameReading(seconds=4.0, text=SCREENS[1]),
    ]
    states = group_states(readings)
    assert len(states) == 2
    assert states[0].frame_count == 2
