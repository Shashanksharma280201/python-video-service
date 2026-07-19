"""Token + cost accounting.

Ported from youtube-clone/src/lib/pipeline/usage.ts.

Token counts are EXACT — they come from the OpenAI response `usage` object.
Cost is derived from an editable rate table.

The longest-prefix test below guards a real bug: `startswith` matched "gpt-5.4"
before "gpt-5.4-mini", so 62 of 105 mini calls were priced at flagship rates and
inflated the reported cost by ~17%.
"""

import pytest

from app.pipeline.usage import (
    CHAT_PRICES,
    UsageTotals,
    price_for,
    record_chat,
    record_whisper,
    reset_usage,
    usage_snapshot,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_usage()
    yield
    reset_usage()


# ─── price lookup ─────────────────────────────────────────────────────────────


def test_exact_model_ids_are_priced_directly():
    assert price_for("gpt-5.4") == CHAT_PRICES["gpt-5.4"]
    assert price_for("gpt-5.4-mini") == CHAT_PRICES["gpt-5.4-mini"]


def test_the_longest_matching_prefix_wins():
    """A dated mini id must never be priced as the flagship."""
    assert price_for("gpt-5.4-mini-2026-03-17") == CHAT_PRICES["gpt-5.4-mini"]
    assert price_for("gpt-5.4-mini-2026-03-17") != CHAT_PRICES["gpt-5.4"]


def test_dated_flagship_ids_still_match_the_flagship():
    assert price_for("gpt-5.4-2026-03-17") == CHAT_PRICES["gpt-5.4"]


def test_dated_fallback_ids_are_priced_correctly():
    assert price_for("gpt-4o-mini-2024-07-18") == CHAT_PRICES["gpt-4o-mini"]
    assert price_for("gpt-4o-2024-08-06") == CHAT_PRICES["gpt-4o"]


def test_an_unknown_model_has_no_price():
    assert price_for("some-other-model") is None


# ─── chat accounting ──────────────────────────────────────────────────────────


def test_records_exact_token_counts():
    record_chat("gpt-5.4", {"prompt_tokens": 1000, "completion_tokens": 500}, "tag")
    t = usage_snapshot()
    assert t.chat_calls == 1
    assert t.prompt_tokens == 1000
    assert t.completion_tokens == 500


def test_cost_uses_the_rate_table():
    record_chat("gpt-5.4", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, "tag")
    # gpt-5.4: $1.25 in + $10.00 out per 1M
    assert usage_snapshot().usd == pytest.approx(11.25)


def test_a_dated_mini_id_is_not_priced_as_the_flagship():
    """The regression itself: this is what inflated cost by ~17%."""
    record_chat(
        "gpt-5.4-mini-2026-03-17", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    )
    # mini: $0.25 in + $2.00 out, NOT $1.25 + $10.00
    assert usage_snapshot().usd == pytest.approx(2.25)


def test_totals_accumulate_across_calls():
    record_chat("gpt-5.4-mini", {"prompt_tokens": 100, "completion_tokens": 50})
    record_chat("gpt-5.4-mini", {"prompt_tokens": 200, "completion_tokens": 100})
    t = usage_snapshot()
    assert t.chat_calls == 2
    assert t.prompt_tokens == 300
    assert t.completion_tokens == 150


def test_an_unknown_model_records_tokens_but_no_cost():
    record_chat("mystery-model", {"prompt_tokens": 1000, "completion_tokens": 1000})
    t = usage_snapshot()
    assert t.prompt_tokens == 1000
    assert t.usd == 0


def test_missing_usage_is_tolerated():
    """A response without a usage object must not break the pipeline."""
    record_chat("gpt-5.4", None)
    assert usage_snapshot().chat_calls == 1


def test_partial_usage_is_tolerated():
    record_chat("gpt-5.4", {"prompt_tokens": 100})
    t = usage_snapshot()
    assert t.prompt_tokens == 100
    assert t.completion_tokens == 0


def test_accounting_never_raises_on_garbage_input():
    """Accounting must never break a real transcription."""
    record_chat("gpt-5.4", {"prompt_tokens": "not a number"})  # type: ignore[dict-item]
    record_chat(None)  # type: ignore[arg-type]
    assert isinstance(usage_snapshot(), UsageTotals)


# ─── whisper accounting ───────────────────────────────────────────────────────


def test_whisper_is_priced_per_minute():
    record_whisper(60)
    assert usage_snapshot().usd == pytest.approx(0.006)


def test_whisper_seconds_accumulate():
    record_whisper(30)
    record_whisper(90)
    t = usage_snapshot()
    assert t.whisper_calls == 2
    assert t.whisper_seconds == 120


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf")])
def test_invalid_durations_are_treated_as_zero(bad):
    record_whisper(bad)
    t = usage_snapshot()
    assert t.whisper_seconds == 0
    assert t.usd == 0


def test_chat_and_whisper_costs_combine():
    record_chat("gpt-5.4", {"prompt_tokens": 1_000_000, "completion_tokens": 0})
    record_whisper(60)
    assert usage_snapshot().usd == pytest.approx(1.25 + 0.006)


def test_snapshot_is_a_copy_not_a_live_reference():
    before = usage_snapshot()
    record_chat("gpt-5.4", {"prompt_tokens": 100, "completion_tokens": 0})
    assert before.prompt_tokens == 0
