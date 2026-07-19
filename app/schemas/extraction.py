"""The frozen API contract.

These models ARE the deliverable. The client consumes this exact shape from the
Node service today, so any change here is a breaking change — not a refactor.

Source of truth being mirrored:
  youtube-clone/src/lib/videoExtractionResponse.ts
  youtube-clone/src/lib/pipeline/domain-types.ts

tests/test_contract.py asserts the emitted key set field-for-field. If you change
a field name here, that test fails — which is the point.
"""

from datetime import datetime

from pydantic import field_serializer

from app.schemas.base import WireModel, to_node_iso

# ─── machine guide (DomainData) ───────────────────────────────────────────────


class Step(WireModel):
    """One action in a procedure. `visual` is vision-derived: where on screen."""

    text: str = ""
    expected: str = ""
    visual: str = ""
    start: float | None = None


class GuideItem(WireModel):
    """A titled note — machine intro, safety. `steps` are plain strings here."""

    title: str = ""
    detail: str = ""
    steps: list[str] = []
    start: float | None = None


class DebugItem(WireModel):
    """A guided fix for a problem or error code, told as a teaching story."""

    code: str = ""
    title: str = ""
    symptom: str = ""
    story: str = ""
    fix: list[Step] = []
    verify: str = ""
    if_not_resolved: str = ""
    tools: list[str] = []
    difficulty: str = ""
    time: str = ""
    start: float | None = None


class Procedure(WireModel):
    """A routine preventive-maintenance procedure."""

    title: str = ""
    detail: str = ""
    steps: list[Step] = []
    tools: list[str] = []
    difficulty: str = ""
    time: str = ""
    start: float | None = None


class SpecItem(WireModel):
    label: str = ""
    value: str = ""
    start: float | None = None


class GlossaryTerm(WireModel):
    term: str = ""
    definition: str = ""


class DomainData(WireModel):
    """The full 12-field machine guide, persisted on Video.domain_data."""

    machine: str = ""
    summary: str = ""
    overview: str = ""
    machine_intro: list[GuideItem] = []
    preventive_maintenance: list[Procedure] = []
    error_codes: list[DebugItem] = []
    troubleshooting: list[DebugItem] = []
    safety: list[GuideItem] = []
    tools: list[str] = []
    parts: list[str] = []
    specs: list[SpecItem] = []
    glossary: list[GlossaryTerm] = []


EMPTY_DOMAIN = DomainData()


class DomainMetaData(WireModel):
    """The 4-field subset echoed on every chunk.

    Kept for backward compatibility — `guide` at the top level is the complete
    structure. Dropping this would break existing callers.
    """

    machine: str = ""
    summary: str = ""
    overview: str = ""
    machine_intro: list[GuideItem] = []


# ─── chunks + transcript ──────────────────────────────────────────────────────


class Chunk(WireModel):
    """One chapter of the video.

    `chunk_id` is `{video.id}-{index}` — index-based, so it is NOT stable across
    reprocessing. Callers needing durable references should key on start/end.
    """

    chunk_id: str
    start: float
    end: float
    main_tag: str = ""
    sub_tag: str = ""
    # Short LLM-written label for what this chapter is about.
    chunk_title: str = ""
    transcript: str = ""
    summarized_text: str = ""
    tools: list[str] = []
    thumbnail_url: str | None = None
    blob_url: str
    video_summary: str = ""
    domain_meta_data: DomainMetaData = DomainMetaData()


class TranscriptSegment(WireModel):
    start: float
    end: float
    text: str
    main_tag: str = ""
    sub_tag: str = ""


# ─── responses ────────────────────────────────────────────────────────────────


class ExtractionResponse(WireModel):
    """The complete result — returned on DONE by both /videoExtraction and
    /response-status."""

    resource_id: str
    machine_id: str | None = None
    tenant_id: str | None = None
    status: str
    title: str = ""
    description: str = ""
    created_at: datetime
    thumbnail_url: str | None = None
    guide: DomainData = EMPTY_DOMAIN
    chunks: list[Chunk] = []
    chunk_count: int = 0
    transcript: list[TranscriptSegment] = []

    @field_serializer("created_at")
    def _created_at(self, dt: datetime) -> str:
        return to_node_iso(dt)


class PendingResponse(WireModel):
    """202 from /videoExtraction while work is in flight."""

    resource_id: str
    machine_id: str | None = None
    tenant_id: str | None = None
    status: str
    chunks: list[Chunk] = []
    chunk_count: int = 0


class ProcessingStatusResponse(WireModel):
    """200 from /response-status while work is in flight."""

    resource_id: str
    machine_id: str | None = None
    tenant_id: str | None = None
    status: str
    poll_after_ms: int = 5000


class FailedResponse(WireModel):
    resource_id: str
    machine_id: str | None = None
    tenant_id: str | None = None
    status: str = "FAILED"
    error: str = "processing failed"


class NotFoundResponse(WireModel):
    resource_id: str
    status: str = "NOT_FOUND"


class ErrorResponse(WireModel):
    error: str
