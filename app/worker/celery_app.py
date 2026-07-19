"""Celery application.

The API pod enqueues; this worker consumes. Configuration is deliberately
conservative — a video is expensive to redo, so correctness beats throughput.
"""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "video-service",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    # Acknowledge only AFTER the task finishes. A worker killed mid-video has
    # its message redelivered rather than silently dropped — combined with the
    # step ledger, the redelivered run resumes instead of starting over.
    task_acks_late=True,
    # A lost worker's task goes back on the queue.
    task_reject_on_worker_lost=True,
    # One video at a time per worker process. Video work is CPU and network
    # heavy; prefetching would leave jobs parked behind a four-hour run.
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Results are a debugging aid; the Video row is the source of truth.
    result_expires=24 * 3600,
)
