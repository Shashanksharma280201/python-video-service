"""Machine-maintenance domain layer.

Ported from youtube-clone/src/lib/pipeline/domain.ts.

After the generic pipeline produces a timestamped transcript and chapters, this
runs one flagship pass over them and extracts a self-service DEBUGGING guide:
for every problem or error it writes symptom -> likely cause -> how to check ->
fix steps -> verify -> what to try next, plus preventive maintenance, safety,
tools/parts, specs and a plain-language glossary. Every item carries timestamps
so the player can jump to the exact moment.

Orchestration-agnostic — no Celery or FastAPI imports.
"""

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.pipeline.domain_types import as_domain_data, empty_domain
from app.pipeline.openai_client import chat_complete
from app.pipeline.prompts import DOMAIN_SYSTEM
from app.pipeline.types import TaggedSegment, VideoSegment

log = logging.getLogger(__name__)

# The flagship handles a large context. ~300k chars covers a ~4hr video in ONE
# coherent pass while leaving room for the big JSON output. Longer than this is
# split and merged, which is strictly worse — the model loses cross-chunk
# context — so the threshold is deliberately generous.
SINGLE_PASS_CHARS = 300_000
CHUNK_CHARS = 140_000

DOMAIN_CONCURRENCY = 3


def fmt_clock(sec: float) -> str:
    """Seconds -> M:SS. Floors, so a timestamp never points past its moment."""
    s = max(0, math.floor(sec))
    return f"{s // 60}:{s % 60:02d}"


def _run_pass(user: str) -> dict[str, Any]:
    res = chat_complete(
        messages=[
            {"role": "system", "content": DOMAIN_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        # Rich debug flows plus a glossary need a lot of room.
        max_completion_tokens=16000,
        label="domain-guide",
    )
    content = res.choices[0].message.content or "{}"
    parsed = json.loads(content)
    return parsed if isinstance(parsed, dict) else {}


def _dedupe(items: list[dict]) -> list[dict]:
    """Drop repeats, keyed on whichever identifying field the item carries.

    Overlapping transcript chunks routinely describe the same fault twice.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for x in items:
        key = x.get("code") or x.get("title") or x.get("label") or x.get("term") or ""
        key = key.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


def merge_domains(parts: list[dict]) -> dict:
    """Merge several partial guides (from transcript chunks) into one."""
    merged = empty_domain()

    for p in parts:
        # First non-empty wins for the scalars.
        merged["machine"] = merged["machine"] or p.get("machine", "")
        merged["summary"] = merged["summary"] or p.get("summary", "")
        merged["overview"] = merged["overview"] or p.get("overview", "")
        for key in (
            "machineIntro",
            "preventiveMaintenance",
            "errorCodes",
            "troubleshooting",
            "safety",
            "tools",
            "parts",
            "specs",
            "glossary",
        ):
            merged[key].extend(p.get(key, []))

    for key in (
        "machineIntro",
        "preventiveMaintenance",
        "errorCodes",
        "troubleshooting",
        "safety",
        "specs",
        "glossary",
    ):
        merged[key] = _dedupe(merged[key])

    merged["tools"] = list(dict.fromkeys(merged["tools"]))
    merged["parts"] = list(dict.fromkeys(merged["parts"]))
    return merged


def extract_domain_data(
    transcript_segments: list[TaggedSegment],
    chapters: list[VideoSegment],
    duration: float,
) -> dict:
    """Build the debugging guide from the timestamped transcript + chapters.

    Returns an empty guide on ANY failure. The guide is valuable but optional;
    it must never stop the chapters and transcript from being saved.
    """
    spoken = [s for s in transcript_segments if s.get("text") and s["text"].strip()]
    if not spoken and not chapters:
        return empty_domain()

    full_transcript = "\n".join(
        f"[{fmt_clock(s['start'])} | {round(s['start'])}s] {s['text'].strip()}" for s in spoken
    )

    chapter_list = "\n".join(
        f"{i + 1}. [{fmt_clock(c['start'])} | {round(c['start'])}s] {c['mainTag']} — {c['subTag']}"
        for i, c in enumerate(chapters)
    )

    def wrap(t: str) -> str:
        return (
            f"CHAPTERS:\n{chapter_list or '(none)'}\n\n"
            f"TRANSCRIPT:\n{t or '(no speech — silent/observational video)'}"
        )

    try:
        if len(full_transcript) <= SINGLE_PASS_CHARS:
            return as_domain_data(_run_pass(wrap(full_transcript))) or empty_domain()

        chunks = [
            full_transcript[i : i + CHUNK_CHARS]
            for i in range(0, len(full_transcript), CHUNK_CHARS)
        ]
        with ThreadPoolExecutor(max_workers=DOMAIN_CONCURRENCY) as pool:
            parts = list(
                pool.map(lambda c: as_domain_data(_run_pass(wrap(c))) or empty_domain(), chunks)
            )
        return merge_domains(parts)
    except Exception as err:
        log.error("[domain] extraction failed: %s", err)
        return empty_domain()
