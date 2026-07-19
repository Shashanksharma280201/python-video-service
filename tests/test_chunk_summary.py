"""Ported from youtube-clone/tests/chunkSummary.test.ts.

parse_chunk_summaries never raises: a malformed model response degrades every
chunk to empty values rather than failing the video.
"""

import json

from app.pipeline.chunk_summary import parse_chunk_summaries


def test_reads_title_summary_and_tools_per_chunk_by_index():
    raw = json.dumps(
        {
            "chunks": [
                {
                    "i": 0,
                    "title": "Loosen cap nut",
                    "summary": "Loosen the cap nut.",
                    "tools": ["14mm spanner"],
                },
                {"i": 1, "title": "Check oil level", "summary": "Check oil level.", "tools": []},
            ]
        }
    )
    assert parse_chunk_summaries(raw, 2) == [
        {
            "title": "Loosen cap nut",
            "summarizedText": "Loosen the cap nut.",
            "tools": ["14mm spanner"],
        },
        {"title": "Check oil level", "summarizedText": "Check oil level.", "tools": []},
    ]


def test_falls_back_to_empty_values_on_malformed_output():
    assert parse_chunk_summaries("not json", 2) == [
        {"title": "", "summarizedText": "", "tools": []},
        {"title": "", "summarizedText": "", "tools": []},
    ]


def test_fills_gaps_for_chunks_the_model_omitted():
    raw = json.dumps({"chunks": [{"i": 0, "title": "Step A", "summary": "A", "tools": ["x"]}]})
    assert parse_chunk_summaries(raw, 2) == [
        {"title": "Step A", "summarizedText": "A", "tools": ["x"]},
        {"title": "", "summarizedText": "", "tools": []},
    ]


def test_defaults_title_to_empty_when_the_model_omits_it():
    raw = json.dumps({"chunks": [{"i": 0, "summary": "no title here", "tools": []}]})
    assert parse_chunk_summaries(raw, 1) == [
        {"title": "", "summarizedText": "no title here", "tools": []}
    ]


def test_non_string_tools_are_discarded():
    """The model occasionally emits objects in the tools array; never trust it."""
    raw = json.dumps(
        {"chunks": [{"i": 0, "title": "t", "summary": "s", "tools": ["ok", 5, None, {"a": 1}]}]}
    )
    assert parse_chunk_summaries(raw, 1)[0]["tools"] == ["ok"]


def test_values_are_trimmed():
    raw = json.dumps(
        {"chunks": [{"i": 0, "title": "  t  ", "summary": "  s  ", "tools": ["  sp  "]}]}
    )
    assert parse_chunk_summaries(raw, 1) == [{"title": "t", "summarizedText": "s", "tools": ["sp"]}]


def test_falls_back_to_positional_order_when_indices_are_absent():
    raw = json.dumps({"chunks": [{"title": "A", "summary": "a"}, {"title": "B", "summary": "b"}]})
    out = parse_chunk_summaries(raw, 2)
    assert [c["title"] for c in out] == ["A", "B"]


def test_a_non_list_chunks_field_degrades_to_empties():
    assert parse_chunk_summaries(json.dumps({"chunks": "nope"}), 1) == [
        {"title": "", "summarizedText": "", "tools": []}
    ]


def test_zero_count_returns_empty():
    assert parse_chunk_summaries(json.dumps({"chunks": []}), 0) == []
