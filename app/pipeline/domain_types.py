"""Types + defensive normalizers for the machine-maintenance guide.

Ported from youtube-clone/src/lib/pipeline/domain-types.ts.

The guide is built for SELF-SERVICE DEBUGGING: each problem is a full flow —
symptom -> likely cause -> how to check -> fix steps -> verify -> if it still
fails.

These produce plain camelCase dicts, not Pydantic models, because the result is
persisted verbatim into Video.domain_data and read straight back by the response
builder. app/schemas/extraction.py describes the same shape for the API layer;
this module is what the pipeline writes.

The legacy field fallbacks (question/meaning/cause/answer, bare-string steps)
are why videos processed by older versions still render.
"""

from typing import Any

# ─── normalizer primitives ────────────────────────────────────────────────────


def _as_str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _as_str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [s for s in (_as_str(x) for x in v) if s]


def _as_num(v: Any) -> float | None:
    """A timestamp, or None. Negatives are rejected — they would seek the
    player to an invalid position."""
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")) or n < 0:
        return None
    return n


def _as_list(v: Any) -> list[dict]:
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, dict)]


def as_steps(v: Any) -> list[dict[str, Any]]:
    """Steps may arrive as objects or, in old records, as bare strings."""
    out: list[dict[str, Any]] = []
    if not isinstance(v, list):
        return out
    for s in v:
        step: dict[str, Any]
        if isinstance(s, str):
            step = {"text": _as_str(s), "expected": "", "visual": "", "start": None}
        elif isinstance(s, dict):
            step = {
                "text": _as_str(s.get("text")),
                "expected": _as_str(s.get("expected")),
                "visual": _as_str(s.get("visual")),
                "start": _as_num(s.get("start")),
            }
        else:
            continue
        if step["text"]:
            out.append(step)
    return out


def _as_guide_item(v: Any) -> dict[str, Any]:
    o = v if isinstance(v, dict) else {}
    return {
        "title": _as_str(o.get("title")),
        "detail": _as_str(o.get("detail")),
        "steps": _as_str_list(o.get("steps")),
        "start": _as_num(o.get("start")),
    }


def _as_debug_item(v: Any) -> dict[str, Any]:
    o = v if isinstance(v, dict) else {}
    return {
        "code": _as_str(o.get("code")),
        # Legacy fallbacks so old records still show something.
        "title": _as_str(o.get("title")) or _as_str(o.get("question")) or _as_str(o.get("meaning")),
        "symptom": _as_str(o.get("symptom")) or _as_str(o.get("question")),
        "story": _as_str(o.get("story")) or _as_str(o.get("cause")) or _as_str(o.get("answer")),
        "fix": as_steps(o.get("fix") if o.get("fix") is not None else o.get("steps")),
        "verify": _as_str(o.get("verify")),
        "ifNotResolved": _as_str(o.get("ifNotResolved")),
        "tools": _as_str_list(o.get("tools")),
        "difficulty": _as_str(o.get("difficulty")),
        "time": _as_str(o.get("time")),
        "start": _as_num(o.get("start")),
    }


def _as_procedure(v: Any) -> dict[str, Any]:
    o = v if isinstance(v, dict) else {}
    return {
        "title": _as_str(o.get("title")),
        "detail": _as_str(o.get("detail")),
        "steps": as_steps(o.get("steps")),
        "tools": _as_str_list(o.get("tools")),
        "difficulty": _as_str(o.get("difficulty")),
        "time": _as_str(o.get("time")),
        "start": _as_num(o.get("start")),
    }


# ─── the guide ────────────────────────────────────────────────────────────────


def empty_domain() -> dict[str, Any]:
    """A FRESH empty guide.

    Returned by value, never shared — a module-level mutable default would let
    one video's guide leak into another's.
    """
    return {
        "machine": "",
        "summary": "",
        "overview": "",
        "machineIntro": [],
        "preventiveMaintenance": [],
        "errorCodes": [],
        "troubleshooting": [],
        "safety": [],
        "tools": [],
        "parts": [],
        "specs": [],
        "glossary": [],
    }


# Read-only reference for comparisons. Use empty_domain() when you need one to
# hand out or mutate.
EMPTY_DOMAIN = empty_domain()


def has_domain_content(d: dict[str, Any] | None) -> bool:
    """True when there is at least one populated section worth showing.

    A machine NAME alone does not count — a guide tab with only a title in it
    is worse than no tab.
    """
    if not d:
        return False
    return bool(
        d.get("summary")
        or d.get("overview")
        or d.get("machineIntro")
        or d.get("preventiveMaintenance")
        or d.get("errorCodes")
        or d.get("troubleshooting")
        or d.get("safety")
        or d.get("tools")
        or d.get("parts")
        or d.get("specs")
        or d.get("glossary")
    )


def as_domain_data(raw: Any) -> dict[str, Any] | None:
    """Normalize stored JSON or model output into a valid guide, or None."""
    if not isinstance(raw, dict):
        return None

    d: dict[str, Any] = {
        "machine": _as_str(raw.get("machine")),
        "summary": _as_str(raw.get("summary")),
        "overview": _as_str(raw.get("overview")),
        "machineIntro": [
            x
            for x in (_as_guide_item(i) for i in _as_list(raw.get("machineIntro")))
            if x["title"] or x["detail"]
        ],
        "preventiveMaintenance": [
            x
            for x in (_as_procedure(i) for i in _as_list(raw.get("preventiveMaintenance")))
            if x["title"]
        ],
        "errorCodes": [
            x
            for x in (_as_debug_item(i) for i in _as_list(raw.get("errorCodes")))
            if x["code"] or x["title"]
        ],
        "troubleshooting": [
            x
            for x in (_as_debug_item(i) for i in _as_list(raw.get("troubleshooting")))
            if x["title"] or x["symptom"]
        ],
        "safety": [
            x
            for x in (_as_guide_item(i) for i in _as_list(raw.get("safety")))
            if x["title"] or x["detail"]
        ],
        "tools": _as_str_list(raw.get("tools")),
        "parts": _as_str_list(raw.get("parts")),
        "specs": [
            s
            for s in (
                {
                    "label": _as_str(i.get("label")),
                    "value": _as_str(i.get("value")),
                    "start": _as_num(i.get("start")),
                }
                for i in _as_list(raw.get("specs"))
            )
            if s["label"] or s["value"]
        ],
        "glossary": [
            g
            for g in (
                {"term": _as_str(i.get("term")), "definition": _as_str(i.get("definition"))}
                for i in _as_list(raw.get("glossary"))
            )
            if g["term"] and g["definition"]
        ],
    }

    return d if has_domain_content(d) else None
