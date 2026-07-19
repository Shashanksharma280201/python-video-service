"""Derive the video's phases once, then tag each spoken segment into one.

Ported from youtube-clone/src/lib/pipeline/tag.ts.

Tagging needs the WHOLE transcript for a consistent phase vocabulary, so this
runs after every segment has been transcribed.

The response parsing is extracted into pure functions (parse_analysis,
parse_tag_batch) that the Node version kept inline — this is what lets the
failure modes be tested without a model call.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypedDict

from app.pipeline.merge_other import merge_orphan_other
from app.pipeline.openai_client import chat_complete
from app.pipeline.prompts import (
    ANALYZE_VIDEO_SYSTEM,
    TAG_PHASE_HINT_DEFAULT,
    TAG_PHASE_HINT_WITH_PHASES,
    TAG_SEGMENTS_SYSTEM,
)
from app.pipeline.types import TAG_BATCH_SIZE, RawSegment, TaggedSegment

log = logging.getLogger(__name__)

TAG_CONCURRENCY = 3


class Analysis(TypedDict):
    category: str
    phases: list[str]


class Tag(TypedDict):
    mainTag: str
    subTag: str


def parse_analysis(text: str) -> Analysis:
    """Never raises — a failed analysis falls back to the default phase list."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {"category": "General", "phases": []}

    if not isinstance(parsed, dict):
        return {"category": "General", "phases": []}

    category = parsed.get("category")
    phases = parsed.get("phases")
    return {
        "category": category if isinstance(category, str) else "General",
        "phases": phases if isinstance(phases, list) else [],
    }


def parse_tag_batch(text: str, count: int) -> list[Tag]:
    """Map the model's {i, m, s} output onto the batch's indices.

    Anything missing or malformed becomes "other" rather than a made-up label.
    """
    by_index: dict[int, Any] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("segments"), list):
            for x in parsed["segments"]:
                if isinstance(x, dict) and isinstance(x.get("i"), int):
                    by_index[x["i"]] = x
    except (ValueError, TypeError):
        by_index = {}

    out: list[Tag] = []
    for j in range(count):
        entry = by_index.get(j) or {}
        m_raw = entry.get("m")
        s_raw = entry.get("s")
        main = m_raw if isinstance(m_raw, str) else "Other"
        sub = s_raw if isinstance(s_raw, str) else ""
        out.append({"mainTag": main.lower().strip(), "subTag": sub.strip()})
    return out


def analyze_video(sample_text: str) -> Analysis:
    """Derive this video's category and 4-8 phase labels."""
    try:
        res = chat_complete(
            messages=[
                {"role": "system", "content": ANALYZE_VIDEO_SYSTEM},
                {"role": "user", "content": sample_text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            mini=True,
            label="analyze-video",
        )
        return parse_analysis(res.choices[0].message.content or "{}")
    except Exception as err:
        log.warning("[tag] analyze failed, using defaults: %s", err)
        return {"category": "General", "phases": []}


def _tag_batch(batch: list[RawSegment], phase_hint: str) -> list[Tag]:
    payload = [{"i": i, "t": s["text"][:200]} for i, s in enumerate(batch)]
    try:
        res = chat_complete(
            messages=[
                {
                    "role": "system",
                    "content": TAG_SEGMENTS_SYSTEM.format(phase_hint=phase_hint),
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0,
            max_completion_tokens=1600,
            response_format={"type": "json_object"},
            mini=True,
            label="tag-segments",
        )
        return parse_tag_batch(res.choices[0].message.content or "{}", len(batch))
    except Exception as err:
        log.warning("[tag] batch of %d failed -> 'other': %s", len(batch), err)
        return [{"mainTag": "other", "subTag": ""} for _ in batch]


def tag_segments(segments: list[RawSegment], phases: list[str]) -> list[TaggedSegment]:
    """Tag every segment, then absorb short orphan "other" runs."""
    if not segments:
        return []

    phase_hint = (
        TAG_PHASE_HINT_WITH_PHASES.format(phases=", ".join(phases))
        if phases
        else TAG_PHASE_HINT_DEFAULT
    )

    batches = [segments[i : i + TAG_BATCH_SIZE] for i in range(0, len(segments), TAG_BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=TAG_CONCURRENCY) as pool:
        results = list(pool.map(lambda b: _tag_batch(b, phase_hint), batches))

    all_tags = [t for batch in results for t in batch]

    tagged: list[TaggedSegment] = [
        {
            **seg,
            "mainTag": all_tags[i]["mainTag"] if i < len(all_tags) else "other",
            "subTag": all_tags[i]["subTag"] if i < len(all_tags) else "",
        }
        for i, seg in enumerate(segments)
    ]
    return merge_orphan_other(tagged)
