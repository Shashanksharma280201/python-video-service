"""Ported from youtube-clone/src/lib/pipeline/consolidate.ts."""

import math

from app.pipeline.types import MAX_CHAPTERS, TaggedSegment


def consolidate_chapters(
    segments: list[TaggedSegment], max_chapters: int = MAX_CHAPTERS
) -> list[TaggedSegment]:
    """Fold hundreds of fine-grained segments into a handful of navigable chapters.

    Only the chapter list is consolidated — the full-resolution transcript is
    kept separately. Fewer chapters also means far fewer thumbnails to extract
    and upload (304 -> ~40), which is what keeps the frames step small.
    """
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: s["start"])

    # Pass 1: merge consecutive segments sharing a phase. A run of "diagnosis"
    # lines becomes one Diagnosis chapter.
    chapters: list[TaggedSegment] = []
    for s in ordered:
        last = chapters[-1] if chapters else None
        if last and last["mainTag"] == s["mainTag"]:
            last["end"] = max(last["end"], s["end"])
        else:
            # Copy: the caller's segments must not be mutated, and the first
            # segment's subTag becomes the chapter label.
            chapters.append({**s})

    # Pass 2: hard cap — repeatedly fold the shortest chapter into a neighbour
    # so a choppy video cannot explode the count.
    while len(chapters) > max_chapters:
        min_idx = 0
        min_dur = math.inf
        for i, c in enumerate(chapters):
            d = c["end"] - c["start"]
            if d < min_dur:
                min_dur = d
                min_idx = i

        neighbour = min_idx - 1 if min_idx > 0 else min_idx + 1
        lo, hi = min(min_idx, neighbour), max(min_idx, neighbour)
        chapters[lo]["start"] = min(chapters[lo]["start"], chapters[hi]["start"])
        chapters[lo]["end"] = max(chapters[lo]["end"], chapters[hi]["end"])
        chapters.pop(hi)

    return chapters
