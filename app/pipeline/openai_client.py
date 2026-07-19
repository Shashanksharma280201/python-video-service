"""Shared OpenAI chat client + model selection.

Ported from youtube-clone/src/lib/pipeline/openai.ts. Every chat call in the
pipeline goes through chat_complete(), so this is the ONE place a model is
chosen.

  MODEL       — flagship: the machine guide
  MODEL_MINI  — cheaper:  tagging, chunk summaries, reassignment, chapter search
  VISION      — gpt-5.4-mini

Models are PINNED in code, not read from the environment. The Azure ConfigMap
sets OPENAI_MODEL=gpt-4o and an env var beats a code default, so a default alone
would silently keep the deployment on gpt-4o.

Transcription is unaffected — it stays on whisper-1 (see transcribe.py). No
GPT-5 speech-to-text model exists, and the gpt-4o-transcribe models refuse
verbose_json, so they return no per-segment timestamps, which chapters,
thumbnails and chunk boundaries all depend on.

Token limits: every call sends `max_completion_tokens`, never `max_tokens`.
GPT-5 models reject max_tokens outright; gpt-4o accepts both, so one parameter
works everywhere with no branching.
"""

import logging
from functools import lru_cache
from typing import Any, cast

from openai import OpenAI

from app.config import get_settings
from app.pipeline.usage import record_chat

log = logging.getLogger(__name__)

MODEL = "gpt-5.4"
MODEL_MINI = "gpt-5.4-mini"
FALLBACK = "gpt-4o"
FALLBACK_MINI = "gpt-4o-mini"

# Image calls need a vision-capable model. The FLAGSHIP gpt-5.4 REJECTS
# image_url content ("400 image_url is only supported by certain models") — but
# gpt-5.4-mini ACCEPTS it (verified: it reaches image parsing, not a
# content-type rejection). So vision stays in the 5.4 family on the mini model,
# with gpt-4o as a vision-capable fallback.
VISION_MODEL = "gpt-5.4-mini"
VISION_FALLBACK = "gpt-4o"

# Set the first time a call has to fall back, i.e. the key cannot use the pinned
# model. Reported on /api/health, because "configured model" alone is not proof:
# without this, health would keep claiming gpt-5.4 while every call silently ran
# on gpt-4o — and a model comparison would be measuring nothing.
_fell_back_to: str | None = None


@lru_cache
def get_client() -> OpenAI:
    return OpenAI(api_key=get_settings().openai_api_key)


def reset_fallback_state() -> None:
    global _fell_back_to
    _fell_back_to = None


def active_models() -> dict[str, Any]:
    """What this deployment runs. Surfaced on /api/health so a model switch can
    be verified rather than assumed."""
    return {
        "model": MODEL,
        "modelMini": MODEL_MINI,
        "visionModel": VISION_MODEL,
        "transcription": "whisper-1",
        # None = the pinned models are genuinely being used.
        "fellBackTo": _fell_back_to,
    }


def is_model_unavailable(err: Exception) -> bool:
    """True when the error means this key cannot use the model at all.

    Distinguished from a transient failure, which must NOT silently downgrade
    the model — that would hide an outage behind lower-quality output.
    """
    status = getattr(err, "status", None)
    if status in (403, 404):
        return True

    code = getattr(err, "code", "") or ""
    inner = getattr(err, "error", None)
    if isinstance(inner, dict):
        code = code or inner.get("code", "")
        message = inner.get("message", "")
    else:
        message = getattr(err, "message", "") or ""

    if code == "model_not_found":
        return True

    msg = str(message).lower()
    return any(s in msg for s in ("does not exist", "do not have access", "not available"))


def chat_complete(
    *,
    messages: list[dict[str, Any]],
    mini: bool = False,
    vision: bool = False,
    label: str | None = None,
    **params: Any,
) -> Any:
    """Run a chat completion, falling back if the pinned model is unavailable."""
    global _fell_back_to

    if vision:
        primary, fallback = VISION_MODEL, VISION_FALLBACK
    elif mini:
        primary, fallback = MODEL_MINI, FALLBACK_MINI
    else:
        primary, fallback = MODEL, FALLBACK

    step = label or ("vision" if vision else "chat")

    try:
        res = get_client().chat.completions.create(
            model=primary, messages=cast(Any, messages), **params
        )
        # res.model is the model that ACTUALLY ran, so accounting stays correct
        # even if the account served a dated snapshot id.
        record_chat(getattr(res, "model", None) or primary, _usage_dict(res), step)
        return res
    except Exception as err:
        if primary != fallback and is_model_unavailable(err):
            log.warning('[openai] model "%s" unavailable — falling back to "%s"', primary, fallback)
            _fell_back_to = fallback  # surfaced on /api/health, never silent
            res = get_client().chat.completions.create(
                model=fallback, messages=cast(Any, messages), **params
            )
            record_chat(getattr(res, "model", None) or fallback, _usage_dict(res), step)
            return res
        raise


def _usage_dict(res: Any) -> dict | None:
    usage = getattr(res, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
    }
