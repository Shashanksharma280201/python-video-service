"""Build the client-facing extraction response.

Ported from youtube-clone/src/lib/videoExtractionResponse.ts.

Returns a plain camelCase dict rather than a Pydantic model. The shape is
already exactly what goes over the wire, and building it directly keeps one
translation layer instead of two — app/schemas/extraction.py stays the
authoritative DESCRIPTION of the contract, and tests validate this output
against it.
"""

from collections.abc import Callable
from typing import Any

from app.models import Video
from app.pipeline.domain_types import as_domain_data, empty_domain
from app.schemas.base import to_node_iso
from app.storage import presigned_download_url, s3_key

TTL = 6 * 3600

Signer = Callable[[str], str]


def _default_signer(url: str) -> str:
    return presigned_download_url(s3_key(url), TTL)


def _guide_meta(guide: dict[str, Any] | None) -> dict[str, Any]:
    """The 4-field subset echoed on every chunk, kept for backward
    compatibility. `guide` at the top level is the complete structure."""
    g = guide or {}
    intro = g.get("machineIntro")
    return {
        "machine": g.get("machine", ""),
        "summary": g.get("summary", ""),
        "overview": g.get("overview", ""),
        "machineIntro": intro if isinstance(intro, list) else [],
    }


def build_extraction_response(video: Video, sign: Signer | None = None) -> dict[str, Any]:
    signer = sign or _default_signer

    resource_id = video.external_id or video.id
    tx = video.transcript_segments if isinstance(video.transcript_segments, list) else []
    segs = video.topic_segments if isinstance(video.topic_segments, list) else []

    raw_guide = video.domain_data if isinstance(video.domain_data, dict) else None
    meta = _guide_meta(raw_guide)
    # The full 12-field guide, normalized. Only the 4-field domainMetaData used
    # to reach the caller; the actionable guide was dropped.
    full_guide = as_domain_data(video.domain_data) or empty_domain()
    video_summary = (raw_guide or {}).get("summary", "")

    blob_url_signed = signer(video.blob_url)
    thumbnail_url = signer(video.thumbnail_url) if video.thumbnail_url else None

    chunks = []
    for i, seg in enumerate(segs):
        start, end = seg["start"], seg["end"]
        chunks.append(
            {
                # Index-based, so NOT stable across reprocessing. Callers needing
                # a durable reference should key on start/end.
                "chunkId": f"{video.id}-{i}",
                "start": start,
                "end": end,
                "mainTag": seg.get("mainTag", ""),
                "subTag": seg.get("subTag", ""),
                # Short LLM-written label for what this chapter is about.
                "chunkTitle": seg.get("title") or "",
                "transcript": " ".join(t["text"].strip() for t in tx if start <= t["start"] < end),
                "summarizedText": seg.get("summarizedText") or "",
                "tools": seg.get("tools") or [],
                "thumbnailUrl": signer(seg["thumbnailPath"]) if seg.get("thumbnailPath") else None,
                "blobUrl": blob_url_signed,
                "videoSummary": video_summary,
                "domainMetaData": meta,
            }
        )

    # Full timestamped transcript with its phase tags — the transcript-view
    # data, which the flat per-chunk string does not expose on its own.
    transcript = [
        {
            "start": t["start"],
            "end": t["end"],
            "text": t["text"],
            "mainTag": t.get("mainTag", ""),
            "subTag": t.get("subTag", ""),
        }
        for t in tx
    ]

    return {
        "resourceId": resource_id,
        "machineId": video.machine_id,
        "tenantId": video.tenant_id,
        "status": str(video.transcript_status),
        "title": video.title,
        "description": video.description,
        "createdAt": to_node_iso(video.created_at),
        "thumbnailUrl": thumbnail_url,
        # Video-level machine guide (one per video).
        "guide": full_guide,
        # Chapters, each with its tags, summary, tools and thumbnail.
        "chunks": chunks,
        "chunkCount": len(chunks),
        # Full tagged transcript segments.
        "transcript": transcript,
    }
