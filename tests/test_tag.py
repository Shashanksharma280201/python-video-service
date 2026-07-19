"""Phase derivation + per-segment tagging.

Ported from youtube-clone/src/lib/pipeline/tag.ts, which had no vitest coverage.
The batch-response parsing is extracted into a pure function here so the failure
modes (omitted indices, malformed JSON, bad casing) can be tested without a
model call — the Node version buried it inline.
"""

import json

import pytest

from app.pipeline import openai_client as oc
from app.pipeline.tag import analyze_video, parse_analysis, parse_tag_batch, tag_segments
from app.pipeline.usage import reset_usage


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]
        self.model = "gpt-5.4-mini"
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1}


class FakeClient:
    """Returns queued responses in order; raises if told to."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.chat = self
        self.requests = []

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        nxt = self._responses.pop(0) if self._responses else "{}"
        if isinstance(nxt, Exception):
            raise nxt
        return FakeResponse(nxt)


@pytest.fixture(autouse=True)
def _reset():
    reset_usage()
    oc.reset_fallback_state()
    yield
    reset_usage()
    oc.reset_fallback_state()


@pytest.fixture
def fake(monkeypatch):
    def install(*responses):
        client = FakeClient(responses)
        monkeypatch.setattr(oc, "get_client", lambda: client)
        return client

    return install


def raw(i, text="hello there"):
    return {"id": i, "start": i * 10.0, "end": i * 10.0 + 10.0, "text": text}


# ─── parse_analysis ───────────────────────────────────────────────────────────


def test_reads_category_and_phases():
    out = parse_analysis(json.dumps({"category": "Laptop Repair", "phases": ["Intro", "Repair"]}))
    assert out == {"category": "Laptop Repair", "phases": ["Intro", "Repair"]}


def test_defaults_category_to_general():
    assert parse_analysis(json.dumps({"phases": ["A"]}))["category"] == "General"


def test_defaults_phases_to_empty():
    assert parse_analysis(json.dumps({"category": "X"}))["phases"] == []


def test_malformed_analysis_degrades_to_defaults():
    assert parse_analysis("not json") == {"category": "General", "phases": []}


def test_non_string_category_degrades_to_general():
    assert parse_analysis(json.dumps({"category": 5}))["category"] == "General"


# ─── parse_tag_batch ──────────────────────────────────────────────────────────


def test_maps_each_index_to_its_tag():
    raw_json = json.dumps(
        {
            "segments": [
                {"i": 0, "m": "introduction", "s": "Overview of the parts"},
                {"i": 1, "m": "diagnosis", "s": "Testing battery voltage"},
            ]
        }
    )
    assert parse_tag_batch(raw_json, 2) == [
        {"mainTag": "introduction", "subTag": "Overview of the parts"},
        {"mainTag": "diagnosis", "subTag": "Testing battery voltage"},
    ]


def test_main_tags_are_lowercased_and_trimmed():
    """The model returns Title Case; stored tags are lowercase."""
    raw_json = json.dumps({"segments": [{"i": 0, "m": "  Introduction  ", "s": " x "}]})
    assert parse_tag_batch(raw_json, 1) == [{"mainTag": "introduction", "subTag": "x"}]


def test_omitted_indices_fall_back_to_other():
    raw_json = json.dumps({"segments": [{"i": 0, "m": "repair", "s": "a"}]})
    assert parse_tag_batch(raw_json, 2)[1] == {"mainTag": "other", "subTag": ""}


def test_malformed_batch_degrades_every_segment_to_other():
    assert parse_tag_batch("not json", 2) == [
        {"mainTag": "other", "subTag": ""},
        {"mainTag": "other", "subTag": ""},
    ]


# ─── analyze_video ────────────────────────────────────────────────────────────


def test_analyze_video_returns_the_parsed_result(fake):
    fake(json.dumps({"category": "Cooking Tutorial", "phases": ["Prep", "Cook"]}))
    assert analyze_video("some transcript") == {
        "category": "Cooking Tutorial",
        "phases": ["Prep", "Cook"],
    }


def test_analyze_video_survives_a_model_failure(fake):
    """A failed analysis must not fail the video — tagging has a default list."""
    fake(RuntimeError("boom"))
    assert analyze_video("some transcript") == {"category": "General", "phases": []}


def test_analyze_video_uses_the_mini_tier(fake):
    client = fake(json.dumps({"category": "X", "phases": []}))
    analyze_video("t")
    assert client.requests[0]["model"] == "gpt-5.4-mini"


# ─── tag_segments ─────────────────────────────────────────────────────────────


def test_tags_every_segment(fake):
    fake(
        json.dumps(
            {"segments": [{"i": 0, "m": "repair", "s": "a"}, {"i": 1, "m": "repair", "s": "b"}]}
        )
    )
    out = tag_segments([raw(0), raw(1)], ["Repair"])
    assert [s["mainTag"] for s in out] == ["repair", "repair"]
    assert [s["subTag"] for s in out] == ["a", "b"]


def test_tagging_preserves_the_original_segment_fields(fake):
    fake(json.dumps({"segments": [{"i": 0, "m": "repair", "s": "a"}]}))
    out = tag_segments([raw(0, "the actual text")], ["Repair"])
    assert out[0]["text"] == "the actual text"
    assert out[0]["start"] == 0.0
    assert out[0]["end"] == 10.0


def test_empty_input_returns_empty(fake):
    assert tag_segments([], ["Repair"]) == []


def test_a_failed_batch_degrades_to_other(fake):
    fake(RuntimeError("boom"))
    out = tag_segments([raw(0)], ["Repair"])
    assert out[0]["mainTag"] == "other"


def test_the_videos_own_phases_are_offered_to_the_model(fake):
    client = fake(json.dumps({"segments": [{"i": 0, "m": "repair", "s": "a"}]}))
    tag_segments([raw(0)], ["Disassembly", "Repair"])
    system = client.requests[0]["messages"][0]["content"]
    assert "Disassembly, Repair" in system


def test_the_default_phase_list_is_used_when_none_were_derived(fake):
    client = fake(json.dumps({"segments": [{"i": 0, "m": "repair", "s": "a"}]}))
    tag_segments([raw(0)], [])
    system = client.requests[0]["messages"][0]["content"]
    assert "introduction, overview, diagnosis" in system


def test_segments_are_split_into_batches(fake):
    from app.pipeline.types import TAG_BATCH_SIZE

    n = TAG_BATCH_SIZE + 5
    client = fake(*[json.dumps({"segments": []})] * 3)
    tag_segments([raw(i) for i in range(n)], ["Repair"])
    assert len(client.requests) == 2


def test_orphan_other_merging_is_applied_after_tagging(fake):
    """A short lone 'other' between two 'repair' segments is absorbed."""
    fake(
        json.dumps(
            {
                "segments": [
                    {"i": 0, "m": "repair", "s": "a"},
                    {"i": 1, "m": "other", "s": "b"},
                    {"i": 2, "m": "repair", "s": "c"},
                ]
            }
        )
    )
    segs = [
        {"id": 0, "start": 0.0, "end": 10.0, "text": "a"},
        {"id": 1, "start": 10.0, "end": 13.0, "text": "b"},  # 3s orphan
        {"id": 2, "start": 13.0, "end": 25.0, "text": "c"},
    ]
    out = tag_segments(segs, ["Repair"])
    assert [s["mainTag"] for s in out] == ["repair", "repair", "repair"]
