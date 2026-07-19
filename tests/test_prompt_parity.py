"""Every prompt must reach the model character-for-character as Node sends it.

This is the mitigation for the single biggest risk in the port. Prompts drive
model output; a reworded prompt produces different chapters, different titles
and a different guide, while every other test still passes and the API contract
still validates. The drift would only surface as "the Python one gives worse
results", weeks later, with no obvious cause.

`tests/fixtures/node_prompts.json` was EXTRACTED PROGRAMMATICALLY from
youtube-clone/src/lib/pipeline/{tag,chunkSummary,reassignOther}.ts — not
retyped. TS interpolations are normalized to the Python placeholder names:
`${phases.join(", ")}` -> `{phases}`, `${phaseHint}` -> `{phase_hint}`.

Comparison is on the RENDERED prompt, not the source string. The Python
versions double their literal braces so str.format leaves the JSON examples
intact; that is an encoding detail the model never sees. Rendering both sides
compares what actually goes over the wire.

If you intend to change a prompt, change it in BOTH services and regenerate the
fixture. Do not "fix" this test by editing the expectation alone.
"""

import json
from pathlib import Path

import pytest

from app.pipeline import prompts

NODE_PROMPTS = json.loads((Path(__file__).parent / "fixtures" / "node_prompts.json").read_text())

# Placeholder -> the value substituted into BOTH sides before comparing.
TEMPLATED = {
    "TAG_PHASE_HINT_WITH_PHASES": ("{phases}", "Intro, Repair, Testing"),
    "TAG_SEGMENTS_SYSTEM": ("{phase_hint}", "THE-PHASE-HINT"),
    "REASSIGN_SYSTEM": ("{phases}", "Intro, Repair, Testing"),
    "VISION_MULTI_FRAME": ("{count}", "5"),
    "VISION_LOCATE_COMPONENT": ("{step_text}", "Loosen the cap nut"),
}


def render_python(name: str) -> str:
    """What app/pipeline/prompts.py actually sends."""
    template = getattr(prompts, name)
    if name not in TEMPLATED:
        return template
    placeholder, value = TEMPLATED[name]
    return template.format(**{placeholder.strip("{}"): value})


def render_node(name: str) -> str:
    """What the Node service actually sends, with the same substitution.

    str.replace, not str.format — the Node string's literal JSON braces are
    single and would break a format call.
    """
    template = NODE_PROMPTS[name]
    if name not in TEMPLATED:
        return template
    placeholder, value = TEMPLATED[name]
    return template.replace(placeholder, value)


@pytest.mark.parametrize("name", sorted(NODE_PROMPTS))
def test_rendered_prompt_matches_the_node_service(name):
    assert hasattr(prompts, name), f"{name} is missing from app/pipeline/prompts.py"
    assert render_python(name) == render_node(name)


def test_every_node_prompt_is_covered():
    """A prompt added to Node must be added here too."""
    assert set(NODE_PROMPTS) == {
        "ANALYZE_VIDEO_SYSTEM",
        "TAG_PHASE_HINT_WITH_PHASES",
        "TAG_PHASE_HINT_DEFAULT",
        "TAG_SEGMENTS_SYSTEM",
        "CHUNK_SUMMARY_SYSTEM",
        "REASSIGN_SYSTEM",
        "DOMAIN_SYSTEM",
        "VISION_SINGLE_FRAME",
        "VISION_MULTI_FRAME",
        "VISION_LOCATE_COMPONENT",
    }


def test_the_domain_prompt_keeps_its_grounding_rules():
    """These three sentences are what stop the guide inventing machine facts.

    Without them the model confidently produces plausible torque values and
    part numbers that appear nowhere in the video — the worst possible failure
    for a maintenance guide someone will act on.
    """
    p = prompts.DOMAIN_SYSTEM
    assert "must come ONLY from the transcript" in p
    assert "Never invent codes, specs, numbers, or steps." in p
    assert "Leave a section as an empty array" in p


def test_the_domain_prompt_requests_every_contract_section():
    """The 12 guide fields the client reads must all be asked for."""
    p = prompts.DOMAIN_SYSTEM
    for field in (
        "machine",
        "summary",
        "overview",
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
        assert f'"{field}"' in p, f"the domain prompt never asks for {field}"


def test_json_examples_survive_formatting():
    """The JSON examples inside these prompts are full of literal braces.

    If one is not doubled, str.format raises or silently eats it — and the model
    receives a malformed example to imitate.
    """
    assert '{"segments":[{"i":0,"m":"introduction"' in render_python("TAG_SEGMENTS_SYSTEM")
    assert '{"assignments":[{"i":0,"phase":"..."}]}' in render_python("REASSIGN_SYSTEM")
    assert '{"chunks":[{"i":0,"title":"..."' in prompts.CHUNK_SUMMARY_SYSTEM


def test_substituted_values_reach_the_rendered_prompt():
    assert "Intro, Repair, Testing" in render_python("REASSIGN_SYSTEM")
    assert "THE-PHASE-HINT" in render_python("TAG_SEGMENTS_SYSTEM")


def test_chunk_summary_prompt_asks_for_the_title_field():
    """chunkTitle is the newest contract field — the prompt must request it."""
    assert '"title"' in prompts.CHUNK_SUMMARY_SYSTEM
    assert "3-6 word label" in prompts.CHUNK_SUMMARY_SYSTEM


def test_prompts_do_not_invent_tools():
    """Removing this instruction makes the model hallucinate tool names."""
    assert "Do not invent tools." in prompts.CHUNK_SUMMARY_SYSTEM
