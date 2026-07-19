"""Every LLM prompt in the pipeline, in one place.

COPIED CHARACTER-FOR-CHARACTER from the Node service. Rewording any of these
changes model output and breaks parity with the deployed system — which is the
whole point of the port. Treat these strings as data, not prose to improve.

Source files:
  src/lib/pipeline/tag.ts           -> ANALYZE_VIDEO_SYSTEM, TAG_*
  src/lib/pipeline/chunkSummary.ts  -> CHUNK_SUMMARY_SYSTEM
  src/lib/pipeline/reassignOther.ts -> REASSIGN_SYSTEM

Placeholders use str.format, so any literal brace in a prompt must be doubled.
The JSON examples below are full of braces — that is why they appear doubled.
"""

# ─── tag.ts :: analyzeVideo ───────────────────────────────────────────────────

ANALYZE_VIDEO_SYSTEM = """Analyze this video transcript and return:
1. "category": the video type (e.g. "Laptop Repair", "Cooking Tutorial", "Unboxing", "Teaching", "Workout", etc.)
2. "phases": 4-8 single-word phase labels describing the main stages of THIS specific video. Title Case.
Return JSON: { "category": "...", "phases": ["Phase1", "Phase2", ...] }"""


# ─── tag.ts :: tagSegments ────────────────────────────────────────────────────

# Used when analyzeVideo derived a phase vocabulary for this specific video.
TAG_PHASE_HINT_WITH_PHASES = (
    'Choose "m" for each segment from this list, picking the closest match: '
    '{phases}, other. Use "other" ONLY when none genuinely fit — avoid it '
    "whenever a real phase applies."
)

# Used when analyzeVideo returned nothing usable.
TAG_PHASE_HINT_DEFAULT = (
    'Choose "m" from: introduction, overview, diagnosis, repair, testing, '
    'verification, safety, parts, conclusion, other. Use "other" ONLY as a last resort.'
)

TAG_SEGMENTS_SYSTEM = """Tag each transcript segment with:
- "m": the phase label (see the list below)
- "s": 2-5 word specific description (sub tag)
{phase_hint}
Return ONLY JSON — no input text: {{"segments":[{{"i":0,"m":"introduction","s":"Overview of the parts"}},{{"i":1,"m":"diagnosis","s":"Testing battery voltage"}}]}}"""


# ─── chunkSummary.ts :: summarizeChunks ───────────────────────────────────────

CHUNK_SUMMARY_SYSTEM = (
    'For each transcript chunk write "title": a short 3-6 word label naming what '
    'the chunk is about, "summary": one plain sentence describing what happens, '
    'and "tools": an array of the physical tools/instruments named in that chunk '
    "(empty if none). Do not invent tools. Return ONLY this JSON object: "
    '{"chunks":[{"i":0,"title":"...","summary":"...","tools":["..."]}]}'
)


# ─── reassignOther.ts :: reassignOtherTags ────────────────────────────────────

REASSIGN_SYSTEM = (
    "Each item describes one video chapter. Assign each to the single best-fitting "
    'phase from this list: {phases}. Only use "other" if a chapter genuinely fits '
    "none of them. Return ONLY this JSON object: "
    '{{"assignments":[{{"i":0,"phase":"..."}}]}}'
)
