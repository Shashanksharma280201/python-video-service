"""Ported from youtube-clone/src/lib/pipeline/alignSegmentTags.ts."""

from app.pipeline.types import TaggedSegment, VideoSegment


def align_segment_tags(
    segments: list[TaggedSegment], chapters: list[VideoSegment]
) -> list[TaggedSegment]:
    """Give each transcript segment the phase of the chapter it sits in.

    Chapters are the source of truth for the phase label: they are consolidated
    and (after reassignment) free of "other". A transcript segment sits inside
    exactly one chapter, so it should carry that chapter's phase — otherwise the
    transcript view contradicts the chapter list it was built from.

    Only mainTag is propagated; each segment keeps its own subTag, which
    describes that specific moment.
    """
    if not chapters:
        return segments

    out: list[TaggedSegment] = []
    for seg in segments:
        chapter = next(
            (c for c in chapters if seg["start"] >= c["start"] and seg["start"] < c["end"]),
            None,
        )
        if not chapter or not chapter["mainTag"]:
            out.append(seg)
        else:
            out.append({**seg, "mainTag": chapter["mainTag"]})
    return out
