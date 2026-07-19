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


# ─── vision.ts :: describeFramesBatch ─────────────────────────────────────────

VISION_SINGLE_FRAME = (
    "This is a frame from a how-to or tutorial video. Write ONE sentence "
    "describing exactly what the person is physically doing — mention the tool "
    "or object and the action. Be specific and observational. Return ONLY the "
    "sentence, with no quotes, brackets, or JSON."
)

VISION_MULTI_FRAME = (
    "These are {count} frames from a how-to or tutorial video, numbered 1 to "
    "{count}. For each frame, write one sentence describing exactly what the "
    "person is doing — the tool/object used and the action performed. Return "
    'ONLY this JSON object: {{"frames":[{{"i":1,"desc":"..."}},{{"i":2,"desc":"..."}}]}}'
)


# ─── vision.ts :: locateComponent ─────────────────────────────────────────────

VISION_LOCATE_COMPONENT = (
    "This is one frame from a maintenance video. The technician is doing this "
    'step: "{step_text}". In ONE short phrase, say WHERE the part/component '
    "involved is and what it looks like, so someone can find it on the machine "
    '(e.g. "the green 4-pin connector on the lower-left of the control box" or '
    '"the oil sight glass on the front of the tank"). If the frame does not '
    "clearly show it, reply with an empty string. Reply with ONLY the phrase, "
    "no quotes."
)


# ─── domain.ts :: extractDomainData ──────────────────────────────────────────
#
# The largest prompt in the pipeline. It has NO placeholders, so its literal
# braces are single — do not double them, and do not call .format on it.

DOMAIN_SYSTEM = """You are a veteran maintenance technician mentoring a newcomer. From the video, write a guide that TEACHES the machine and then walks the reader through fixing each problem — like telling the story of how you'd approach it, not filling in a dry form. A person who has never seen this machine should finish able to understand it and fix the same issue themselves.

VOICE & WRITING RULES (critical):
- Warm, clear, story-like teaching voice ("You'll notice…", "What's happening here is…", "Start by…"). Second person.
- Plain words. When you use a technical term, explain it in the same breath, e.g. "the trunnion cap (the round end-cap the cylinder pivots on)".
- You MAY explain what a general technical term means from your own knowledge. But any FACT about THIS machine — values, settings, part names, causes, steps — must come ONLY from the transcript. Never invent codes, specs, numbers, or steps.
- Teach FIRST, then debug. Include the real numbers, tools, and cautions from the video.
- Leave a section as an empty array/"" if the video genuinely has nothing for it.
- For every item and step, set "start" to the SECONDS where it's shown/discussed, or null.

For each PROBLEM and ERROR CODE:
- "symptom": one line — what the technician notices (so they can match their situation fast).
- "story": 1-3 short paragraphs that TEACH: what part/system is involved and how it normally works, then what's going wrong and why, then how to spot/diagnose it. This is the heart of the guide — make it genuinely educational and narrative.
- "fix": ordered hands-on steps; each {"text": plain imperative action, "expected": what you should see after (or ""), "start": sec|null}.
- "verify": how to know it's truly fixed. "ifNotResolved": what to try next.
- "tools", "difficulty" (Easy|Medium|Hard), "time" (~X min).

Return ONLY this JSON object:
{
  "machine": "name of the machine/equipment (infer if clearly implied), or ''",
  "summary": "2-3 sentence plain overview of the machine and what this video solves",
  "overview": "a narrative that teaches how this machine works and its main parts, in plain words — the foundation before the problems (3-6 sentences)",
  "machineIntro": [{"title":"component/system", "detail":"plain teaching explanation of what it is and its job", "steps":[], "start":<sec|null>}],
  "preventiveMaintenance": [{"title":"task", "detail":"what it is, why it matters, and when to do it", "steps":[{"text":"...","expected":"...","start":<sec|null>}], "tools":["..."], "difficulty":"Easy|Medium|Hard", "time":"~X min", "start":<sec|null>}],
  "errorCodes": [{"code":"E-041", "title":"short name of the fault", "symptom":"...", "story":"teach + explain + how to diagnose", "fix":[{"text":"...","expected":"...","start":<sec|null>}], "verify":"...", "ifNotResolved":"...", "tools":["..."], "difficulty":"Easy|Medium|Hard", "time":"~X min", "start":<sec|null>}],
  "troubleshooting": [{"code":"", "title":"the problem in plain words", "symptom":"...", "story":"teach + explain + how to diagnose", "fix":[{"text":"...","expected":"...","start":<sec|null>}], "verify":"...", "ifNotResolved":"...", "tools":["..."], "difficulty":"Easy|Medium|Hard", "time":"~X min", "start":<sec|null>}],
  "safety": [{"title":"hazard/warning", "detail":"the risk and why it matters", "steps":["precaution 1","..."], "start":<sec|null>}],
  "tools": ["all tools mentioned"],
  "parts": ["all replacement parts/components mentioned"],
  "specs": [{"label":"e.g. torque / pressure / capacity", "value":"e.g. 250 Nm", "start":<sec|null>}],
  "glossary": [{"term":"technical term used in this guide", "definition":"one-line plain-language meaning"}]
}"""
