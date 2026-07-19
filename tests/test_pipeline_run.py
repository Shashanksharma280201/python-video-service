"""End-to-end orchestration.

Ported from youtube-clone/src/workflows/transcribe-video.ts.

Every pipeline module is faked here — this tests the ORCHESTRATION: that steps
run in the right order, that results flow between them correctly, that a failure
marks the video FAILED rather than leaving it stuck PROCESSING, and that a
restart resumes instead of replaying.

The real modules are covered by their own suites.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, TranscriptStatus, Video
from app.worker import pipeline as pl
from app.worker.steps import StepContext


@pytest.fixture
def maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


@pytest.fixture
def video(maker):
    with maker() as s:
        v = Video(
            title="clip.mp4",
            blob_url="https://acct.blob.core.windows.net/videosvc/videos/clip.mp4",
            external_id="r-1",
            transcript_status=TranscriptStatus.PROCESSING,
        )
        s.add(v)
        s.commit()
        return v.id


class Calls(list):
    def names(self):
        return [c[0] for c in self]


@pytest.fixture
def fakes(monkeypatch):
    """Replace every pipeline module with a recorder."""
    calls = Calls()

    def rec(name, result):
        def f(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        return f

    monkeypatch.setattr(
        pl, "parse_storage_url", rec("parse_storage_url", ("videosvc", "videos/clip.mp4"))
    )
    monkeypatch.setattr(pl, "presigned_download_url", rec("presign", "https://signed/clip.mp4"))
    monkeypatch.setattr(pl, "probe_duration", rec("probe", 900.0))
    monkeypatch.setattr(pl, "extract_audio_slice", rec("slice", None))
    monkeypatch.setattr(pl, "detect_silent_windows", rec("silence", []))
    monkeypatch.setattr(
        pl,
        "transcribe_audio_file",
        rec(
            "transcribe",
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 5.0,
                    "text": "hello there",
                    "no_speech_prob": 0.0,
                    "avg_logprob": 0.0,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        pl, "analyze_video", rec("analyze", {"category": "Repair", "phases": ["Intro"]})
    )
    monkeypatch.setattr(
        pl,
        "tag_segments",
        rec(
            "tag",
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 5.0,
                    "text": "hello there",
                    "mainTag": "intro",
                    "subTag": "s",
                }
            ],
        ),
    )
    monkeypatch.setattr(pl, "find_unspoken_gaps", rec("gaps", []))
    monkeypatch.setattr(pl, "chunk_long_gaps", rec("chunk_gaps", []))
    monkeypatch.setattr(pl, "build_silent_segments", rec("silent_segments", []))
    monkeypatch.setattr(
        pl,
        "consolidate_chapters",
        rec(
            "consolidate",
            [{"id": 0, "start": 0.0, "end": 5.0, "text": "", "mainTag": "intro", "subTag": "s"}],
        ),
    )
    monkeypatch.setattr(
        pl,
        "generate_video_segments",
        rec(
            "thumbnails",
            [
                {
                    "mainTag": "intro",
                    "subTag": "s",
                    "start": 0.0,
                    "end": 5.0,
                    "thumbnailPath": "https://t/0.jpg",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        pl,
        "summarize_chunks",
        rec(
            "summarize", [{"title": "Intro bit", "summarizedText": "A thing", "tools": ["Spanner"]}]
        ),
    )
    monkeypatch.setattr(
        pl, "reassign_other_tags", lambda segs: (calls.append(("reassign", (segs,), {})), segs)[1]
    )
    monkeypatch.setattr(
        pl,
        "extract_domain_data",
        rec("domain", {**pl.empty_domain(), "machine": "Pump", "summary": "S"}),
    )
    monkeypatch.setattr(pl, "enrich_steps_with_vision", rec("enrich", None))
    return calls


# ─── happy path ───────────────────────────────────────────────────────────────


def test_a_full_run_marks_the_video_done(maker, video, fakes):
    with maker() as s:
        assert pl.run_pipeline(s, video) == "DONE"
        assert s.get(Video, video).transcript_status == TranscriptStatus.DONE


def test_the_results_are_persisted(maker, video, fakes):
    with maker() as s:
        pl.run_pipeline(s, video)
        v = s.get(Video, video)
        assert v.transcript == "hello there"
        assert v.transcript_segments[0]["text"] == "hello there"
        assert v.topic_segments[0]["mainTag"] == "intro"
        assert v.domain_data["machine"] == "Pump"


def test_the_feed_thumbnail_is_denormalized_onto_the_video(maker, video, fakes):
    """So a feed query never has to pull the heavy transcript JSON."""
    with maker() as s:
        pl.run_pipeline(s, video)
        assert s.get(Video, video).thumbnail_url == "https://t/0.jpg"


def test_chunk_titles_reach_the_stored_chapters(maker, video, fakes):
    """chunkTitle is the newest contract field — it must survive to the DB."""
    with maker() as s:
        pl.run_pipeline(s, video)
        assert s.get(Video, video).topic_segments[0]["title"] == "Intro bit"
        assert s.get(Video, video).topic_segments[0]["tools"] == ["Spanner"]


def test_steps_run_in_the_documented_order(maker, video, fakes):
    with maker() as s:
        pl.run_pipeline(s, video)
    order = fakes.names()
    for earlier, later in [
        ("probe", "transcribe"),
        ("transcribe", "tag"),
        ("tag", "consolidate"),
        ("consolidate", "thumbnails"),
        ("thumbnails", "summarize"),
        ("summarize", "reassign"),
        ("reassign", "domain"),
        ("domain", "enrich"),
    ]:
        assert order.index(earlier) < order.index(later), f"{earlier} must precede {later}"


def test_summaries_are_produced_before_reassignment(maker, video, fakes):
    """Reassignment classifies 'other' chapters FROM their summaries, so it
    cannot run first or it has nothing to work with."""
    order = fakes.names()
    with maker() as s:
        pl.run_pipeline(s, video)
    order = fakes.names()
    assert order.index("summarize") < order.index("reassign")


def test_the_transcript_is_aligned_to_the_final_chapter_tags(maker, video, fakes, monkeypatch):
    """Chapters are the source of truth for the phase — the transcript view
    must not contradict the chapter list it was built from."""
    monkeypatch.setattr(
        pl,
        "reassign_other_tags",
        lambda segs: [{**c, "mainTag": "diagnosis"} for c in segs],
    )
    with maker() as s:
        pl.run_pipeline(s, video)
        assert s.get(Video, video).transcript_segments[0]["mainTag"] == "diagnosis"


# ─── failure behaviour ────────────────────────────────────────────────────────


def test_a_failure_marks_the_video_failed(maker, video, fakes, monkeypatch):
    """A stuck PROCESSING row would make the caller poll forever."""

    def boom(*a, **k):
        raise RuntimeError("transcription exploded")

    monkeypatch.setattr(pl, "probe_duration", boom)

    with maker() as s:
        with pytest.raises(RuntimeError):
            pl.run_pipeline(s, video)
        assert s.get(Video, video).transcript_status == TranscriptStatus.FAILED


def test_the_failure_message_is_recorded(maker, video, fakes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("transcription exploded")

    monkeypatch.setattr(pl, "probe_duration", boom)
    with maker() as s:
        with pytest.raises(RuntimeError):
            pl.run_pipeline(s, video)
        assert "transcription exploded" in s.get(Video, video).transcript


def test_a_missing_video_fails_fast(maker, fakes):
    with maker() as s:
        with pytest.raises(pl.FatalError):
            pl.run_pipeline(s, "no-such-video")


def test_a_vision_failure_does_not_fail_the_video(maker, video, fakes, monkeypatch):
    """Silent-stretch descriptions are a bonus; chapters still stand."""

    def boom(*a, **k):
        raise RuntimeError("vision down")

    monkeypatch.setattr(pl, "build_silent_segments", boom)
    with maker() as s:
        assert pl.run_pipeline(s, video) == "DONE"


def test_a_thumbnail_failure_does_not_fail_the_video(maker, video, fakes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ffmpeg down")

    monkeypatch.setattr(pl, "generate_video_segments", boom)
    with maker() as s:
        assert pl.run_pipeline(s, video) == "DONE"
        # Chapters survive, just without pictures.
        assert s.get(Video, video).topic_segments[0]["thumbnailPath"] is None


def test_a_domain_failure_does_not_fail_the_video(maker, video, fakes, monkeypatch):
    """The guide is the most valuable output but still must not block saving."""

    def boom(*a, **k):
        raise RuntimeError("guide down")

    monkeypatch.setattr(pl, "extract_domain_data", boom)
    with maker() as s:
        assert pl.run_pipeline(s, video) == "DONE"
        assert s.get(Video, video).domain_data == pl.empty_domain()


def test_a_guide_enrichment_failure_does_not_fail_the_video(maker, video, fakes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("locate down")

    monkeypatch.setattr(pl, "enrich_steps_with_vision", boom)
    with maker() as s:
        assert pl.run_pipeline(s, video) == "DONE"


# ─── resume ───────────────────────────────────────────────────────────────────


def test_a_rerun_after_a_crash_does_not_redo_completed_steps(maker, video, fakes):
    with maker() as s:
        pl.run_pipeline(s, video)

    first_transcribes = fakes.names().count("transcribe")
    assert first_transcribes > 0

    fakes.clear()
    with maker() as s:  # a restarted worker
        assert pl.run_pipeline(s, video) == "DONE"

    assert "transcribe" not in fakes.names()
    assert "domain" not in fakes.names()


def test_the_ledger_records_every_step(maker, video, fakes):
    with maker() as s:
        pl.run_pipeline(s, video)
        done = StepContext(s, video).completed()

    assert "prepare" in done
    assert any(k.startswith("transcribe:") for k in done)
    assert {"tag", "frames", "summarize", "reassign", "domain", "enrich", "save"} <= done


def test_every_presign_targets_the_videos_own_container(maker, video, fakes):
    """Ingested videos may live in a TENANT's container, not the default one.

    Presigning against the default container yields a URL that 404s, and the
    failure surfaces much later as an ffmpeg error with no obvious cause.
    """
    with maker() as s:
        pl.run_pipeline(s, video)

    presigns = [c for c in fakes if c[0] == "presign"]
    assert presigns, "expected at least one presign"
    for _name, args, kwargs in presigns:
        container = kwargs.get("container") or (args[1] if len(args) > 1 else None)
        assert container == "videosvc", f"presign missing container: args={args} kwargs={kwargs}"


def test_a_long_video_fans_out_one_transcribe_step_per_slice(maker, video, fakes, monkeypatch):
    monkeypatch.setattr(pl, "probe_duration", lambda *a, **k: 1800.0)  # 30 min
    with maker() as s:
        pl.run_pipeline(s, video)
        done = StepContext(s, video).completed()

    slices = [k for k in done if k.startswith("transcribe:")]
    assert len(slices) == 3  # 3 x 10-minute chunks
    assert {"transcribe:0", "transcribe:600", "transcribe:1200"} == set(slices)
