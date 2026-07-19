"""Defensive normalization of the stored guide JSON.

Ported from youtube-clone/src/lib/pipeline/domain-types.ts.

as_domain_data has two jobs, and the second is the subtle one:
  1. Turn whatever the model returned into a valid guide.
  2. Keep OLD records readable — early versions stored `question`/`meaning`/
     `cause`/`answer` instead of `title`/`symptom`/`story`, and steps as bare
     strings instead of objects. Those fallbacks are why a video processed
     months ago still renders today.
"""

from app.pipeline.domain_types import (
    EMPTY_DOMAIN,
    as_domain_data,
    as_steps,
    has_domain_content,
)

# ─── as_steps ─────────────────────────────────────────────────────────────────


def test_reads_step_objects():
    out = as_steps([{"text": "Loosen the nut", "expected": "It turns freely", "start": 12.5}])
    assert out == [
        {"text": "Loosen the nut", "expected": "It turns freely", "visual": "", "start": 12.5}
    ]


def test_accepts_bare_strings_from_old_records():
    assert as_steps(["Loosen the nut"]) == [
        {"text": "Loosen the nut", "expected": "", "visual": "", "start": None}
    ]


def test_drops_steps_with_no_text():
    assert as_steps([{"expected": "something"}, {"text": "   "}]) == []


def test_a_non_list_yields_no_steps():
    assert as_steps("not a list") == []
    assert as_steps(None) == []


def test_negative_timestamps_are_rejected():
    """A negative start would seek the player to an invalid position."""
    assert as_steps([{"text": "x", "start": -5}])[0]["start"] is None


def test_non_numeric_timestamps_are_rejected():
    assert as_steps([{"text": "x", "start": "abc"}])[0]["start"] is None


def test_numeric_strings_are_accepted_as_timestamps():
    assert as_steps([{"text": "x", "start": "12.5"}])[0]["start"] == 12.5


def test_visual_survives_the_vision_enrichment():
    out = as_steps([{"text": "x", "visual": "the green connector, lower left"}])
    assert out[0]["visual"] == "the green connector, lower left"


# ─── as_domain_data ───────────────────────────────────────────────────────────


def test_reads_a_full_guide():
    d = as_domain_data(
        {
            "machine": "Lubrication System",
            "summary": "Diagnosing low lube flow.",
            "overview": "The lube system keeps parts oiled.",
            "machineIntro": [{"title": "Float switch", "detail": "Detects oil level."}],
            "troubleshooting": [
                {
                    "title": "Low flow",
                    "symptom": "Pressure gauge reads low",
                    "story": "The pump draws from the reservoir...",
                    "fix": [{"text": "Check the filter", "expected": "It is clean"}],
                    "verify": "Pressure returns to 40 psi",
                    "ifNotResolved": "Replace the pump",
                    "tools": ["Multimeter"],
                    "difficulty": "Medium",
                    "time": "~30 min",
                }
            ],
            "tools": ["Multimeter", "14mm spanner"],
            "glossary": [{"term": "Trunnion", "definition": "The pivot end-cap."}],
        }
    )
    assert d is not None
    assert d["machine"] == "Lubrication System"
    assert len(d["troubleshooting"]) == 1
    assert d["troubleshooting"][0]["ifNotResolved"] == "Replace the pump"
    assert d["tools"] == ["Multimeter", "14mm spanner"]


def test_missing_sections_become_empty_lists_not_none():
    """The response builder indexes these directly; None would crash it."""
    d = as_domain_data({"machine": "X", "summary": "Y"})
    assert d is not None
    for key in (
        "machineIntro",
        "preventiveMaintenance",
        "errorCodes",
        "troubleshooting",
        "safety",
        "tools",
        "parts",
        "specs",
        "glossary",
    ):
        assert d[key] == []


def test_legacy_field_names_still_read():
    """Old records used question/meaning/cause/answer."""
    d = as_domain_data(
        {
            "machine": "X",
            "troubleshooting": [
                {"question": "Why is it leaking?", "cause": "The seal is worn."},
            ],
            "errorCodes": [{"code": "E-041", "meaning": "Overtemp", "answer": "Let it cool."}],
        }
    )
    assert d is not None
    assert d["troubleshooting"][0]["title"] == "Why is it leaking?"
    assert d["troubleshooting"][0]["symptom"] == "Why is it leaking?"
    assert d["troubleshooting"][0]["story"] == "The seal is worn."
    assert d["errorCodes"][0]["title"] == "Overtemp"
    assert d["errorCodes"][0]["story"] == "Let it cool."


def test_legacy_steps_key_is_read_as_fix():
    d = as_domain_data({"machine": "X", "troubleshooting": [{"title": "T", "steps": ["Do it"]}]})
    assert d is not None
    assert d["troubleshooting"][0]["fix"][0]["text"] == "Do it"


def test_empty_items_are_dropped():
    d = as_domain_data(
        {
            "machine": "X",
            "troubleshooting": [{"title": "Real"}, {}, {"title": "   "}],
            "glossary": [{"term": "A", "definition": "B"}, {"term": "", "definition": "C"}],
        }
    )
    assert d is not None
    assert len(d["troubleshooting"]) == 1
    assert len(d["glossary"]) == 1


def test_a_glossary_entry_needs_both_term_and_definition():
    # `summary` is present only so the guide has content and survives the
    # has_domain_content gate — the assertion under test is about glossary.
    d = as_domain_data(
        {
            "machine": "X",
            "summary": "S",
            "glossary": [{"term": "A", "definition": ""}, {"term": "", "definition": "B"}],
        }
    )
    assert d is not None
    assert d["glossary"] == []


def test_non_string_tools_are_discarded():
    d = as_domain_data({"machine": "X", "tools": ["Real", 5, None, ""]})
    assert d is not None
    assert d["tools"] == ["Real"]


def test_a_guide_with_no_content_is_none():
    """Nothing worth showing means no guide tab at all."""
    assert as_domain_data({}) is None
    assert as_domain_data({"machine": "X"}) is None  # a name alone is not content


def test_garbage_input_is_none():
    assert as_domain_data(None) is None
    assert as_domain_data("a string") is None
    assert as_domain_data([1, 2, 3]) is None


def test_values_are_trimmed():
    d = as_domain_data({"machine": "  X  ", "summary": "  S  "})
    assert d is not None
    assert d["machine"] == "X"
    assert d["summary"] == "S"


# ─── has_domain_content ───────────────────────────────────────────────────────


def test_empty_domain_has_no_content():
    assert has_domain_content(EMPTY_DOMAIN) is False
    assert has_domain_content(None) is False


def test_a_machine_name_alone_is_not_content():
    assert has_domain_content({**EMPTY_DOMAIN, "machine": "Pump"}) is False


def test_any_populated_section_counts_as_content():
    assert has_domain_content({**EMPTY_DOMAIN, "summary": "S"}) is True
    assert has_domain_content({**EMPTY_DOMAIN, "tools": ["Spanner"]}) is True
    assert has_domain_content({**EMPTY_DOMAIN, "troubleshooting": [{"title": "T"}]}) is True


def test_empty_domain_is_not_shared_between_callers():
    """A shared mutable default would let one video's guide leak into another."""
    a = as_domain_data({"machine": "X", "summary": "S"})
    assert a is not None
    a["tools"].append("mutated")
    assert EMPTY_DOMAIN["tools"] == []
