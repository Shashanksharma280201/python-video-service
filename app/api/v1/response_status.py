"""GET /api/v1/response-status.

Ported from youtube-clone/src/app/api/v1/response-status/route.ts.

The read-only companion to POST /videoExtraction, which STARTS the job: the
caller polls here until the work finishes, then reads the result.

HTTP status answers "did the status CHECK work", NOT the job state:

  200 — resource found; the JOB state is in the body's `status` field
        (PROCESSING | DONE | FAILED). On DONE the full result is returned
        inline, so one poll loop yields everything — no second call.
  404 — no video for this resourceId
  400 — resourceId missing

This deliberately avoids the "202 looks like success" trap: a caller reads
body.status, never the HTTP code, to decide whether to keep polling.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TranscriptStatus, Video
from app.services.extraction_response import build_extraction_response

router = APIRouter()

POLL_AFTER_MS = 5000


@router.get("/api/v1/response-status")
async def response_status(resourceId: str = "", db: Session = Depends(get_db)) -> JSONResponse:
    resource_id = (resourceId or "").strip()
    if not resource_id:
        return JSONResponse({"error": "resourceId is required"}, status_code=400)

    video = db.query(Video).filter_by(external_id=resource_id).one_or_none()
    if video is None:
        return JSONResponse({"resourceId": resource_id, "status": "NOT_FOUND"}, status_code=404)

    status = video.transcript_status

    # Done — hand back the complete result inline.
    if status == TranscriptStatus.DONE:
        return JSONResponse(build_extraction_response(video), status_code=200)

    # Failed — a 200 so the poll loop stops cleanly. The status check itself
    # succeeded; it is the job that did not.
    if status == TranscriptStatus.FAILED:
        return JSONResponse(
            {
                "resourceId": video.external_id or video.id,
                "machineId": video.machine_id,
                "tenantId": video.tenant_id,
                "status": "FAILED",
                "error": "processing failed",
            },
            status_code=200,
        )

    # Still running — tell the caller when to ask again.
    return JSONResponse(
        {
            "resourceId": video.external_id or video.id,
            "machineId": video.machine_id,
            "tenantId": video.tenant_id,
            "status": str(status),
            "pollAfterMs": POLL_AFTER_MS,
        },
        status_code=200,
    )
