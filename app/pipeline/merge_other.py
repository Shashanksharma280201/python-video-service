"""Ported from youtube-clone/src/lib/pipeline/mergeOther.ts."""

from app.pipeline.types import TaggedSegment


def merge_orphan_other(segments: list[TaggedSegment], max_secs: float = 8) -> list[TaggedSegment]:
    """Absorb a short lone "other" segment into its neighbours' phase.

    A brief "other" flanked by two segments sharing the same non-"other" tag is
    almost always filler mid-task, and would otherwise surface as its own
    chapter. Longer "other" runs are real content and are left alone.
    """
    out: list[TaggedSegment] = []
    for i, seg in enumerate(segments):
        if seg["mainTag"] != "other":
            out.append(seg)
            continue

        prev = segments[i - 1] if i > 0 else None
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        short = seg["end"] - seg["start"] < max_secs

        if (
            short
            and prev
            and nxt
            and prev["mainTag"] != "other"
            and prev["mainTag"] == nxt["mainTag"]
        ):
            out.append({**seg, "mainTag": prev["mainTag"]})
        else:
            out.append(seg)
    return out
