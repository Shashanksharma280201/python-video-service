"""Shared types + tunable constants for the transcription pipeline.

Ported from youtube-clone/src/lib/pipeline/types.ts.

This module is orchestration-agnostic — no Celery, no FastAPI imports — so the
whole `pipeline/` package could be driven by anything.

Segments are TypedDicts rather than dataclasses on purpose: these structures are
persisted verbatim as JSON into Video.topic_segments / transcript_segments and
read straight back by the response builder. Keeping them dict-native means no
conversion layer can drift, and the camelCase keys match what Node writes.
"""

from typing import NotRequired, TypedDict

from app.config import get_settings


class RawSegment(TypedDict):
    id: int
    start: float
    end: float
    text: str
    # Whisper's confidence that this is NOT speech.
    no_speech_prob: NotRequired[float]
    # Whisper's confidence in the TEXT it produced. Used together with
    # no_speech_prob to spot hallucinations — see is_hallucination().
    avg_logprob: NotRequired[float]


class TaggedSegment(RawSegment):
    mainTag: str
    subTag: str


class VideoSegment(TypedDict):
    mainTag: str
    subTag: str
    start: float
    end: float
    thumbnailPath: str | None
    # Per-chunk enrichment: a short LLM title, a one-line summary, and the tools
    # named in this chunk. Optional so older records still validate.
    title: NotRequired[str]
    summarizedText: NotRequired[str]
    tools: NotRequired[list[str]]


class SilentWindow(TypedDict):
    start: float
    end: float | None


class Gap(TypedDict):
    start: float
    end: float


# ─── tunables ─────────────────────────────────────────────────────────────────


def segment_secs() -> int:
    """How long each video segment is. Tunable for 1-4hr videos."""
    return get_settings().chunk_minutes * 60


WHISPER_LIMIT = 25 * 1024 * 1024  # 25 MB — Whisper's per-file cap
TAG_BATCH_SIZE = 20
THUMB_CONCURRENCY = 6  # parallel ffmpeg frame extractions per step
SILENCE_NOISE_DB = -35  # dB floor — below this counts as silence
SILENCE_MIN_SECS = 3  # ignore gaps shorter than this
SILENCE_CHUNK_SECS = 25  # split long silent gaps into chunks of this size
VISION_BATCH_SIZE = 5  # frames per Vision call
VISION_CONCURRENCY = 3  # parallel Vision calls
MAX_SILENT_CHUNKS = 60  # safety cap for very long silent videos
FULL_SILENT_CHUNK_SECS = 25  # chunk size when the whole video has no speech
NO_SPEECH_PROB_THRESH = 0.6  # "probably not speech" per Whisper
LOGPROB_THRESH = -1.0  # below this, Whisper is unsure of the TEXT it wrote
# A segment is only a hallucination when BOTH signals are bad. Whisper often
# reports a high no_speech_prob for confident non-English speech, so neither
# signal is safe alone.
MIN_REAL_TEXT_CHARS = 4  # fewer real characters than this = hallucination
MAX_CHAPTERS = 40
