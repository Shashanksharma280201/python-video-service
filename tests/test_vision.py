"""Vision description parsing.

Ported from youtube-clone/src/lib/pipeline/vision.ts, which had no vitest
coverage.

parse_descriptions is the important one. Its job is not just to read JSON — it
is to make sure a malformed response NEVER leaks raw JSON text into a chapter
subtitle. A chapter reading `[{"i":1,"desc":"..."}]` on screen is far worse than
one reading "performing task", so every failure path falls back to the generic
phrase.
"""

import json

from app.pipeline.vision import FALLBACK_DESCRIPTION, parse_descriptions


def test_reads_descriptions_from_a_frames_object():
    raw = json.dumps(
        {"frames": [{"i": 1, "desc": "Loosening a bolt"}, {"i": 2, "desc": "Wiping the seal"}]}
    )
    assert parse_descriptions(raw, 2) == ["Loosening a bolt", "Wiping the seal"]


def test_reads_descriptions_from_a_bare_array():
    raw = json.dumps([{"i": 1, "desc": "Loosening a bolt"}])
    assert parse_descriptions(raw, 1) == ["Loosening a bolt"]


def test_strips_markdown_fences():
    """The model wraps JSON in ```json fences when not in json_object mode."""
    raw = '```json\n{"frames":[{"i":1,"desc":"Turning a valve"}]}\n```'
    assert parse_descriptions(raw, 1) == ["Turning a valve"]


def test_strips_a_bare_fence():
    raw = '```\n{"frames":[{"i":1,"desc":"Turning a valve"}]}\n```'
    assert parse_descriptions(raw, 1) == ["Turning a valve"]


def test_recovers_an_array_embedded_in_prose():
    """Last resort: grab the first [...] block out of a chatty response."""
    raw = 'Sure! Here you go:\n[{"i":1,"desc":"Removing a panel"}]\nHope that helps.'
    assert parse_descriptions(raw, 1) == ["Removing a panel"]


def test_frames_are_indexed_from_one_not_zero():
    """The prompt numbers frames 1..N; index 0 must not silently match."""
    raw = json.dumps({"frames": [{"i": 1, "desc": "First"}, {"i": 2, "desc": "Second"}]})
    assert parse_descriptions(raw, 2) == ["First", "Second"]


def test_falls_back_to_positional_order_when_indices_are_absent():
    raw = json.dumps({"frames": [{"desc": "First"}, {"desc": "Second"}]})
    assert parse_descriptions(raw, 2) == ["First", "Second"]


def test_missing_entries_get_the_fallback_phrase():
    raw = json.dumps({"frames": [{"i": 1, "desc": "Only one"}]})
    assert parse_descriptions(raw, 3) == [
        "Only one",
        FALLBACK_DESCRIPTION,
        FALLBACK_DESCRIPTION,
    ]


def test_malformed_output_never_leaks_raw_json_into_a_chapter():
    """The whole point of this function."""
    out = parse_descriptions('[{"i":1,"desc":', 2)
    assert out == [FALLBACK_DESCRIPTION, FALLBACK_DESCRIPTION]
    assert not any("{" in d or "[" in d for d in out)


def test_plain_prose_does_not_become_a_description():
    out = parse_descriptions("I'm sorry, I can't help with that.", 1)
    assert out == [FALLBACK_DESCRIPTION]


def test_an_empty_description_becomes_the_fallback():
    raw = json.dumps({"frames": [{"i": 1, "desc": "   "}]})
    assert parse_descriptions(raw, 1) == [FALLBACK_DESCRIPTION]


def test_a_non_string_description_becomes_the_fallback():
    raw = json.dumps({"frames": [{"i": 1, "desc": 42}]})
    assert parse_descriptions(raw, 1) == [FALLBACK_DESCRIPTION]


def test_descriptions_are_trimmed():
    raw = json.dumps({"frames": [{"i": 1, "desc": "  Tightening a clamp  "}]})
    assert parse_descriptions(raw, 1) == ["Tightening a clamp"]


def test_zero_count_returns_empty():
    assert parse_descriptions(json.dumps({"frames": []}), 0) == []
