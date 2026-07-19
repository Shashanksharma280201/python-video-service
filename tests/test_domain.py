"""Building the machine-maintenance guide.

Ported from youtube-clone/src/lib/pipeline/domain.ts, which had no vitest
coverage.

The guide is the highest-value output of the pipeline and the one a technician
will physically act on, so the failure behaviour matters more than the happy
path: extraction NEVER raises, and a failure yields an empty guide rather than
blocking chapters and the transcript from being saved.
"""

import json

import pytest

from app.pipeline import openai_client as oc
from app.pipeline.domain import (
    CHUNK_CHARS,
    SINGLE_PASS_CHARS,
    extract_domain_data,
    fmt_clock,
    merge_domains,
)
from app.pipeline.domain_types import empty_domain
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
        self.model = "gpt-5.4"
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1}


class FakeClient:
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


def tx(start, text, main_tag="repair", sub_tag="s"):
    return {
        "id": int(start),
        "start": start,
        "end": start + 5,
        "text": text,
        "mainTag": main_tag,
        "subTag": sub_tag,
    }


def ch(start, end, main_tag="repair", sub_tag="doing a thing"):
    return {
        "mainTag": main_tag,
        "subTag": sub_tag,
        "start": start,
        "end": end,
        "thumbnailPath": None,
    }


GUIDE = {
    "machine": "Lube Pump",
    "summary": "Diagnosing low flow.",
    "overview": "The pump moves oil.",
    "troubleshooting": [{"title": "Low flow", "symptom": "Gauge reads low"}],
    "tools": ["Spanner"],
}


# ─── fmt_clock ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secs,expected",
    [
        (0, "0:00"),
        (5, "0:05"),
        (65, "1:05"),
        (600, "10:00"),
        (3661, "61:01"),  # over an hour stays in minutes, as Node does
        (12.9, "0:12"),  # floors, never rounds up past the moment
    ],
)
def test_formats_a_clock_timestamp(secs, expected):
    assert fmt_clock(secs) == expected


def test_negative_times_clamp_to_zero():
    assert fmt_clock(-10) == "0:00"


# ─── merge_domains ────────────────────────────────────────────────────────────


def test_merging_keeps_the_first_non_empty_scalar():
    a = {**empty_domain(), "machine": "", "summary": "First summary"}
    b = {**empty_domain(), "machine": "Pump", "summary": "Second summary"}
    out = merge_domains([a, b])
    assert out["machine"] == "Pump"
    assert out["summary"] == "First summary"


def test_merging_concatenates_sections():
    a = {**empty_domain(), "troubleshooting": [{"title": "A"}]}
    b = {**empty_domain(), "troubleshooting": [{"title": "B"}]}
    assert len(merge_domains([a, b])["troubleshooting"]) == 2


def test_merging_deduplicates_by_title():
    """Overlapping transcript chunks describe the same fault twice."""
    a = {**empty_domain(), "troubleshooting": [{"title": "Low flow"}]}
    b = {**empty_domain(), "troubleshooting": [{"title": "low FLOW"}]}
    assert len(merge_domains([a, b])["troubleshooting"]) == 1


def test_merging_deduplicates_error_codes_by_code():
    a = {**empty_domain(), "errorCodes": [{"code": "E-041", "title": "Overtemp"}]}
    b = {**empty_domain(), "errorCodes": [{"code": "E-041", "title": "Over temperature"}]}
    assert len(merge_domains([a, b])["errorCodes"]) == 1


def test_merging_deduplicates_flat_string_lists():
    a = {**empty_domain(), "tools": ["Spanner", "Multimeter"]}
    b = {**empty_domain(), "tools": ["Spanner", "Torque wrench"]}
    assert sorted(merge_domains([a, b])["tools"]) == ["Multimeter", "Spanner", "Torque wrench"]


def test_merging_deduplicates_glossary_by_term():
    a = {**empty_domain(), "glossary": [{"term": "Trunnion", "definition": "A"}]}
    b = {**empty_domain(), "glossary": [{"term": "trunnion", "definition": "B"}]}
    assert len(merge_domains([a, b])["glossary"]) == 1


def test_merging_drops_items_with_no_key():
    a = {**empty_domain(), "troubleshooting": [{"title": ""}, {"title": "Real"}]}
    assert len(merge_domains([a])["troubleshooting"]) == 1


def test_merging_nothing_yields_an_empty_guide():
    assert merge_domains([]) == empty_domain()


# ─── extract_domain_data ──────────────────────────────────────────────────────


def test_builds_a_guide_from_transcript_and_chapters(fake):
    fake(json.dumps(GUIDE))
    out = extract_domain_data([tx(10, "The pump is leaking")], [ch(0, 60)], 60)
    assert out["machine"] == "Lube Pump"
    assert out["troubleshooting"][0]["title"] == "Low flow"


def test_no_speech_and_no_chapters_skips_the_model_entirely(fake):
    """Nothing to describe — do not spend a flagship call on it."""
    client = fake(json.dumps(GUIDE))
    assert extract_domain_data([], [], 60) == empty_domain()
    assert client.requests == []


def test_a_silent_video_with_chapters_still_runs(fake):
    client = fake(json.dumps(GUIDE))
    out = extract_domain_data([], [ch(0, 60)], 60)
    assert out["machine"] == "Lube Pump"
    assert "no speech" in client.requests[0]["messages"][1]["content"]


def test_a_model_failure_yields_an_empty_guide_not_an_exception(fake):
    """The guide must never block chapters and the transcript from saving."""
    fake(RuntimeError("boom"))
    assert extract_domain_data([tx(10, "text")], [ch(0, 60)], 60) == empty_domain()


def test_malformed_json_yields_an_empty_guide(fake):
    fake("not json at all")
    assert extract_domain_data([tx(10, "text")], [ch(0, 60)], 60) == empty_domain()


def test_a_contentless_guide_becomes_the_empty_guide(fake):
    fake(json.dumps({"machine": "Pump"}))
    assert extract_domain_data([tx(10, "text")], [ch(0, 60)], 60) == empty_domain()


def test_the_user_message_carries_timestamps_for_video_seeking(fake):
    client = fake(json.dumps(GUIDE))
    extract_domain_data([tx(65, "Check the filter")], [ch(0, 60)], 60)
    user = client.requests[0]["messages"][1]["content"]
    assert "[1:05 | 65s] Check the filter" in user


def test_the_user_message_lists_numbered_chapters(fake):
    client = fake(json.dumps(GUIDE))
    extract_domain_data([tx(10, "t")], [ch(0, 60, "diagnosis", "checking flow")], 60)
    user = client.requests[0]["messages"][1]["content"]
    assert "1. [0:00 | 0s] diagnosis — checking flow" in user


def test_blank_transcript_segments_are_ignored(fake):
    client = fake(json.dumps(GUIDE))
    extract_domain_data([tx(10, "   "), tx(20, "Real text")], [ch(0, 60)], 60)
    user = client.requests[0]["messages"][1]["content"]
    assert "Real text" in user
    assert "[0:10" not in user


def test_the_guide_uses_the_flagship_model(fake):
    """This is the one call worth the flagship — it produces the whole guide."""
    client = fake(json.dumps(GUIDE))
    extract_domain_data([tx(10, "t")], [ch(0, 60)], 60)
    assert client.requests[0]["model"] == "gpt-5.4"


def test_a_normal_length_video_is_one_pass(fake):
    client = fake(json.dumps(GUIDE))
    extract_domain_data([tx(10, "short transcript")], [ch(0, 60)], 60)
    assert len(client.requests) == 1


def test_a_very_long_transcript_is_chunked_and_merged(fake):
    """A 4hr+ video exceeds one context window, so it is split and merged."""
    long_text = "x" * (SINGLE_PASS_CHARS + CHUNK_CHARS)
    client = fake(
        json.dumps({**GUIDE, "troubleshooting": [{"title": "Fault A"}]}),
        json.dumps({**GUIDE, "troubleshooting": [{"title": "Fault B"}]}),
    )
    out = extract_domain_data([tx(10, long_text)], [ch(0, 60)], 60)

    assert len(client.requests) > 1
    titles = {t["title"] for t in out["troubleshooting"]}
    assert titles == {"Fault A", "Fault B"}
