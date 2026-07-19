"""Whisper hallucination filtering + rate-limit parsing.

is_hallucination is ported from youtube-clone/tests/isHallucination.test.ts,
including its two regression cases:

1. The "real characters" test must count letters/digits in ANY script. An
   ASCII-only test silently discarded every non-Latin segment, leaving Hindi,
   Arabic and Chinese videos with a completely empty transcript.

2. A high no_speech_prob ALONE does not mean silence. Whisper is routinely
   unsure whether non-English audio is speech (~0.9) while being confident in
   the text it produced (avg_logprob ~-0.4). Dropping on that signal alone
   discarded 31 of 35 real Hindi segments. BOTH signals must be bad.
"""

import pytest

from app.pipeline.transcribe import RateLimitedError, is_hallucination, parse_retry_after

DURATION = 600


def seg(text, no_speech_prob=0.0, start=0.0, avg_logprob=0.0):
    return {
        "id": 1,
        "start": start,
        "end": start + 2,
        "text": text,
        "no_speech_prob": no_speech_prob,
        "avg_logprob": avg_logprob,
    }


# ─── is_hallucination ─────────────────────────────────────────────────────────


def test_keeps_real_english_speech():
    assert is_hallucination(seg("Start the machine by pressing the button."), DURATION) is False


@pytest.mark.parametrize(
    "text",
    [
        "चाय पॉइंट मशीन को कैसे चालू करें",  # Hindi (Devanagari)
        "كيفية تشغيل الآلة",  # Arabic
        "如何启动机器",  # Chinese
        "コーヒーマシンの使い方",  # Japanese
        "Как запустить машину",  # Russian
    ],
)
def test_keeps_non_latin_speech(text):
    assert is_hallucination(seg(text), DURATION) is False


def test_still_drops_punctuation_only_noise():
    assert is_hallucination(seg("..."), DURATION) is True


def test_drops_empty_text():
    assert is_hallucination(seg(""), DURATION) is True


def test_keeps_confident_speech_even_when_no_speech_prob_is_high():
    assert is_hallucination(seg("इस मेशीन में शुरू करने के लिए", 0.87, 0, -0.37), DURATION) is False


def test_drops_only_when_no_speech_is_high_and_confidence_is_low():
    assert is_hallucination(seg("Some words here", 0.9, 0, -2.5), DURATION) is True


def test_keeps_a_low_confidence_segment_when_whisper_thinks_it_is_speech():
    assert is_hallucination(seg("Some words here", 0.1, 0, -2.5), DURATION) is False


def test_drops_a_segment_starting_past_the_video_duration():
    assert is_hallucination(seg("Some words here", 0, DURATION + 10), DURATION) is True


def test_missing_confidence_fields_are_tolerated():
    assert (
        is_hallucination({"id": 1, "start": 0, "end": 2, "text": "Real words here"}, DURATION)
        is False
    )


def test_digits_count_as_real_characters():
    assert is_hallucination(seg("2024"), DURATION) is False


# ─── parse_retry_after ────────────────────────────────────────────────────────


def test_prefers_the_retry_after_header():
    assert parse_retry_after({"retry-after": "42"}, "") == 42


def test_parses_minutes_and_seconds_from_the_message():
    assert parse_retry_after({}, "Rate limit reached. Please try again in 7m29s.") == 7 * 60 + 29


def test_parses_seconds_only_from_the_message():
    assert parse_retry_after({}, "try again in 12.3s") == 13  # rounded up


def test_falls_back_to_ten_minutes():
    assert parse_retry_after({}, "some other error") == 600


def test_a_non_numeric_header_falls_through_to_the_message():
    assert parse_retry_after({"retry-after": "soon"}, "try again in 5s") == 5


def test_a_zero_header_falls_through():
    assert parse_retry_after({"retry-after": "0"}, "try again in 5s") == 5


def test_rate_limited_error_carries_its_retry_delay():
    err = RateLimitedError("rate limited", 90)
    assert err.retry_after_secs == 90
    assert "rate limited" in str(err)
