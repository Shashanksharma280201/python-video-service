"""Building the client-facing response.

Ported from youtube-clone/src/lib/videoExtractionResponse.ts and its vitest
suite. This is where the frozen contract finally meets real stored data, so the
assertions mirror the Node tests case-for-case.
"""

from datetime import UTC, datetime

import pytest

from app.models import TranscriptStatus, Video
from app.services.extraction_response import build_extraction_response


def sign(url: str) -> str:
    return f"{url}?sig=1"


@pytest.fixture
def video() -> Video:
    return Video(
        id="v1",
        external_id="r-1",
        machine_id="m-1",
        tenant_id="t-1",
        title="KRC Demo",
        description="desc",
        created_at=datetime(2026, 7, 6, 14, 22, 10, tzinfo=UTC),
        blob_url="https://acct.blob.core.windows.net/videosvc/videos/krc.mp4",
        transcript_status=TranscriptStatus.DONE,
        transcript_segments=[
            {"id": 0, "start": 12.4, "end": 15, "text": "Alright, today the lube pump."},
            {"id": 1, "start": 15, "end": 18.9, "text": "It feeds oil to the bearings."},
            {"id": 2, "start": 40, "end": 44, "text": "Next chapter text."},
        ],
        topic_segments=[
            {
                "mainTag": "intro",
                "subTag": "lube overview",
                "start": 12.4,
                "end": 18.9,
                "thumbnailPath": "https://acct.blob.core.windows.net/videosvc/thumbnails/v1/s0.jpg",
                "title": "Lube system overview",
                "summarizedText": "The lube system feeds oil to the bearings.",
                "tools": ["Multimeter"],
            },
            {
                "mainTag": "diagnosis",
                "subTag": "low flow",
                "start": 40,
                "end": 44,
                "thumbnailPath": None,
                "summarizedText": "Check the flow.",
                "tools": [],
            },
        ],
        domain_data={
            "machine": "Lubrication System",
            "summary": "Diagnosing low-lube-flow.",
            "overview": "The lube system keeps parts oiled.",
            "machineIntro": [{"title": "Float switch", "detail": "Detects oil level."}],
        },
    )


# ─── chunks ───────────────────────────────────────────────────────────────────


def test_maps_a_done_video_into_the_chunk_shape(video):
    r = build_extraction_response(video, sign)
    assert r["resourceId"] == "r-1"
    assert r["machineId"] == "m-1"
    assert r["tenantId"] == "t-1"
    assert r["status"] == "DONE"
    assert r["chunkCount"] == 2
    assert len(r["chunks"]) == 2

    c0 = r["chunks"][0]
    assert c0["chunkId"] == "v1-0"
    assert c0["chunkTitle"] == "Lube system overview"
    assert c0["start"] == 12.4
    assert c0["transcript"] == "Alright, today the lube pump. It feeds oil to the bearings."
    assert c0["summarizedText"] == "The lube system feeds oil to the bearings."
    assert c0["tools"] == ["Multimeter"]
    assert c0["thumbnailUrl"] == (
        "https://acct.blob.core.windows.net/videosvc/thumbnails/v1/s0.jpg?sig=1"
    )
    assert c0["blobUrl"] == "https://acct.blob.core.windows.net/videosvc/videos/krc.mp4?sig=1"
    assert c0["videoSummary"] == "Diagnosing low-lube-flow."
    assert c0["domainMetaData"]["machine"] == "Lubrication System"


def test_a_missing_chunk_title_defaults_to_empty(video):
    r = build_extraction_response(video, sign)
    assert r["chunks"][1]["chunkTitle"] == ""


def test_a_null_thumbnail_path_yields_null_not_a_signed_url(video):
    r = build_extraction_response(video, sign)
    assert r["chunks"][1]["thumbnailUrl"] is None


def test_chunk_transcript_only_includes_segments_inside_the_chunk(video):
    """A segment belongs to exactly one chunk — overlap would duplicate text."""
    r = build_extraction_response(video, sign)
    assert "Next chapter text." not in r["chunks"][0]["transcript"]
    assert r["chunks"][1]["transcript"] == "Next chapter text."


# ─── guide ────────────────────────────────────────────────────────────────────


def test_exposes_the_full_machine_guide_at_the_top_level(video):
    r = build_extraction_response(video, sign)
    assert r["guide"]["machine"] == "Lubrication System"
    assert r["guide"]["summary"] == "Diagnosing low-lube-flow."
    assert len(r["guide"]["machineIntro"]) == 1


def test_sections_with_no_data_normalize_to_empty_arrays(video):
    """The client indexes these directly; null would crash it."""
    r = build_extraction_response(video, sign)
    assert r["guide"]["troubleshooting"] == []
    assert r["guide"]["errorCodes"] == []
    assert r["guide"]["preventiveMaintenance"] == []


def test_empty_domain_data_yields_an_empty_guide_not_a_crash(video):
    video.domain_data = None
    r = build_extraction_response(video, sign)
    assert r["chunks"][0]["domainMetaData"] == {
        "machine": "",
        "summary": "",
        "overview": "",
        "machineIntro": [],
    }
    assert r["chunks"][0]["videoSummary"] == ""
    assert r["guide"]["machine"] == ""


# ─── transcript ───────────────────────────────────────────────────────────────


def test_returns_the_full_tagged_transcript(video):
    r = build_extraction_response(video, sign)
    assert len(r["transcript"]) == 3
    assert r["transcript"][0]["start"] == 12.4
    assert r["transcript"][0]["text"] == "Alright, today the lube pump."


def test_transcript_tags_default_to_empty_when_absent(video):
    r = build_extraction_response(video, sign)
    assert r["transcript"][0]["mainTag"] == ""
    assert r["transcript"][0]["subTag"] == ""


def test_transcript_tags_are_carried_through_when_present(video):
    video.transcript_segments = [
        {"start": 0, "end": 5, "text": "t", "mainTag": "intro", "subTag": "opening"}
    ]
    r = build_extraction_response(video, sign)
    assert r["transcript"][0]["mainTag"] == "intro"
    assert r["transcript"][0]["subTag"] == "opening"


# ─── top level ────────────────────────────────────────────────────────────────


def test_signs_the_top_level_thumbnail_when_present(video):
    video.thumbnail_url = "https://acct.blob.core.windows.net/videosvc/thumbnails/v1/s0.jpg"
    r = build_extraction_response(video, sign)
    assert r["thumbnailUrl"] == (
        "https://acct.blob.core.windows.net/videosvc/thumbnails/v1/s0.jpg?sig=1"
    )


def test_a_missing_top_level_thumbnail_is_null(video):
    r = build_extraction_response(video, sign)
    assert r["thumbnailUrl"] is None


def test_uses_external_id_as_resource_id_falling_back_to_id(video):
    video.external_id = None
    assert build_extraction_response(video, sign)["resourceId"] == "v1"


def test_created_at_is_serialized_the_way_node_does(video):
    """The client parses this field; Pydantic's default format differs."""
    assert build_extraction_response(video, sign)["createdAt"] == "2026-07-06T14:22:10.000Z"


def test_empty_segments_yield_an_empty_but_valid_response(video):
    video.topic_segments = None
    video.transcript_segments = None
    r = build_extraction_response(video, sign)
    assert r["chunks"] == []
    assert r["chunkCount"] == 0
    assert r["transcript"] == []


def test_the_response_validates_against_the_frozen_contract(video):
    """Belt and braces: the builder's output must satisfy the Pydantic models."""
    from app.schemas.extraction import ExtractionResponse

    r = build_extraction_response(video, sign)
    parsed = ExtractionResponse.model_validate(r)
    assert parsed.model_dump(by_alias=True, mode="json")["chunkCount"] == 2
