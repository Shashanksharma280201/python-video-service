"""Speech-to-text via OpenAI Whisper (whisper-1).

Ported from youtube-clone/src/lib/pipeline/transcribe.ts.

Stays on whisper-1 deliberately: no GPT-5 speech-to-text model exists, and the
gpt-4o-transcribe models refuse verbose_json, so they return no per-segment
timestamps — which chapters, thumbnails and chunk boundaries all depend on.

On a 429 this raises a typed RateLimitedError carrying the retry-after seconds,
so the orchestrator can pace durably instead of failing the video.
"""

import logging
import math
import re
import unicodedata
from typing import Any

from app.pipeline.openai_client import get_client
from app.pipeline.types import (
    LOGPROB_THRESH,
    MIN_REAL_TEXT_CHARS,
    NO_SPEECH_PROB_THRESH,
    RawSegment,
)
from app.pipeline.usage import record_whisper

log = logging.getLogger(__name__)

_RETRY_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)


class RateLimitedError(Exception):
    def __init__(self, message: str, retry_after_secs: float):
        super().__init__(message)
        self.retry_after_secs = retry_after_secs


def parse_retry_after(headers: dict[str, str] | None, message: str) -> int:
    """Seconds to wait before retrying, from the header or the message text."""
    raw = (headers or {}).get("retry-after")
    if raw:
        try:
            secs = int(raw)
            if secs > 0:
                return secs
        except (TypeError, ValueError):
            pass

    m = _RETRY_RE.search(message or "")
    if m:
        minutes = int(m.group(1) or 0)
        seconds = math.ceil(float(m.group(2) or 0))
        return minutes * 60 + seconds

    return 600  # fallback: 10 minutes


def _is_letter_or_number(ch: str) -> bool:
    """Unicode-aware equivalent of the \\p{L}\\p{N} test.

    An ASCII-only version of this check silently discarded every non-Latin
    segment, leaving Hindi, Arabic and Chinese videos with an empty transcript.
    """
    return unicodedata.category(ch)[0] in ("L", "N")


def is_hallucination(seg: RawSegment, total_duration: float) -> bool:
    """Drop ambient noise, music and tool sounds that Whisper reported as speech.

    A high no_speech_prob ALONE does not mean silence. Whisper is routinely
    unsure whether non-English audio is speech (~0.9) while being perfectly
    confident in the text it produced (avg_logprob ~-0.4). Dropping on that
    signal alone discarded 31 of 35 real Hindi segments, so BOTH signals must be
    bad — the same pair Whisper's own reference implementation uses.
    """
    no_speech = (seg.get("no_speech_prob") or 0) >= NO_SPEECH_PROB_THRESH
    low_confidence = (seg.get("avg_logprob") or 0) < LOGPROB_THRESH
    if no_speech and low_confidence:
        return True
    if seg["start"] >= total_duration:
        return True
    real = sum(1 for ch in seg["text"] if _is_letter_or_number(ch))
    return real < MIN_REAL_TEXT_CHARS


def transcribe_audio_file(file_path: str, audio_secs: float | None = None) -> list[RawSegment]:
    """Transcribe one audio file. Timestamps are relative to that file.

    `audio_secs` is the length of audio being SENT, used only for cost
    accounting — Whisper bills per minute submitted, not per minute of speech
    returned. Falls back to the last segment's end when not supplied.
    """
    try:
        with open(file_path, "rb") as fh:
            result = get_client().audio.transcriptions.create(
                file=fh,
                model="whisper-1",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
    except Exception as err:
        if getattr(err, "status", None) == 429 or getattr(err, "status_code", None) == 429:
            message = _error_message(err)
            raise RateLimitedError(
                f"OpenAI rate limit: {message or 'rate limit exceeded'}",
                parse_retry_after(_error_headers(err), message),
            ) from err
        raise

    segs = getattr(result, "segments", None) or []
    billed = audio_secs if audio_secs is not None else (segs[-1].end if segs else 0)
    record_whisper(billed)

    return [
        {
            "id": s.id,
            "start": s.start,
            "end": s.end,
            "text": s.text.strip(),
            "no_speech_prob": getattr(s, "no_speech_prob", 0) or 0,
            "avg_logprob": getattr(s, "avg_logprob", 0) or 0,
        }
        for s in segs
    ]


def _error_message(err: Any) -> str:
    inner = getattr(err, "error", None)
    if isinstance(inner, dict):
        return str(inner.get("message", ""))
    return str(getattr(err, "message", "") or err)


def _error_headers(err: Any) -> dict[str, str]:
    headers = getattr(err, "headers", None)
    if headers is None:
        response = getattr(err, "response", None)
        headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers).items()}
    except (TypeError, ValueError):
        return {}
