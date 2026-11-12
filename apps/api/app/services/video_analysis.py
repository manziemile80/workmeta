"""Video analysis: turning a screen recording into a sequence of actions.

A recording of someone doing a task is not a document. It is a **sequence**:
the person is in one screen, they do something, and they are in the next
screen. The action is the difference between the two.

So a recording is analysed in three steps:

1. the frames are read with OCR, and each one keeps the time it was
   recorded at;
2. neighbouring frames that show the same screen are grouped into one **UI
   state**, so a screen that sat still for twenty seconds counts once;
3. the change between two states is a **transition** — what the screen
   looked like before, and what it looked like after.

What the model receives is therefore a timeline of states and transitions,
not a pile of OCR text. That is the difference between a summary of a video
and a set of actions somebody can be taught to repeat.

The frame timing and the state grouping are deterministic, so they are
tested without a provider. The model is only asked to name the actions, and
it is told that the material is untrusted data.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.services.media import (
    MediaCapabilityUnavailable,
    TimestampedFrameExtractor,
    build_timed_frame_extractor,
)

logger = logging.getLogger(__name__)

# Frames are compared after this normalisation, so "Sales  Screen" and
# "sales screen" count as the same screen.
_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^a-z0-9 ]+")

# Two screens count as the same when this much of their text matches.
SIMILARITY_THRESHOLD = 0.82

# The most frames one recording contributes. A recording that shows fifty
# different screens is not a task anybody can be taught.
MAX_STATES = 40


@dataclass(slots=True)
class FrameReading:
    """One frame of a recording and the text that was on it."""

    seconds: float
    text: str


@dataclass(slots=True)
class UiState:
    """A screen the recording stayed on."""

    index: int
    seconds: float
    text: str
    frame_count: int = 1


@dataclass(slots=True)
class Transition:
    """What changed between two screens."""

    seconds: float
    before: str
    after: str


@dataclass(slots=True)
class VideoAnalysis:
    """The result of reading a recording."""

    states: list[UiState] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    frames_read: int = 0
    duration_seconds: float = 0.0

    @property
    def action_count(self) -> int:
        return len(self.transitions)


def normalise_text(text: str) -> str:
    """Reduce text to its comparable form."""
    lowered = (text or "").lower()
    lowered = _NON_WORD.sub(" ", lowered)
    return _WHITESPACE.sub(" ", lowered).strip()


def similarity(left: str, right: str) -> float:
    """How much two screens look alike, from 0 (nothing alike) to 1."""
    first = set(normalise_text(left).split())
    second = set(normalise_text(right).split())
    if not first and not second:
        return 1.0
    if not first or not second:
        return 0.0
    shared = len(first & second)
    return shared / len(first | second)


def group_states(
    readings: list[FrameReading],
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[UiState]:
    """Group frames that show the same screen into one state.

    A screen that did not change is one state, no matter how many frames it
    filled. That keeps a recording of one long task short enough for a model
    to read.
    """
    states: list[UiState] = []
    for reading in readings:
        text = (reading.text or "").strip()
        if states and similarity(states[-1].text, text) >= threshold:
            current = states[-1]
            current.frame_count += 1
            # The fullest text seen on this screen is kept: a form that was
            # filled in while the recording ran is more useful to the model
            # than the empty one it started with.
            if text and len(text) > len(current.text):
                current.text = text
            continue
        if len(states) >= MAX_STATES:
            break
        states.append(
            UiState(
                index=len(states) + 1,
                seconds=reading.seconds,
                text=text,
            )
        )
    return states


def build_transitions(states: list[UiState]) -> list[Transition]:
    """The change between each screen and the next."""
    transitions: list[Transition] = []
    for index in range(len(states) - 1):
        before = states[index]
        after = states[index + 1]
        transitions.append(
            Transition(
                seconds=after.seconds,
                before=before.text,
                after=after.text,
            )
        )
    return transitions


def render_timeline(analysis: VideoAnalysis) -> str:
    """The recording as text: the screens and what changed between them.

    This is what the model reads. It is deliberately a *timeline* so the
    model can see the order the actions happened in.
    """
    if not analysis.states:
        return ""

    lines: list[str] = []
    for state in analysis.states:
        screen = state.text.strip() or "(no readable text on this screen)"
        lines.append(f"SCREEN {state.index} at {_format_time(state.seconds)}:")
        lines.append(screen)

    if analysis.transitions:
        lines.append("")
        lines.append("WHAT CHANGED BETWEEN THE SCREENS:")
        for index, transition in enumerate(analysis.transitions, start=1):
            lines.append(
                f"CHANGE {index} at {_format_time(transition.seconds)}:"
            )
            lines.append(f"  before: {transition.before.strip() or '(nothing readable)'}")
            lines.append(f"  after:  {transition.after.strip() or '(nothing readable)'}")

    return "\n".join(lines).strip()


def _format_time(seconds: float) -> str:
    minutes, remainder = divmod(max(0.0, seconds), 60)
    return f"{int(minutes)}:{remainder:04.1f}"


async def read_recording(
    data: bytes,
    *,
    mime_type: str,
    extractor: TimestampedFrameExtractor | None = None,
    ocr_reader=None,
) -> VideoAnalysis:
    """Read a recording into a timeline of screens and transitions.

    ``ocr_reader`` is called with the bytes of one frame and returns the text
    on it. It is passed in so the caller can supply the OCR engine, which is
    an optional capability of the deployment.
    """
    if extractor is None:
        extractor = build_timed_frame_extractor()
    if extractor is None:
        raise MediaCapabilityUnavailable(
            "Reading screen recordings needs ffmpeg on this deployment. "
            "Upload the manual or the screenshots instead."
        )
    if ocr_reader is None:
        from app.services.ocr import get_ocr_engine

        ocr = get_ocr_engine()
        if ocr is None:
            raise MediaCapabilityUnavailable(
                "Reading screen recordings needs OCR on this deployment. "
                "Upload the manual or the screenshots instead."
            )
        ocr_reader = ocr.extract_image

    frames = await extractor.timed_frames(data, mime_type=mime_type)
    if not frames:
        raise MediaCapabilityUnavailable(
            "No frames could be read from the screen recording."
        )
    logger.info(
        "recording_frames_extracted",
        frames=len(frames),
        first_seconds=round(frames[0][0], 2),
        last_seconds=round(frames[-1][0], 2),
        extractor=getattr(extractor, "name", "unknown"),
    )

    readings: list[FrameReading] = []
    for seconds, frame in frames:
        text = ""
        try:
            text = await ocr_reader(frame, mime_type="image/png")
        except Exception as exc:  # noqa: BLE001 - one unreadable frame is not fatal
            logger.warning(
                "recording_frame_unreadable",
                seconds=round(seconds, 2),
                reason=str(exc)[:300],
            )
            text = ""
        readings.append(FrameReading(seconds=seconds, text=(text or "").strip()))

    states = group_states(readings)
    transitions = build_transitions(states)
    duration = max((reading.seconds for reading in readings), default=0.0)
    return VideoAnalysis(
        states=states,
        transitions=transitions,
        frames_read=len(readings),
        duration_seconds=duration,
    )
