"""POST /api/v1/videoExtraction.

Ported from youtube-clone/src/app/api/v1/videoExtraction/route.ts.

ASYNCHRONOUS: the call kicks off the pipeline and returns immediately. It never
holds the request open — a multi-hour video is processed in the background and
the caller learns it finished by asking again.

The contract:
  first call for a new resourceId  -> 202, work started
  call again while running          -> 202, still running
  call again once finished          -> 200 with the full result
  failed                            -> 409

Processing is IDEMPOTENT per resourceId: an existing resource is never
reprocessed. The call attaches to the in-flight run or returns the finished
result straight away.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TranscriptStatus, Video
from app.services.extraction_response import build_extraction_response
from app.storage import StorageUrlError, exists, parse_storage_url

log = logging.getLogger(__name__)
router = APIRouter()


# Indirected so tests can substitute them without a broker or a storage account.
def enqueue_video(video_id: str) -> None:
    from app.worker.tasks import process_video

    process_video.delay(video_id)


def blob_exists(key: str, container: str) -> bool:
    return exists(key, container)


def _respond(video: Video) -> JSONResponse:
    status = video.transcript_status

    if status == TranscriptStatus.DONE:
        return JSONResponse(build_extraction_response(video), status_code=200)

    if status == TranscriptStatus.FAILED:
        return JSONResponse(
            {
                "resourceId": video.external_id or video.id,
                "status": "FAILED",
                "error": "processing failed",
            },
            status_code=409,
        )

    return JSONResponse(
        {
            "resourceId": video.external_id or video.id,
            "machineId": video.machine_id,
            "tenantId": video.tenant_id,
            "status": str(status),
            "chunks": [],
            "chunkCount": 0,
        },
        status_code=202,
    )


@router.post("/api/v1/videoExtraction")
async def video_extraction(payload: dict, db: Session = Depends(get_db)) -> JSONResponse:
    machine_id = (payload.get("machineId") or "").strip()
    resource_id = (payload.get("resourceId") or "").strip()
    tenant_id = (payload.get("tenantId") or "").strip()
    video_url = (payload.get("videoURL") or "").strip()

    if not (machine_id and resource_id and tenant_id and video_url):
        return JSONResponse(
            {"error": "machineId, resourceId, tenantId and videoURL are required"},
            status_code=400,
        )

    # Existing resource: never reprocess. Return its current state.
    existing = db.query(Video).filter_by(external_id=resource_id).one_or_none()
    if existing is not None:
        return _respond(existing)

    try:
        container, key = parse_storage_url(video_url)
    except StorageUrlError:
        return JSONResponse(
            {"error": "videoURL must point at the configured storage account"},
            status_code=400,
        )

    try:
        present = blob_exists(key, container)
    except Exception:
        # Reaching storage failed outright — bad credentials, network, outage.
        # That is OUR problem, not the caller's, so it stays a 500 (as the Node
        # service does), but with a legible body instead of a bare traceback:
        # a misconfigured storage key is otherwise very hard to tell apart from
        # a genuinely missing blob.
        log.exception("[videoExtraction] storage check failed for %s/%s", container, key)
        return JSONResponse({"error": "Storage unavailable"}, status_code=500)

    if not present:
        return JSONResponse({"error": "Video file not found in storage"}, status_code=404)

    video = Video(
        external_id=resource_id,
        machine_id=machine_id,
        tenant_id=tenant_id,
        title=key.split("/")[-1] or key,
        description="",
        blob_url=video_url,
        transcript_status=TranscriptStatus.PROCESSING,
    )
    try:
        db.add(video)
        db.commit()
    except IntegrityError:
        # Two callers raced past the lookup above and both tried to insert; the
        # external_id unique constraint let exactly one win. The loser attaches
        # to the winner's run rather than erroring.
        db.rollback()
        winner = db.query(Video).filter_by(external_id=resource_id).one_or_none()
        if winner is not None:
            return _respond(winner)
        raise

    try:
        enqueue_video(video.id)
    except Exception:
        # Without this the row would sit PROCESSING forever with no worker
        # coming for it, and the caller would poll indefinitely.
        log.exception("[videoExtraction] failed to enqueue %s", video.id)
        video.transcript_status = TranscriptStatus.FAILED
        db.commit()
        return JSONResponse({"error": "Failed to start processing"}, status_code=500)

    return _respond(video)
