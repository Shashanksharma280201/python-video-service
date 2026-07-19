"""Celery task wiring.

The task itself is thin — it owns the session and the retry policy, and defers
everything else to run_pipeline. What matters here is the retry semantics, which
replace the Node engine's RetryableError/FatalError handling:

  RateLimitedError -> retry after the delay the API asked for. A long video can
                      legitimately pace across hours of quota windows, so this
                      must NOT be treated as a failure.
  FatalError       -> never retry. The input is wrong; retrying cannot fix it
                      and would burn quota re-failing.
  anything else    -> bounded retries with backoff.
"""

import pytest

from app.pipeline.transcribe import RateLimitedError
from app.worker import tasks
from app.worker.pipeline import FatalError


def test_the_celery_app_is_configured_from_the_environment(monkeypatch):
    from app.worker.celery_app import celery_app

    assert celery_app.conf.broker_url
    assert celery_app.conf.task_acks_late is True, (
        "a worker killed mid-task must have its message redelivered, not dropped"
    )


def test_a_rate_limit_is_retried_after_the_requested_delay():
    err = RateLimitedError("slow down", 900)
    assert tasks.retry_delay_for(err) == 900


def test_a_rate_limit_delay_is_clamped_to_something_sane():
    """A pathological retry-after must not park a job for a week."""
    assert tasks.retry_delay_for(RateLimitedError("x", 10**9)) <= tasks.MAX_RETRY_DELAY


def test_a_rate_limit_delay_has_a_floor():
    assert tasks.retry_delay_for(RateLimitedError("x", 0)) >= 1


@pytest.mark.parametrize("err", [RuntimeError("boom"), OSError("disk")])
def test_generic_errors_use_backoff(err):
    assert tasks.retry_delay_for(err) == tasks.DEFAULT_RETRY_DELAY


def test_fatal_errors_are_never_retried():
    assert tasks.should_retry(FatalError("bad input")) is False


def test_rate_limits_are_retried():
    assert tasks.should_retry(RateLimitedError("x", 60)) is True


def test_generic_errors_are_retried():
    assert tasks.should_retry(RuntimeError("boom")) is True


def test_the_task_is_registered_under_a_stable_name():
    """The API enqueues by NAME, so renaming it silently breaks ingestion."""
    assert "process_video" in tasks.celery_app.tasks


def test_rate_limit_retries_are_generous_enough_for_a_long_video():
    """A 4hr video can cross many quota windows; a low cap would fail it."""
    assert tasks.MAX_RETRIES >= 25
