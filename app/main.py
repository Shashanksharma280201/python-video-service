"""FastAPI application factory.

The API process is stateless and never processes video — it validates input,
enqueues work and reads results. All ffmpeg/OpenAI work lives in the Celery
worker (app/worker), so this pod stays small and restarts cheaply.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api import health


def create_app() -> FastAPI:
    app = FastAPI(
        title="Video Extraction Service",
        version="0.1.0",
        description="Internal service — Python port of the Node video extraction service.",
    )

    app.include_router(health.router, tags=["health"])

    # Phase 1+ routers mount here:
    #   app.include_router(upload.router, dependencies=[Depends(require_service_key)])
    #   app.include_router(video_extraction.router, dependencies=[Depends(require_service_key)])
    #   app.include_router(response_status.router, dependencies=[Depends(require_service_key)])

    @app.exception_handler(404)
    async def not_found(_request, _exc) -> JSONResponse:
        return JSONResponse({"error": "Not found"}, status_code=404)

    return app


app = create_app()
