"""One thumbnail per chapter.

Ported from youtube-clone/src/lib/pipeline/thumbnails.ts, which had no vitest
coverage.

A missing thumbnail is explicitly NOT fatal: the chapter still works without
one, so every failure path yields thumbnailPath=None rather than raising. The
Node service returned 40/40 thumbnails on a real run, but one bad frame must
not lose the other 39.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.pipeline.thumbnails import generate_video_segments, thumb_name

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def seg(start, end, main_tag="repair", sub_tag="doing a thing"):
    return {
        "id": int(start),
        "start": start,
        "end": end,
        "text": "t",
        "mainTag": main_tag,
        "subTag": sub_tag,
    }


class FakeUploader:
    """Stands in for blob storage; records what it was asked to upload."""

    def __init__(self, fail=False):
        self.uploads = []
        self.fail = fail

    def __call__(self, local_path: str, key: str, content_type: str) -> str:
        if self.fail:
            raise RuntimeError("upload exploded")
        self.uploads.append((local_path, key, content_type))
        return f"https://acct.blob.core.windows.net/videosvc/{key}"


# ─── thumb_name ───────────────────────────────────────────────────────────────


def test_names_are_derived_from_the_start_time_in_centiseconds():
    """Naming by start time keeps names unique even across separate batches."""
    assert thumb_name(12.34) == "segment-1234.jpg"


def test_names_round_rather_than_truncate():
    assert thumb_name(12.345) == "segment-1235.jpg"


def test_distinct_starts_give_distinct_names():
    assert thumb_name(0) != thumb_name(0.01)


# ─── generate_video_segments ──────────────────────────────────────────────────


def test_no_segments_yields_no_work():
    up = FakeUploader()
    assert generate_video_segments([], "src.mp4", "v1", upload=up) == []
    assert up.uploads == []


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("thumbs") / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@needs_ffmpeg
def test_produces_one_segment_per_input_with_an_uploaded_thumbnail(clip):
    up = FakeUploader()
    out = generate_video_segments([seg(1, 5), seg(5, 9)], str(clip), "v1", upload=up)

    assert len(out) == 2
    assert len(up.uploads) == 2
    assert all(s["thumbnailPath"] is not None for s in out)


@needs_ffmpeg
def test_carries_the_chapter_fields_through(clip):
    out = generate_video_segments(
        [seg(1, 5, "diagnosis", "checking the coil")], str(clip), "v1", upload=FakeUploader()
    )
    assert out[0]["mainTag"] == "diagnosis"
    assert out[0]["subTag"] == "checking the coil"
    assert out[0]["start"] == 1
    assert out[0]["end"] == 5


@needs_ffmpeg
def test_uploads_under_a_per_video_prefix(clip):
    """Keying by video id is what makes cleanup a prefix delete."""
    up = FakeUploader()
    generate_video_segments([seg(1, 5)], str(clip), "vid-42", upload=up)
    _local, key, content_type = up.uploads[0]
    assert key == "thumbnails/vid-42/segment-100.jpg"
    assert content_type == "image/jpeg"


@needs_ffmpeg
def test_segment_order_is_preserved(clip):
    out = generate_video_segments(
        [seg(1, 3), seg(3, 5), seg(5, 7)], str(clip), "v1", upload=FakeUploader()
    )
    assert [s["start"] for s in out] == [1, 3, 5]


def test_a_frame_that_cannot_be_extracted_yields_a_null_thumbnail(tmp_path):
    """Non-fatal: the chapter still exists, just without a picture."""
    up = FakeUploader()
    out = generate_video_segments([seg(1, 5)], str(tmp_path / "missing.mp4"), "v1", upload=up)
    assert len(out) == 1
    assert out[0]["thumbnailPath"] is None
    assert up.uploads == []


@needs_ffmpeg
def test_an_upload_failure_yields_a_null_thumbnail_not_an_exception(clip):
    out = generate_video_segments([seg(1, 5)], str(clip), "v1", upload=FakeUploader(fail=True))
    assert out[0]["thumbnailPath"] is None


@needs_ffmpeg
def test_one_bad_segment_does_not_lose_the_others(clip):
    """A frame past the end of the clip fails; its neighbours must survive."""
    up = FakeUploader()
    out = generate_video_segments(
        [seg(1, 3), seg(9999, 10000), seg(5, 7)], str(clip), "v1", upload=up
    )
    assert len(out) == 3
    assert out[0]["thumbnailPath"] is not None
    assert out[2]["thumbnailPath"] is not None
