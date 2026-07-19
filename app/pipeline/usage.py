"""Token + cost accounting for every OpenAI call in the pipeline.

Ported from youtube-clone/src/lib/pipeline/usage.ts.

Turns "we estimate ~$X per video" into the ACTUAL tokens and dollars. Token
counts are EXACT — straight from the response `usage` object. Cost is derived
from the rate table below; edit it to match your account and the dollars become
exact too.

Scope: the running total is per PROCESS. A Celery worker handling one video end
to end makes that the video's total; if stages are spread across workers, the
per-call log lines are the source of truth.

This module never raises. Accounting must never break a real transcription.
"""

import logging
import math
from dataclasses import dataclass, replace

log = logging.getLogger(__name__)


# ── Rate card (USD per 1M tokens). EDIT to your account's real prices. ─────────
@dataclass(frozen=True)
class ChatPrice:
    inp: float
    out: float


CHAT_PRICES: dict[str, ChatPrice] = {
    # Placeholder GPT-5-tier rates — replace with your billed rates.
    "gpt-5.4": ChatPrice(inp=1.25, out=10.0),
    "gpt-5.4-mini": ChatPrice(inp=0.25, out=2.0),
    # Fallback models (known public rates).
    "gpt-4o": ChatPrice(inp=2.5, out=10.0),
    "gpt-4o-mini": ChatPrice(inp=0.15, out=0.6),
}

# Whisper is priced per minute of audio sent (real/public rate).
WHISPER_PER_MIN = 0.006


@dataclass
class UsageTotals:
    chat_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    whisper_calls: int = 0
    whisper_seconds: float = 0.0
    usd: float = 0.0


_totals = UsageTotals()


def price_for(model: str) -> ChatPrice | None:
    """Look up a price row, tolerating dated ids like "gpt-5.4-mini-2026-03-17".

    The LONGEST matching prefix wins. Both "gpt-5.4" and "gpt-5.4-mini" prefix a
    dated mini id; without the length sort the flagship matched first and mini
    calls were billed at ~5x their real rate.
    """
    if model in CHAT_PRICES:
        return CHAT_PRICES[model]
    matches = [k for k in CHAT_PRICES if model.startswith(k)]
    if not matches:
        return None
    return CHAT_PRICES[max(matches, key=len)]


def _money(n: float) -> str:
    return f"${n:.6f}"


def record_chat(model: str, usage: dict | None = None, label: str = "chat") -> None:
    """Record one chat completion.

    `model` must be the model that ACTUALLY ran (response.model), so a silent
    fallback is priced correctly rather than at the requested model's rate.
    """
    try:
        prompt = int((usage or {}).get("prompt_tokens") or 0)
        completion = int((usage or {}).get("completion_tokens") or 0)
        price = price_for(model)
        cost = (prompt / 1e6) * price.inp + (completion / 1e6) * price.out if price else 0.0

        _totals.chat_calls += 1
        _totals.prompt_tokens += prompt
        _totals.completion_tokens += completion
        _totals.usd += cost

        cost_str = _money(cost) if price else "? (no rate for model)"
        log.info(
            "[usage] chat step=%s model=%s prompt=%d completion=%d total=%d cost=%s "
            "| run: calls=%d tokens=%d cost=%s",
            label,
            model,
            prompt,
            completion,
            prompt + completion,
            cost_str,
            _totals.chat_calls,
            _totals.prompt_tokens + _totals.completion_tokens,
            _money(_totals.usd),
        )
    except Exception:  # noqa: BLE001 — accounting must never break the pipeline
        pass


def record_whisper(seconds: float, label: str = "whisper") -> None:
    """Record one Whisper transcription of `seconds` of audio."""
    try:
        secs = seconds if math.isfinite(seconds) and seconds > 0 else 0.0
        cost = (secs / 60) * WHISPER_PER_MIN

        _totals.whisper_calls += 1
        _totals.whisper_seconds += secs
        _totals.usd += cost

        log.info(
            "[usage] whisper step=%s seconds=%.1f min=%.2f cost=%s | run: whisperMin=%.2f cost=%s",
            label,
            secs,
            secs / 60,
            _money(cost),
            _totals.whisper_seconds / 60,
            _money(_totals.usd),
        )
    except Exception:  # noqa: BLE001
        pass


def usage_snapshot() -> UsageTotals:
    """A COPY of the running totals — callers must not mutate the live object."""
    return replace(_totals)


def reset_usage() -> None:
    """Clear the running totals. Called at the start of a video."""
    global _totals
    _totals = UsageTotals()


def log_usage_total(tag: str = "video") -> None:
    """Print one clear summary block. Call once at the end of a run."""
    t = _totals
    tokens = t.prompt_tokens + t.completion_tokens
    log.info(
        "\n==================== USAGE TOTAL (%s) ====================\n"
        "  GPT chat calls   : %d\n"
        "  prompt tokens    : %d\n"
        "  completion tokens: %d\n"
        "  chat tokens total: %d\n"
        "  Whisper calls    : %d  (%.2f min audio)\n"
        "  ESTIMATED COST   : %s   (tokens are exact; cost uses editable rates)\n"
        "=============================================================\n",
        tag,
        t.chat_calls,
        t.prompt_tokens,
        t.completion_tokens,
        tokens,
        t.whisper_calls,
        t.whisper_seconds / 60,
        _money(t.usd),
    )
