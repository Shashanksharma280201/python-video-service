"""The durable transcription pipeline.

Ported from youtube-clone/src/workflows/transcribe-video.ts.

The big idea, preserved from Node: NEVER download the full video. ffmpeg seeks
each slice it needs (audio chunks, frames) straight from a presigned URL via
HTTP range requests, so on-disk footprint per step is only the small OUTPUT — a
~2MB audio slice or a JPEG — never the multi-GB source.

Each step presigns its OWN short-lived URL at execution time, so nothing expires
mid-run even when rate limits pace a long video across hours.

Every stage goes through StepContext.run, so a worker killed partway resumes
from the last completed step instead of replaying the whole video. Fan-out
slices key on their offset and resume independently.

All domain logic lives in the orchestration-agnostic app/pipeline/* modules;
this file is the only thing that knows about the database and the ledger.
"""

import logging
import os
import tempfile
from functools import partial
from typing import Any, cast

from sqlalchemy.orm import Session

from app.models import TranscriptStatus, Video
from app.pipeline.align_segment_tags import align_segment_tags
from app.pipeline.chunk_summary import summarize_chunks
from app.pipeline.consolidate import consolidate_chapters
from app.pipeline.domain import extract_domain_data
from app.pipeline.domain_types import empty_domain
from app.pipeline.gaps import chunk_long_gaps, find_unspoken_gaps
from app.pipeline.media import detect_silent_windows, extract_audio_slice, probe_duration
from app.pipeline.reassign_other import reassign_other_tags
from app.pipeline.tag import analyze_video, tag_segments
from app.pipeline.thumbnails import generate_video_segments
from app.pipeline.transcribe import is_hallucination, transcribe_audio_file
from app.pipeline.types import (
    FULL_SILENT_CHUNK_SECS,
    MAX_CHAPTERS,
    MAX_SILENT_CHUNKS,
    SILENCE_CHUNK_SECS,
    segment_secs,
)
from app.pipeline.usage import log_usage_total, reset_usage
from app.pipeline.vision import build_silent_segments, enrich_steps_with_vision
from app.storage import parse_storage_url, presigned_download_url
from app.worker.steps import StepContext, chunk_key

log = logging.getLogger(__name__)

# Modest — kind to the API quota, and a long video paces across hours anyway.
TRANSCRIBE_CONCURRENCY = 3


class FatalError(Exception):
    """Do not retry — the input is wrong, not the environment."""


# ─── steps ────────────────────────────────────────────────────────────────────


def _prepare(session: Session, video_id: str) -> dict[str, Any]:
    """Probe the duration and slice the timeline into transcribe segments.

    Reads the container header only — no decode, no download.
    """
    video = session.get(Video, video_id)
    if video is None:
        raise FatalError(f"Video {video_id} not found")

    # The video may live in any container in our account (e.g. a tenant's), so
    # parse both container and key from its stored URL.
    container, key = parse_storage_url(video.blob_url)
    # Presign against the video's OWN container — an ingested video may live in
    # a tenant's container, and the default one would simply 404.
    url = presigned_download_url(key, container=container)
    duration = probe_duration(url)
    total = max(duration, 1)

    step = segment_secs()
    segments = []
    t = 0.0
    while t < total:
        segments.append({"offset": t, "dur": min(step, total - t) or step})
        t += step

    return {"key": key, "container": container, "duration": duration, "segments": segments}


def _transcribe_chunk(video_id: str, key: str, container: str, offset: float, dur: float) -> dict:
    """Transcribe ONE slice.

    ffmpeg rips just this ~10-minute audio slice straight from the presigned
    URL (disk ~2MB), Whisper transcribes it, and silencedetect runs on it.
    Timestamps are offset back onto the real timeline.
    """
    url = presigned_download_url(key, container=container)
    local = os.path.join(tempfile.gettempdir(), f"tchunk-{video_id}-{round(offset)}.mp3")
    try:
        extract_audio_slice(url, offset, dur, local)

        segs = transcribe_audio_file(local, dur)
        spoken = [
            {
                "id": s["id"],
                "start": s["start"] + offset,
                "end": s["end"] + offset,
                "text": s["text"],
                "no_speech_prob": s.get("no_speech_prob", 0),
                "avg_logprob": s.get("avg_logprob", 0),
            }
            for s in segs
        ]

        windows = detect_silent_windows(local)
        silent_windows = [
            {
                "start": w["start"] + offset,
                "end": (w["end"] if w["end"] is not None else dur) + offset,
            }
            for w in windows
        ]
        return {"spoken": spoken, "silentWindows": silent_windows}
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass


def _tag(spoken_raw: list[dict], duration: float) -> list[dict]:
    """Clean, de-hallucinate and phase-tag the merged spoken segments."""
    clamped = [
        {**s, "start": min(s["start"], duration), "end": min(s["end"], duration)}
        for s in spoken_raw
    ]
    real = [s for s in clamped if not is_hallucination(cast(Any, s), duration)]
    real.sort(key=lambda s: s["start"])
    real = [{**s, "id": i} for i, s in enumerate(real)]

    sample = " ".join(s["text"] for s in real)[:60000]
    phases = analyze_video(sample).get("phases", [])
    try:
        return cast(Any, tag_segments(cast(Any, real), phases))
    except Exception as err:
        log.warning("[pipeline] tagging failed, defaulting to 'other': %s", err)
        return [{**s, "mainTag": "other", "subTag": ""} for s in real]


def _frames(
    video_id: str,
    key: str,
    container: str,
    windows: list[dict],
    spoken_segments: list[dict],
    duration: float,
) -> dict[str, Any]:
    """Describe silent stretches, consolidate chapters, extract thumbnails."""
    url = presigned_download_url(key, container=container)

    chunks: list[Any]
    if not spoken_segments and duration > 0:
        # Wholly silent video: describe it on a fixed grid.
        chunks = []
        t = 0.0
        while t < duration:
            chunks.append({"start": t, "end": min(t + FULL_SILENT_CHUNK_SECS, duration)})
            t += FULL_SILENT_CHUNK_SECS
        chunks = chunks[:MAX_SILENT_CHUNKS]
    else:
        gaps = find_unspoken_gaps(cast(Any, windows), cast(Any, spoken_segments), duration)
        chunks = cast(Any, chunk_long_gaps(gaps, SILENCE_CHUNK_SECS))

    silent_segments: list[dict] = []
    try:
        silent_segments = cast(Any, build_silent_segments(cast(Any, chunks), url, video_id))
    except Exception as err:
        # Non-fatal: a video without silent-stretch descriptions still has
        # chapters and a transcript.
        log.error("[pipeline] silent vision failed: %s", err)

    # Full-resolution transcript (spoken + silent descriptions).
    transcript_segments = sorted(
        [*spoken_segments, *({**s, "text": s["subTag"]} for s in silent_segments)],
        key=lambda s: s["start"],
    )

    chapters = consolidate_chapters(
        cast(Any, sorted([*spoken_segments, *silent_segments], key=lambda s: s["start"])),
        MAX_CHAPTERS,
    )

    try:
        topic_segments = generate_video_segments(chapters, url, video_id)
    except Exception as err:
        log.error("[pipeline] thumbnails failed: %s", err)
        topic_segments = [
            {
                "mainTag": c["mainTag"],
                "subTag": c["subTag"],
                "start": c["start"],
                "end": c["end"],
                "thumbnailPath": None,
            }
            for c in chapters
        ]

    transcript = " ".join(s["text"] for s in spoken_segments)
    return {
        "transcript": transcript,
        "transcriptSegments": transcript_segments,
        "topicSegments": topic_segments,
    }


def _summarize(topic_segments: list[dict], transcript_segments: list[dict]) -> list[dict]:
    """Give each chapter an LLM title, a one-line summary and its tools."""
    if not topic_segments:
        return topic_segments

    inputs = [
        {
            "mainTag": seg["mainTag"],
            "subTag": seg["subTag"],
            "transcript": " ".join(
                t["text"].strip()
                for t in transcript_segments
                if seg["start"] <= t["start"] < seg["end"]
            ),
        }
        for seg in topic_segments
    ]
    summaries = summarize_chunks(cast(Any, inputs))
    return [
        {
            **seg,
            "title": summaries[i]["title"] if i < len(summaries) else "",
            "summarizedText": summaries[i]["summarizedText"] if i < len(summaries) else "",
            "tools": summaries[i]["tools"] if i < len(summaries) else [],
        }
        for i, seg in enumerate(topic_segments)
    ]


def _domain(transcript_segments: list[dict], topic_segments: list[dict], duration: float) -> dict:
    try:
        return extract_domain_data(
            cast(Any, transcript_segments), cast(Any, topic_segments), duration
        )
    except Exception as err:
        log.error("[pipeline] domain extraction failed: %s", err)
        return empty_domain()


def _enrich_guide(video_id: str, key: str, container: str, domain: dict) -> dict:
    """Add a "where is it on screen" note to each fix step."""
    try:
        url = presigned_download_url(key, container=container)
        steps: list[dict] = []
        for d in domain.get("troubleshooting", []):
            steps.extend(d.get("fix", []))
        for d in domain.get("errorCodes", []):
            steps.extend(d.get("fix", []))
        for p in domain.get("preventiveMaintenance", []):
            steps.extend(p.get("steps", []))
        enrich_steps_with_vision(url, steps, video_id)  # mutates step["visual"]
    except Exception as err:
        log.error("[pipeline] guide vision enrich failed: %s", err)
    return domain


def _save(
    session: Session,
    video_id: str,
    transcript: str,
    transcript_segments: list[dict],
    topic_segments: list[dict],
    domain_data: dict,
) -> None:
    video = session.get(Video, video_id)
    if video is None:
        raise FatalError(f"Video {video_id} disappeared mid-run")

    thumbnail = next((s["thumbnailPath"] for s in topic_segments if s.get("thumbnailPath")), None)

    video.transcript_status = TranscriptStatus.DONE
    video.transcript = transcript
    video.transcript_segments = transcript_segments
    if topic_segments:
        video.topic_segments = topic_segments
    video.thumbnail_url = thumbnail
    video.domain_data = domain_data
    session.commit()


def _fail(session: Session, video_id: str, message: str) -> None:
    video = session.get(Video, video_id)
    if video is None:
        return
    video.transcript_status = TranscriptStatus.FAILED
    video.transcript = message
    session.commit()


# ─── orchestration ────────────────────────────────────────────────────────────


def run_pipeline(session: Session, video_id: str) -> str:
    """Run the whole pipeline for one video. Returns the final status."""
    ctx = StepContext(session, video_id)
    reset_usage()

    try:
        prep = ctx.run("prepare", lambda: _prepare(session, video_id))
        key, container = prep["key"], prep["container"]
        duration, segments = prep["duration"], prep["segments"]

        # Each slice is its own resumable step, keyed by offset — so a worker
        # killed at minute 90 of a four-hour video resumes at minute 90.
        def transcribe_slice(seg: dict[str, Any]) -> Any:
            return _transcribe_chunk(video_id, key, container, seg["offset"], seg["dur"])

        per_chunk = [
            ctx.run(
                chunk_key("transcribe", s["offset"]),
                # Bind this iteration's slice explicitly; a bare closure over
                # the loop variable would transcribe the LAST slice every time.
                partial(transcribe_slice, s),
            )
            for s in segments
        ]
        all_spoken = [x for p in per_chunk for x in p["spoken"]]
        all_windows = [w for p in per_chunk for w in p["silentWindows"]]

        spoken_segments = ctx.run("tag", lambda: _tag(all_spoken, duration))

        frames = ctx.run(
            "frames",
            lambda: _frames(video_id, key, container, all_windows, spoken_segments, duration),
        )
        transcript = frames["transcript"]
        transcript_segments = frames["transcriptSegments"]
        topic_segments = frames["topicSegments"]

        enriched_chunks = ctx.run(
            "summarize", lambda: _summarize(topic_segments, transcript_segments)
        )
        re_tagged = ctx.run("reassign", lambda: reassign_other_tags(enriched_chunks))

        # Chapters are the source of truth for the phase: give each transcript
        # segment the phase of the chapter it sits in, so the transcript view
        # and the chapter list agree. Pure — no step needed.
        aligned = cast(list[dict[str, Any]], align_segment_tags(transcript_segments, re_tagged))

        domain_data = ctx.run("domain", lambda: _domain(aligned, re_tagged, duration))
        enriched = ctx.run("enrich", lambda: _enrich_guide(video_id, key, container, domain_data))

        def do_save() -> None:
            _save(session, video_id, transcript, aligned, re_tagged, enriched)

        ctx.run("save", do_save)

        log_usage_total(f"video {video_id}")
        return "DONE"
    except Exception as err:
        message = str(err) or "An error occurred during transcription. Please try again."
        # Mark FAILED before re-raising: a row stuck on PROCESSING would make
        # the caller poll forever.
        _fail(session, video_id, message)
        raise
