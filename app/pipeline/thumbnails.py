"""Extract one thumbnail per chapter and upload it.

Ported from youtube-clone/src/lib/pipeline/thumbnails.ts.

`source` is a local path or a presigned URL — ffmpeg seeks either, so a long
video never has to be downloaded in full.

The uploader is injected rather than imported so this module stays testable
without touching storage; the default is the real facade.
"""

import logging
import math
import os
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.pipeline.media import extract_frame_at
from app.pipeline.types import THUMB_CONCURRENCY, TaggedSegment, VideoSegment

log = logging.getLogger(__name__)

Uploader = Callable[[str, str, str], str]


def _default_uploader(local_path: str, key: str, content_type: str) -> str:
    from app.storage import get_backend

    return get_backend().upload_file(local_path, key, content_type)


def thumb_name(start: float) -> str:
    """Name by start time in centiseconds.

    Globally unique even when chapters are processed in separate batches or
    steps, which a plain index would not be.

    Uses floor(x + 0.5), NOT Python's round(). Python rounds halves to even
    (round(12.5) == 12) while JavaScript's Math.round rounds halves up
    (Math.round(12.5) == 13). Both services can write to the same blob account,
    so a chapter starting on an exact half-centisecond would otherwise land on
    two different keys depending on which service produced it.
    """
    return f"segment-{math.floor(start * 100 + 0.5)}.jpg"


def generate_video_segments(
    segments: list[TaggedSegment],
    source: str,
    video_id: str,
    upload: Uploader | None = None,
) -> list[VideoSegment]:
    if not segments:
        return []

    uploader = upload or _default_uploader
    thumbnail_dir = os.path.join(tempfile.gettempdir(), f"thumbs-{video_id}")
    os.makedirs(thumbnail_dir, exist_ok=True)

    def one(seg: TaggedSegment) -> VideoSegment:
        name = thumb_name(seg["start"])
        abs_path = os.path.join(thumbnail_dir, name)
        key = f"thumbnails/{video_id}/{name}"

        thumbnail_path: str | None = None
        try:
            extract_frame_at(source, seg["start"], abs_path)
            if os.path.exists(abs_path):
                thumbnail_path = uploader(abs_path, key, "image/jpeg")
        except Exception as err:
            # Non-fatal — the chapter still works without a thumbnail, and one
            # bad frame must not cost the other chapters theirs.
            log.warning("[thumbnails] segment at %.2fs failed: %s", seg["start"], err)

        return {
            "mainTag": seg["mainTag"],
            "subTag": seg["subTag"],
            "start": seg["start"],
            "end": seg["end"],
            "thumbnailPath": thumbnail_path,
        }

    with ThreadPoolExecutor(max_workers=THUMB_CONCURRENCY) as pool:
        return list(pool.map(one, segments))
