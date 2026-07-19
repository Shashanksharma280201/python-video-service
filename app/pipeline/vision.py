"""Vision describes what is happening on screen during silent stretches.

Ported from youtube-clone/src/lib/pipeline/vision.ts. Without this, a wordless
video gets no meaningful chapters at all.

Runs on gpt-5.4-mini, not the flagship — see openai_client.VISION_MODEL.
"""

import base64
import json
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.pipeline.media import extract_frame_at
from app.pipeline.openai_client import chat_complete
from app.pipeline.prompts import (
    VISION_LOCATE_COMPONENT,
    VISION_MULTI_FRAME,
    VISION_SINGLE_FRAME,
)
from app.pipeline.types import (
    VISION_BATCH_SIZE,
    VISION_CONCURRENCY,
    Gap,
    TaggedSegment,
)

log = logging.getLogger(__name__)

# What a chapter says when vision could not describe the frame. Anything is
# better than leaking raw JSON into a subtitle.
FALLBACK_DESCRIPTION = "performing task"

MAX_LOCATED_STEPS = 30  # cap vision calls per video

_FENCE_OPEN = re.compile(r"^```(?:json)?", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"```$")
_ARRAY_BLOCK = re.compile(r"\[[\s\S]*\]")


def parse_descriptions(text: str, count: int) -> list[str]:
    """Extract `count` plain sentences from a possibly-messy model response.

    Handles markdown fences, a bare array, a {"frames": [...]} wrapper, and an
    array embedded in prose.

    Critically it NEVER returns the raw response as a description — a parse
    failure falls back to FALLBACK_DESCRIPTION rather than dumping JSON into a
    chapter subtitle.
    """
    clean = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", text.strip()).strip()).strip()

    arr: list[Any] | None = None
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, list):
            arr = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("frames"), list):
            arr = parsed["frames"]
    except (ValueError, TypeError):
        m = _ARRAY_BLOCK.search(clean)  # last resort: first [...] block
        if m:
            try:
                candidate = json.loads(m.group(0))
                arr = candidate if isinstance(candidate, list) else None
            except (ValueError, TypeError):
                arr = None

    out: list[str] = []
    for j in range(count):
        entry: Any = None
        if arr is not None:
            # The prompt numbers frames from 1, not 0.
            entry = next((e for e in arr if isinstance(e, dict) and e.get("i") == j + 1), None)
            if entry is None and j < len(arr):
                entry = arr[j]

        desc = ""
        if isinstance(entry, dict) and isinstance(entry.get("desc"), str):
            desc = entry["desc"].strip()

        out.append(desc or FALLBACK_DESCRIPTION)
    return out


def _image_content(path: str) -> dict[str, Any]:
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
    }


def describe_frames_batch(frame_paths: list[str]) -> list[str]:
    """Describe frames, VISION_BATCH_SIZE at a time."""
    descriptions: list[str] = []

    for i in range(0, len(frame_paths), VISION_BATCH_SIZE):
        batch = frame_paths[i : i + VISION_BATCH_SIZE]
        images = [_image_content(p) for p in batch]

        try:
            if len(batch) == 1:
                res = chat_complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": VISION_SINGLE_FRAME}, *images],
                        }
                    ],
                    max_completion_tokens=120,
                    vision=True,
                    label="vision-single",
                )
                text = (res.choices[0].message.content or "").strip()
                # A bare sentence is what we asked for; if the model returned
                # JSON anyway, parse it rather than showing braces to a user.
                if text and not text.startswith(("[", "{")):
                    descriptions.append(text)
                else:
                    descriptions.append(parse_descriptions(text, 1)[0])
            else:
                res = chat_complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": VISION_MULTI_FRAME.format(count=len(batch)),
                                },
                                *images,
                            ],
                        }
                    ],
                    max_completion_tokens=500,
                    # json_object mode guarantees parseable JSON — no fences.
                    response_format={"type": "json_object"},
                    vision=True,
                    label="vision-batch",
                )
                text = (res.choices[0].message.content or "").strip()
                descriptions.extend(parse_descriptions(text, len(batch)))
        except Exception as err:
            log.error("[vision] batch failed: %s", err)
            descriptions.extend([FALLBACK_DESCRIPTION] * len(batch))

    return descriptions


def build_silent_segments(chunks: list[Gap], source: str, video_id: str) -> list[TaggedSegment]:
    """Extract a frame at the midpoint of each silent chunk and describe it."""
    if not chunks:
        return []

    vision_dir = os.path.join(tempfile.gettempdir(), f"vision-{video_id}")
    os.makedirs(vision_dir, exist_ok=True)

    def grab(item: tuple[int, Gap]) -> str | None:
        i, chunk = item
        midpoint = (chunk["start"] + chunk["end"]) / 2
        frame_path = os.path.join(vision_dir, f"frame-{i}.jpg")
        try:
            extract_frame_at(source, midpoint, frame_path)
            return frame_path if os.path.exists(frame_path) else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=VISION_CONCURRENCY) as pool:
        frame_paths = list(pool.map(grab, enumerate(chunks)))

    valid = [p for p in frame_paths if p is not None]
    batches = [valid[i : i + VISION_BATCH_SIZE] for i in range(0, len(valid), VISION_BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=VISION_CONCURRENCY) as pool:
        batch_descriptions = list(pool.map(describe_frames_batch, batches))

    all_descriptions = [d for batch in batch_descriptions for d in batch]

    segments: list[TaggedSegment] = []
    desc_idx = 0
    for i, chunk in enumerate(chunks):
        if frame_paths[i] is not None:
            sub_tag = (
                all_descriptions[desc_idx]
                if desc_idx < len(all_descriptions)
                else FALLBACK_DESCRIPTION
            )
            desc_idx += 1
        else:
            sub_tag = FALLBACK_DESCRIPTION

        segments.append(
            {
                "id": -(i + 1),  # negative ids distinguish silent segments
                "start": chunk["start"],
                "end": chunk["end"],
                "text": "",
                "mainTag": "action",
                "subTag": sub_tag,
            }
        )
    return segments


# ─── step-location vision ("where is it on screen") ───────────────────────────


def locate_component(frame_path: str, step_text: str) -> str:
    """Say WHERE the part involved in one fix step is on screen."""
    try:
        res = chat_complete(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": VISION_LOCATE_COMPONENT.format(step_text=step_text),
                        },
                        _image_content(frame_path),
                    ],
                }
            ],
            max_completion_tokens=70,
            vision=True,
            label="vision-locate",
        )
        t = (res.choices[0].message.content or "").strip()
        # Guard against the model echoing the instruction or returning junk.
        if 4 < len(t) < 200:
            return t.strip("\"'")
        return ""
    except Exception as err:
        log.error("[locate] failed: %s", err)
        return ""


def enrich_steps_with_vision(source: str, steps: list[dict], video_id: str) -> None:
    """Fill in each timestamped step's `visual` field. Mutates in place."""
    targets = [s for s in steps if s.get("start") is not None and s.get("text")][:MAX_LOCATED_STEPS]
    if not targets:
        return

    directory = os.path.join(tempfile.gettempdir(), f"loc-{video_id}")
    os.makedirs(directory, exist_ok=True)

    def enrich(item: tuple[int, dict]) -> None:
        i, step = item
        fp = os.path.join(directory, f"loc-{i}.jpg")
        try:
            extract_frame_at(source, step["start"], fp)
            if os.path.exists(fp):
                step["visual"] = locate_component(fp, step["text"])
        except Exception:
            pass  # leave visual empty

    with ThreadPoolExecutor(max_workers=VISION_CONCURRENCY) as pool:
        list(pool.map(enrich, enumerate(targets)))
