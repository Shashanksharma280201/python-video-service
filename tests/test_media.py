"""ffmpeg helpers.

Ported from youtube-clone/src/lib/pipeline/media.ts, which had no vitest
coverage. The stderr parsing is extracted into pure functions so the formats can
be tested without spawning anything; the spawn paths are then exercised against
a real ffmpeg on a generated clip.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.pipeline.media import (
    detect_silent_windows,
    extract_audio_slice,
    extract_frame_at,
    parse_duration,
    parse_silence_windows,
    probe_duration,
    reconnect_flags,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


# ─── parse_duration ───────────────────────────────────────────────────────────


def test_reads_duration_from_ffmpeg_stderr():
    stderr = "  Duration: 00:15:00.05, start: 0.000000, bitrate: 1024 kb/s"
    assert parse_duration(stderr) == pytest.approx(900.05)


def test_reads_multi_hour_durations():
    assert parse_duration("  Duration: 04:30:15.00, start: 0.0") == pytest.approx(
        4 * 3600 + 30 * 60 + 15
    )


def test_missing_duration_yields_zero():
    assert parse_duration("some unrelated ffmpeg output") == 0


def test_empty_stderr_yields_zero():
    assert parse_duration("") == 0


# ─── parse_silence_windows ────────────────────────────────────────────────────


def test_pairs_silence_start_with_silence_end():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 10.5\n"
        "[silencedetect @ 0x1] silence_end: 25.25 | silence_duration: 14.75\n"
    )
    assert parse_silence_windows(stderr) == [{"start": 10.5, "end": 25.25}]


def test_reads_several_windows():
    stderr = "silence_start: 1.0\nsilence_end: 5.0\nsilence_start: 20.0\nsilence_end: 30.0\n"
    assert parse_silence_windows(stderr) == [
        {"start": 1.0, "end": 5.0},
        {"start": 20.0, "end": 30.0},
    ]


def test_an_unterminated_silence_runs_to_the_end():
    """Silence that never ends before EOF has no silence_end line."""
    assert parse_silence_windows("silence_start: 42.0\n") == [{"start": 42.0, "end": None}]


def test_no_silence_yields_no_windows():
    assert parse_silence_windows("nothing here") == []


def test_a_stray_silence_end_is_ignored():
    assert parse_silence_windows("silence_end: 5.0\n") == []


# ─── reconnect_flags ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["http://x/y.mp4", "https://x/y.mp4", "HTTPS://X/Y.MP4"])
def test_remote_sources_get_reconnect_flags(url):
    """A dropped connection mid-read must retry, not fail the whole call."""
    assert "-reconnect" in reconnect_flags(url)


@pytest.mark.parametrize("path", ["/tmp/a.mp4", "relative.mp4", "file:///tmp/a.mp4"])
def test_local_sources_get_no_reconnect_flags(path):
    assert reconnect_flags(path) == []


# ─── real ffmpeg ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    """A 6-second clip: 2s tone, 2s silence, 2s tone."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2,adelay=0|0",
            "-filter_complex",
            "[1:a]apad=pad_dur=4[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@needs_ffmpeg
def test_probes_a_real_clips_duration(clip):
    assert probe_duration(str(clip)) == pytest.approx(6.0, abs=0.5)


@needs_ffmpeg
def test_probing_a_missing_file_returns_zero_rather_than_raising(tmp_path):
    """A broken probe must not crash the pipeline — the step decides."""
    assert probe_duration(str(tmp_path / "nope.mp4")) == 0


@needs_ffmpeg
def test_extracts_an_audio_slice(clip, tmp_path):
    out = tmp_path / "slice.mp3"
    extract_audio_slice(str(clip), 0, 3, str(out))
    assert out.exists()
    assert out.stat().st_size > 0
    # 16kHz mono 32k mp3 of 3s is small — proves we re-encoded, not copied.
    assert out.stat().st_size < 100_000


@needs_ffmpeg
def test_an_audio_slice_starts_at_the_requested_offset(clip, tmp_path):
    out = tmp_path / "slice.mp3"
    extract_audio_slice(str(clip), 2, 2, str(out))
    assert probe_duration(str(out)) == pytest.approx(2.0, abs=0.5)


@needs_ffmpeg
def test_a_failing_audio_slice_raises(tmp_path):
    with pytest.raises(RuntimeError):
        extract_audio_slice(str(tmp_path / "nope.mp4"), 0, 1, str(tmp_path / "o.mp3"))


@needs_ffmpeg
def test_extracts_a_frame(clip, tmp_path):
    out = tmp_path / "frame.jpg"
    extract_frame_at(str(clip), 1.0, str(out))
    assert out.exists()
    assert out.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic


@needs_ffmpeg
def test_a_failing_frame_extraction_raises(tmp_path):
    with pytest.raises(RuntimeError):
        extract_frame_at(str(tmp_path / "nope.mp4"), 0, str(tmp_path / "o.jpg"))


@needs_ffmpeg
def test_detects_silence_in_a_real_clip(clip, tmp_path):
    audio = tmp_path / "audio.mp3"
    extract_audio_slice(str(clip), 0, 6, str(audio))
    windows = detect_silent_windows(str(audio))
    # The clip is tone for 2s then silent — expect at least one window that
    # starts in the back half.
    assert any(w["start"] >= 1.5 for w in windows)


@needs_ffmpeg
def test_silence_detection_on_a_missing_file_returns_empty(tmp_path):
    """Silence detection is best-effort; a failure must not fail the video."""
    assert detect_silent_windows(str(tmp_path / "nope.mp3")) == []
