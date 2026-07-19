"""FastAPI application factory.

The API process is stateless and never processes video — it validates input,
enqueues work and reads results. All ffmpeg/OpenAI work lives in the Celery
worker (app/worker), so this pod stays small and restarts cheaply.
"""

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from app.api import health
from app.api.v1 import response_status, upload, video_extraction
from app.deps.auth import require_service_key


def create_app() -> FastAPI:
    app = FastAPI(
        title="Video Extraction Service",
        version="0.1.0",
        description="Internal service — Python port of the Node video extraction service.",
    )

    app.include_router(health.router, tags=["health"])

    # Everything under /api/v1 is gated by the service key. /api/health is not.
    gated = [Depends(require_service_key)]
    app.include_router(upload.router, tags=["v1"], dependencies=gated)
    app.include_router(video_extraction.router, tags=["v1"], dependencies=gated)
    app.include_router(response_status.router, tags=["v1"], dependencies=gated)

    @app.exception_handler(404)
    async def not_found(_request, _exc) -> JSONResponse:
        return JSONResponse({"error": "Not found"}, status_code=404)

    return app


app = create_app()
