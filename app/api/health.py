"""Liveness/readiness probe.

Deliberately does NOT touch the database or storage, so a brief Neon or Blob
outage cannot take the pod out of rotation.

It reports the configured models because a model switch is otherwise
unverifiable: if the key lacks access to the configured model the client falls
back silently and the output still looks fine.
"""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.config import get_settings
from app.schemas.base import to_node_iso

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "ts": to_node_iso(datetime.now(UTC)),
        "models": {
            "chatModel": s.chat_model,
            "chatModelMini": s.chat_model_mini,
            "visionModel": s.vision_model,
            "transcribeModel": s.transcribe_model,
        },
        "storageBackend": "azure" if s.use_azure else "s3",
    }
