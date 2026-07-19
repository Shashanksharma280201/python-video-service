"""Finding and chunking the unspoken stretches of a video.

Ported from youtube-clone/src/lib/pipeline/gaps.ts. Pure — no I/O.
"""

from app.pipeline.types import (
    MAX_SILENT_CHUNKS,
    SILENCE_MIN_SECS,
    Gap,
    RawSegment,
    SilentWindow,
)


def find_unspoken_gaps(
    silent_windows: list[SilentWindow],
    spoken_segments: list[RawSegment],
    total_duration: float,
) -> list[Gap]:
    """Combine silencedetect windows with the gaps between Whisper segments.

    Two sources because neither alone is sufficient: silencedetect catches
    genuinely quiet videos, while Whisper gaps catch tool-noise videos where the
    silence threshold is never crossed but no one is speaking.
    """
    # Source 1: silence windows that overlap no spoken segment.
    from_silence: list[Gap] = []
    for w in silent_windows:
        end = w["end"] if w["end"] is not None else total_duration
        overlaps = any(s["end"] > w["start"] and s["start"] < end for s in spoken_segments)
        if not overlaps and end - w["start"] >= SILENCE_MIN_SECS:
            from_silence.append({"start": w["start"], "end": end})

    # Source 2: gaps between Whisper segments.
    ordered = sorted(spoken_segments, key=lambda s: s["start"])
    whisper_gaps: list[Gap] = []
    if not ordered:
        whisper_gaps.append({"start": 0, "end": total_duration})
    else:
        if ordered[0]["start"] >= SILENCE_MIN_SECS:
            whisper_gaps.append({"start": 0, "end": ordered[0]["start"]})
        for a, b in zip(ordered, ordered[1:], strict=False):
            if b["start"] - a["end"] >= SILENCE_MIN_SECS:
                whisper_gaps.append({"start": a["end"], "end": b["start"]})
        if total_duration - ordered[-1]["end"] >= SILENCE_MIN_SECS:
            whisper_gaps.append({"start": ordered[-1]["end"], "end": total_duration})

    # Merge + deduplicate by start time (to one decimal, as Node does).
    seen: set[str] = set()
    merged: list[Gap] = []
    for g in [*from_silence, *whisper_gaps]:
        key = f"{g['start']:.1f}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(g)

    return sorted(merged, key=lambda g: g["start"])


def chunk_long_gaps(gaps: list[Gap], chunk_size: float) -> list[Gap]:
    """Split overly long silent gaps into even chunks, capped for safety."""
    chunks: list[Gap] = []
    for gap in gaps:
        if gap["end"] - gap["start"] <= chunk_size:
            chunks.append(gap)
        else:
            t = gap["start"]
            while t < gap["end"]:
                chunks.append({"start": t, "end": min(t + chunk_size, gap["end"])})
                t += chunk_size
    return chunks[:MAX_SILENT_CHUNKS]
