"""Celery tasks.

Thin by design: the task owns the session and the retry policy, and defers all
pipeline logic to run_pipeline.

The retry policy replaces the Node engine's RetryableError/FatalError handling.
Getting it wrong is expensive in both directions — retrying a FatalError burns
quota re-failing, and NOT retrying a rate limit fails a video that would have
succeeded twenty minutes later.
"""

import logging

from app.db import get_sessionmaker
from app.pipeline.transcribe import RateLimitedError
from app.worker.celery_app import celery_app
from app.worker.pipeline import FatalError, run_pipeline

log = logging.getLogger(__name__)

# A 4hr video can legitimately cross many quota windows before finishing, so the
# ceiling is high. The step ledger means each retry resumes rather than restarts.
MAX_RETRIES = 25
DEFAULT_RETRY_DELAY = 60  # seconds
MAX_RETRY_DELAY = 3600  # never park a job for more than an hour


def should_retry(err: Exception) -> bool:
    """A FatalError means the input is wrong — retrying cannot fix it."""
    return not isinstance(err, FatalError)


def retry_delay_for(err: Exception) -> int:
    """How long to wait before the next attempt.

    A rate limit tells us exactly how long to wait, so honour it rather than
    guessing — retrying early just burns another rejection.
    """
    if isinstance(err, RateLimitedError):
        return max(1, min(int(err.retry_after_secs), MAX_RETRY_DELAY))
    return DEFAULT_RETRY_DELAY


@celery_app.task(
    name="process_video",
    bind=True,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_video(self, video_id: str) -> str:
    """Run the extraction pipeline for one video."""
    log.info("[task] starting video %s (attempt %d)", video_id, self.request.retries + 1)

    try:
        with get_sessionmaker()() as session:
            return run_pipeline(session, video_id)
    except Exception as err:
        if not should_retry(err):
            log.error("[task] video %s failed fatally: %s", video_id, err)
            raise

        delay = retry_delay_for(err)
        log.warning(
            "[task] video %s attempt %d failed (%s) — retrying in %ds",
            video_id,
            self.request.retries + 1,
            err,
            delay,
        )
        raise self.retry(exc=err, countdown=delay) from err
