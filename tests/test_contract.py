"""Locks the wire format against the Node service.

The client requires byte-identical response structure. These key sets are
transcribed from youtube-clone/src/lib/videoExtractionResponse.ts and
domain-types.ts. If a rename here silently changes the JSON, these fail.
"""

from datetime import UTC, datetime

from app.schemas.extraction import (
    Chunk,
    DebugItem,
    DomainData,
    DomainMetaData,
    ExtractionResponse,
    FailedResponse,
    GuideItem,
    NotFoundResponse,
    PendingResponse,
    Procedure,
    ProcessingStatusResponse,
    SpecItem,
    Step,
    TranscriptSegment,
)


def keys(model) -> set[str]:
    return set(model.model_dump(by_alias=True).keys())


def test_extraction_response_keys():
    r = ExtractionResponse(resource_id="r-1", status="DONE", created_at=datetime.now(UTC))
    assert keys(r) == {
        "resourceId",
        "machineId",
        "tenantId",
        "status",
        "title",
        "description",
        "createdAt",
        "thumbnailUrl",
        "guide",
        "chunks",
        "chunkCount",
        "transcript",
    }


def test_chunk_keys():
    c = Chunk(chunk_id="v1-0", start=0.0, end=10.0, blob_url="https://x/y.mp4")
    assert keys(c) == {
        "chunkId",
        "start",
        "end",
        "mainTag",
        "subTag",
        "chunkTitle",
        "transcript",
        "summarizedText",
        "tools",
        "thumbnailUrl",
        "blobUrl",
        "videoSummary",
        "domainMetaData",
    }


def test_transcript_segment_keys():
    t = TranscriptSegment(start=0.0, end=1.0, text="hi")
    assert keys(t) == {"start", "end", "text", "mainTag", "subTag"}


def test_guide_keys():
    assert keys(DomainData()) == {
        "machine",
        "summary",
        "overview",
        "machineIntro",
        "preventiveMaintenance",
        "errorCodes",
        "troubleshooting",
        "safety",
        "tools",
        "parts",
        "specs",
        "glossary",
    }


def test_domain_meta_data_is_the_four_field_subset():
    assert keys(DomainMetaData()) == {"machine", "summary", "overview", "machineIntro"}


def test_guide_leaf_keys():
    assert keys(Step()) == {"text", "expected", "visual", "start"}
    assert keys(GuideItem()) == {"title", "detail", "steps", "start"}
    assert keys(SpecItem()) == {"label", "value", "start"}
    assert keys(Procedure()) == {
        "title",
        "detail",
        "steps",
        "tools",
        "difficulty",
        "time",
        "start",
    }
    assert keys(DebugItem()) == {
        "code",
        "title",
        "symptom",
        "story",
        "fix",
        "verify",
        "ifNotResolved",
        "tools",
        "difficulty",
        "time",
        "start",
    }


def test_in_flight_and_error_shapes():
    assert keys(PendingResponse(resource_id="r", status="PROCESSING")) == {
        "resourceId",
        "machineId",
        "tenantId",
        "status",
        "chunks",
        "chunkCount",
    }
    assert keys(ProcessingStatusResponse(resource_id="r", status="PROCESSING")) == {
        "resourceId",
        "machineId",
        "tenantId",
        "status",
        "pollAfterMs",
    }
    assert keys(FailedResponse(resource_id="r")) == {
        "resourceId",
        "machineId",
        "tenantId",
        "status",
        "error",
    }
    assert keys(NotFoundResponse(resource_id="r")) == {"resourceId", "status"}


def test_poll_after_ms_defaults_to_5000():
    r = ProcessingStatusResponse(resource_id="r", status="PROCESSING")
    assert r.model_dump(by_alias=True)["pollAfterMs"] == 5000


def test_created_at_matches_javascript_date_tojson():
    """Node emits 2026-07-06T14:22:10.000Z. Pydantic's default does not."""
    r = ExtractionResponse(
        resource_id="r-1",
        status="DONE",
        created_at=datetime(2026, 7, 6, 14, 22, 10, 0, tzinfo=UTC),
    )
    assert r.model_dump(by_alias=True)["createdAt"] == "2026-07-06T14:22:10.000Z"


def test_created_at_keeps_three_digit_milliseconds():
    r = ExtractionResponse(
        resource_id="r-1",
        status="DONE",
        created_at=datetime(2026, 7, 6, 14, 22, 10, 123456, tzinfo=UTC),
    )
    assert r.model_dump(by_alias=True)["createdAt"] == "2026-07-06T14:22:10.123Z"


def test_naive_datetime_is_treated_as_utc():
    r = ExtractionResponse(
        resource_id="r-1", status="DONE", created_at=datetime(2026, 7, 6, 14, 22, 10)
    )
    assert r.model_dump(by_alias=True)["createdAt"] == "2026-07-06T14:22:10.000Z"
