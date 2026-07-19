"""Per-chunk title, summary and tool extraction.

Ported from youtube-clone/src/lib/pipeline/chunkSummary.ts.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypedDict

from app.pipeline.openai_client import chat_complete
from app.pipeline.prompts import CHUNK_SUMMARY_SYSTEM
from app.pipeline.types import TAG_BATCH_SIZE

log = logging.getLogger(__name__)

SUMMARY_CONCURRENCY = 3


class ChunkInput(TypedDict):
    mainTag: str
    subTag: str
    transcript: str


class ChunkSummary(TypedDict):
    title: str
    summarizedText: str
    tools: list[str]


EMPTY_SUMMARY: ChunkSummary = {"title": "", "summarizedText": "", "tools": []}


def parse_chunk_summaries(text: str, count: int) -> list[ChunkSummary]:
    """Never raises. A malformed response degrades every chunk to empty values."""
    arr: list[Any] | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("chunks"), list):
            arr = parsed["chunks"]
    except (ValueError, TypeError):
        arr = None

    out: list[ChunkSummary] = []
    for j in range(count):
        entry: Any = None
        if arr is not None:
            entry = next((x for x in arr if isinstance(x, dict) and x.get("i") == j), None)
            if entry is None and j < len(arr):
                entry = arr[j]

        if not isinstance(entry, dict):
            out.append({**EMPTY_SUMMARY})
            continue

        title = entry["title"].strip() if isinstance(entry.get("title"), str) else ""
        summary = entry["summary"].strip() if isinstance(entry.get("summary"), str) else ""
        raw_tools = entry.get("tools")
        tools = (
            [t.strip() for t in raw_tools if isinstance(t, str)]
            if isinstance(raw_tools, list)
            else []
        )
        out.append({"title": title, "summarizedText": summary, "tools": tools})
    return out


def _summarize_batch(batch: list[ChunkInput]) -> list[ChunkSummary]:
    payload = [
        {
            "i": i,
            "tag": f"{c['mainTag']} / {c['subTag']}",
            "transcript": c["transcript"][:600],
        }
        for i, c in enumerate(batch)
    ]
    try:
        res = chat_complete(
            messages=[
                {"role": "system", "content": CHUNK_SUMMARY_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0,
            max_completion_tokens=1600,
            response_format={"type": "json_object"},
            mini=True,
            label="chunk-summary",
        )
        content = res.choices[0].message.content or "{}"
        return parse_chunk_summaries(content, len(batch))
    except Exception as err:
        # A failed batch degrades to empties rather than failing the whole video
        # — chapters and the guide still stand.
        log.warning("[chunk_summary] batch failed: %s", err)
        return [{**EMPTY_SUMMARY} for _ in batch]


def summarize_chunks(chunks: list[ChunkInput]) -> list[ChunkSummary]:
    """One batched mini call per group, a few groups in flight at a time."""
    batches = [chunks[i : i + TAG_BATCH_SIZE] for i in range(0, len(chunks), TAG_BATCH_SIZE)]
    if not batches:
        return []

    with ThreadPoolExecutor(max_workers=SUMMARY_CONCURRENCY) as pool:
        results = list(pool.map(_summarize_batch, batches))

    return [s for batch in results for s in batch]
