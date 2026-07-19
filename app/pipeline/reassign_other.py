"""Reassigning "other" chapters to the video's own phase vocabulary.

Ported from youtube-clone/src/lib/pipeline/reassignOther.ts.
"""

import json
import logging
from typing import Any

from app.pipeline.openai_client import chat_complete
from app.pipeline.prompts import REASSIGN_SYSTEM
from app.pipeline.types import VideoSegment

log = logging.getLogger(__name__)


def parse_reassignments(text: str, count: int, allowed: list[str]) -> list[str]:
    """Map the model's {i, phase} output onto the requested indices.

    Keeps "other" when the model omitted an index or returned a phase outside
    the allowed set, so a bad response can never introduce a made-up label.
    """
    allowed_set = set(allowed)

    arr: list[Any] | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("assignments"), list):
            arr = parsed["assignments"]
    except (ValueError, TypeError):
        arr = None

    out: list[str] = []
    for j in range(count):
        entry: Any = None
        if arr is not None:
            entry = next((x for x in arr if isinstance(x, dict) and x.get("i") == j), None)
            if entry is None and j < len(arr):
                entry = arr[j]

        phase = ""
        if isinstance(entry, dict) and isinstance(entry.get("phase"), str):
            phase = entry["phase"].strip()

        out.append(phase if phase in allowed_set else "other")
    return out


def reassign_other_tags(segments: list[VideoSegment]) -> list[VideoSegment]:
    """Reassign every "other" chapter to the best-fitting phase.

    Draws only from the video's OWN phase vocabulary, using each chapter's
    one-line summary. Chapters that genuinely fit nothing stay "other", and
    correct chapters are never disturbed. One cheap mini call per video.
    """
    phases = list(
        dict.fromkeys(s["mainTag"] for s in segments if s["mainTag"] and s["mainTag"] != "other")
    )
    other_idx = [i for i, s in enumerate(segments) if s["mainTag"] == "other"]

    # Nothing to reassign to, or nothing to reassign.
    if not phases or not other_idx:
        return segments

    payload = [
        {
            "i": j,
            "text": (segments[idx].get("summarizedText") or segments[idx]["subTag"] or "")[:300],
        }
        for j, idx in enumerate(other_idx)
    ]

    try:
        res = chat_complete(
            messages=[
                {"role": "system", "content": REASSIGN_SYSTEM.format(phases=", ".join(phases))},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0,
            max_completion_tokens=800,
            response_format={"type": "json_object"},
            mini=True,
            label="reassign-other",
        )
        content = res.choices[0].message.content or "{}"
        mapped = parse_reassignments(content, len(other_idx), phases)
    except Exception as err:
        log.warning("[reassign_other] failed, keeping 'other': %s", err)
        return segments

    out = list(segments)
    for j, idx in enumerate(other_idx):
        if mapped[j] and mapped[j] != "other":
            out[idx] = {**out[idx], "mainTag": mapped[j]}
    return out
