"""Golden-file parity against the real Node service.

`tests/fixtures/node_extraction_response.json` is a genuine DONE response
captured from the deployed Node service on Azure (resource `azure-newvid-001`,
a 15-minute video: 40 chapters, 220 transcript segments). SAS tokens are
redacted; nothing else was altered.

If our Pydantic models cannot round-trip this file byte-for-byte on structure,
the client would see a different shape than it sees today. That is the failure
this file exists to catch.

Phase 5 extends this: run the same video through the Python service and diff the
live output against this fixture structurally.
"""

import json
from pathlib import Path

import pytest

from app.schemas.extraction import Chunk, DomainData, ExtractionResponse, TranscriptSegment

FIXTURE = Path(__file__).parent / "fixtures" / "node_extraction_response.json"


@pytest.fixture(scope="module")
def node() -> dict:
    return json.loads(FIXTURE.read_text())


def aliases(model_cls) -> set[str]:
    return {model_cls.model_fields[f].alias or f for f in model_cls.model_fields}


def test_top_level_keys_match_exactly(node):
    assert aliases(ExtractionResponse) == set(node.keys())


def test_chunk_keys_match_exactly_for_every_chunk(node):
    expected = aliases(Chunk)
    for i, c in enumerate(node["chunks"]):
        assert set(c.keys()) == expected, f"chunk {i} differs"


def test_transcript_keys_match_exactly_for_every_segment(node):
    expected = aliases(TranscriptSegment)
    for i, t in enumerate(node["transcript"]):
        assert set(t.keys()) == expected, f"transcript segment {i} differs"


def test_guide_keys_match_exactly(node):
    assert aliases(DomainData) == set(node["guide"].keys())


def test_real_response_round_trips_without_loss(node):
    out = ExtractionResponse.model_validate(node).model_dump(by_alias=True, mode="json")

    assert len(out["chunks"]) == len(node["chunks"]) == 40
    assert len(out["transcript"]) == len(node["transcript"]) == 220
    assert out["createdAt"] == node["createdAt"]
    assert out["status"] == "DONE"

    # Chapter payloads survive intact — these are what the client actually reads.
    for mine, theirs in zip(out["chunks"], node["chunks"], strict=True):
        assert mine["chunkId"] == theirs["chunkId"]
        assert mine["chunkTitle"] == theirs["chunkTitle"]
        assert mine["start"] == theirs["start"]
        assert mine["end"] == theirs["end"]
        assert mine["summarizedText"] == theirs["summarizedText"]
        assert mine["tools"] == theirs["tools"]

    guide = out["guide"]
    assert len(guide["troubleshooting"]) == len(node["guide"]["troubleshooting"])
    assert len(guide["safety"]) == len(node["guide"]["safety"])
    assert len(guide["glossary"]) == len(node["guide"]["glossary"])


def test_every_chunk_has_an_llm_written_title(node):
    """chunkTitle was the most recent contract addition — guard it explicitly."""
    out = ExtractionResponse.model_validate(node).model_dump(by_alias=True, mode="json")
    assert all(c["chunkTitle"] for c in out["chunks"])
