"""POST /api/v1/upload.

Ported from youtube-clone/src/app/api/v1/upload/route.ts.

Creates a video record and returns a presigned URL the client PUTs the bytes to.
The service never proxies the upload — the file goes straight to storage.
"""

import logging
import re
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import storage
from app.db import get_db
from app.models import TranscriptStatus, Video

log = logging.getLogger(__name__)
router = APIRouter()

_WHITESPACE = re.compile(r"\s+")


def build_key(filename: str) -> str:
    """`videos/<epoch-ms>-<sanitized filename>`.

    The timestamp keeps two uploads of the same filename from colliding; the
    whitespace swap keeps the key usable in a URL without escaping.
    """
    return f"videos/{int(time.time() * 1000)}-{_WHITESPACE.sub('-', filename)}"


@router.post("/api/v1/upload")
async def upload(payload: dict, db: Session = Depends(get_db)) -> JSONResponse:
    title = (payload.get("title") or "").strip()
    filename = (payload.get("filename") or "").strip()
    description = payload.get("description") or ""
    content_type = payload.get("contentType") or "video/mp4"

    if not title or not filename:
        return JSONResponse({"error": "Title and filename are required"}, status_code=400)

    key = build_key(filename)

    try:
        target = storage.presigned_upload_url(key, content_type)

        video = Video(
            title=title,
            description=description,
            # The PUBLIC url — a SAS token stored here would expire and poison
            # every later read of this row.
            blob_url=storage.s3_url(key),
            transcript_status=TranscriptStatus.PENDING,
        )
        db.add(video)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("[upload] failed for key %s", key)
        return JSONResponse({"error": "Upload failed"}, status_code=500)

    return JSONResponse(
        {"id": video.id, "uploadUrl": target.url, "uploadHeaders": target.headers},
        status_code=201,
    )
