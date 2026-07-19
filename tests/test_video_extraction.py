"""POST /api/v1/videoExtraction and GET /api/v1/response-status.

Ported from youtube-clone/src/app/api/v1/{videoExtraction,response-status}/route.ts
and tests/responseStatus.test.ts.

The contract, which the client depends on:

  /videoExtraction is ASYNCHRONOUS. It validates, starts the job and returns
  immediately — it never holds the request open. Processing is idempotent per
  resourceId: an existing resource is never reprocessed.

  /response-status uses HTTP status to answer "did the CHECK work", not "is the
  job done". Callers read body.status, never the HTTP code. This deliberately
  avoids the "202 looks like success" trap.
"""

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import Base, TranscriptStatus, Video

FAKE_KEY = base64.b64encode(b"0" * 32).decode()
BLOB = "https://stdatadevcentralindia.blob.core.windows.net/videosvc/videos/pump.mp4"


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "stdatadevcentralindia")
    monkeypatch.setenv("AZURE_STORAGE_KEY", FAKE_KEY)
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "videosvc")
    monkeypatch.delenv("AZURE_STORAGE_ENDPOINT", raising=False)
    monkeypatch.setenv("SERVICE_API_KEY", "")
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as s:
        app.dependency_overrides[get_db] = lambda: s
        yield s
        app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def enqueued(monkeypatch):
    """Capture what the route hands to Celery instead of hitting Redis."""
    from app.api.v1 import video_extraction as ve

    calls = []
    monkeypatch.setattr(ve, "enqueue_video", lambda vid: calls.append(vid))
    return calls


@pytest.fixture
def exists_ok(monkeypatch):
    from app.api.v1 import video_extraction as ve

    monkeypatch.setattr(ve, "blob_exists", lambda key, container: True)


@pytest.fixture
def client(session, enqueued, exists_ok) -> TestClient:
    return TestClient(app)


BODY = {
    "machineId": "m-1",
    "resourceId": "r-1",
    "tenantId": "t-1",
    "videoURL": BLOB,
}


def post(client, **overrides):
    return client.post("/api/v1/videoExtraction", json={**BODY, **overrides})


def add_video(session, status, **kw):
    v = Video(
        external_id=kw.pop("external_id", "r-1"),
        machine_id="m-1",
        tenant_id="t-1",
        title="pump.mp4",
        blob_url=BLOB,
        transcript_status=status,
        **kw,
    )
    session.add(v)
    session.commit()
    return v


# ─── videoExtraction: starting a job ──────────────────────────────────────────


def test_a_new_resource_returns_202_and_starts_the_job(client, enqueued, session):
    r = post(client)
    assert r.status_code == 202
    body = r.json()
    assert body["resourceId"] == "r-1"
    assert body["status"] == "PROCESSING"
    assert body["chunks"] == []
    assert body["chunkCount"] == 0
    assert len(enqueued) == 1


def test_the_video_record_is_created_as_processing(client, session):
    post(client)
    v = session.query(Video).filter_by(external_id="r-1").one()
    assert v.transcript_status == TranscriptStatus.PROCESSING
    assert v.machine_id == "m-1"
    assert v.tenant_id == "t-1"
    assert v.blob_url == BLOB


def test_the_title_is_derived_from_the_blob_key(client, session):
    post(client)
    assert session.query(Video).filter_by(external_id="r-1").one().title == "pump.mp4"


def test_the_response_does_not_wait_for_processing(client, enqueued):
    """Async by contract — the work happens in the worker, not the request."""
    assert post(client).status_code == 202
    assert len(enqueued) == 1


# ─── videoExtraction: idempotency ─────────────────────────────────────────────


def test_calling_again_while_running_returns_202_without_reprocessing(client, enqueued, session):
    post(client)
    enqueued.clear()

    r = post(client)
    assert r.status_code == 202
    assert r.json()["status"] == "PROCESSING"
    assert enqueued == [], "an in-flight resource must never be re-enqueued"


def test_calling_again_once_finished_returns_200_with_the_full_result(client, enqueued, session):
    add_video(
        session,
        TranscriptStatus.DONE,
        topic_segments=[
            {"mainTag": "intro", "subTag": "s", "start": 0, "end": 5, "thumbnailPath": None}
        ],
        transcript_segments=[{"start": 0, "end": 5, "text": "hi"}],
    )
    r = post(client)
    assert r.status_code == 200
    assert r.json()["status"] == "DONE"
    assert r.json()["chunkCount"] == 1
    assert enqueued == []


def test_a_failed_resource_returns_409(client, session, enqueued):
    add_video(session, TranscriptStatus.FAILED)
    r = post(client)
    assert r.status_code == 409
    assert r.json()["status"] == "FAILED"
    assert enqueued == []


def test_only_one_record_exists_after_repeated_calls(client, session):
    post(client)
    post(client)
    post(client)
    assert session.query(Video).filter_by(external_id="r-1").count() == 1


# ─── videoExtraction: validation ──────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["machineId", "resourceId", "tenantId", "videoURL"])
def test_every_field_is_required(client, missing):
    r = post(client, **{missing: ""})
    assert r.status_code == 400
    assert "required" in r.json()["error"]


def test_whitespace_only_fields_are_rejected(client):
    assert post(client, resourceId="   ").status_code == 400


def test_a_url_outside_our_storage_account_is_rejected(client):
    """The SSRF guard — without it a caller could make us fetch anything."""
    r = post(client, videoURL="https://evil.example.com/x.mp4")
    assert r.status_code == 400
    assert "storage account" in r.json()["error"]


def test_a_missing_blob_returns_404(client, monkeypatch):
    from app.api.v1 import video_extraction as ve

    monkeypatch.setattr(ve, "blob_exists", lambda key, container: False)
    r = post(client)
    assert r.status_code == 404
    assert r.json()["error"] == "Video file not found in storage"


def test_no_record_is_created_when_the_blob_is_missing(client, session, monkeypatch):
    from app.api.v1 import video_extraction as ve

    monkeypatch.setattr(ve, "blob_exists", lambda key, container: False)
    post(client)
    assert session.query(Video).count() == 0


def test_a_failure_to_enqueue_marks_the_video_failed(client, session, monkeypatch):
    """Otherwise the row sits PROCESSING forever with no worker coming."""
    from app.api.v1 import video_extraction as ve

    def boom(_vid):
        raise RuntimeError("redis down")

    monkeypatch.setattr(ve, "enqueue_video", boom)
    r = post(client)
    assert r.status_code == 500
    assert session.query(Video).filter_by(external_id="r-1").one().transcript_status == (
        TranscriptStatus.FAILED
    )


# ─── response-status ──────────────────────────────────────────────────────────


def get_status(client, resource_id="r-1"):
    return client.get(f"/api/v1/response-status?resourceId={resource_id}")


def test_status_requires_a_resource_id(client):
    r = client.get("/api/v1/response-status")
    assert r.status_code == 400
    assert r.json()["error"] == "resourceId is required"


def test_an_unknown_resource_is_404_not_found(client):
    r = get_status(client, "nope")
    assert r.status_code == 404
    assert r.json() == {"resourceId": "nope", "status": "NOT_FOUND"}


def test_a_running_job_returns_200_with_a_poll_hint(client, session):
    """200 because the CHECK succeeded — the job state is in the body."""
    add_video(session, TranscriptStatus.PROCESSING)
    r = get_status(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PROCESSING"
    assert body["pollAfterMs"] == 5000
    assert body["machineId"] == "m-1"


def test_a_finished_job_returns_the_full_result_inline(client, session):
    """One poll loop yields everything — no second call needed."""
    add_video(
        session,
        TranscriptStatus.DONE,
        topic_segments=[
            {
                "mainTag": "intro",
                "subTag": "s",
                "start": 0,
                "end": 5,
                "thumbnailPath": None,
                "title": "The intro",
            }
        ],
        transcript_segments=[{"start": 0, "end": 5, "text": "hi"}],
        domain_data={"machine": "Pump", "summary": "S"},
    )
    r = get_status(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "DONE"
    assert body["chunkCount"] == 1
    assert body["chunks"][0]["chunkTitle"] == "The intro"
    assert body["guide"]["machine"] == "Pump"
    assert len(body["transcript"]) == 1


def test_a_failed_job_returns_200_so_the_poll_loop_stops_cleanly(client, session):
    """The status CHECK succeeded; the job did not. Distinguishing these is
    the whole point of putting job state in the body."""
    add_video(session, TranscriptStatus.FAILED)
    r = get_status(client)
    assert r.status_code == 200
    assert r.json()["status"] == "FAILED"
    assert r.json()["error"] == "processing failed"


def test_a_pending_job_is_reported_as_still_running(client, session):
    add_video(session, TranscriptStatus.NONE)
    r = get_status(client)
    assert r.status_code == 200
    assert r.json()["status"] == "NONE"
    assert r.json()["pollAfterMs"] == 5000


def test_status_is_read_only(client, session, enqueued):
    """Polling must never start work — that is /videoExtraction's job."""
    add_video(session, TranscriptStatus.PROCESSING)
    get_status(client)
    get_status(client)
    assert enqueued == []


# ─── auth ─────────────────────────────────────────────────────────────────────


def test_both_endpoints_require_the_service_key(client, monkeypatch, session):
    monkeypatch.setenv("SERVICE_API_KEY", "secret")
    get_settings.cache_clear()
    assert post(client).status_code == 401
    assert get_status(client).status_code == 401


def test_a_valid_key_is_accepted(client, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "secret")
    get_settings.cache_clear()
    r = client.post(
        "/api/v1/videoExtraction", json=BODY, headers={"Authorization": "Bearer secret"}
    )
    assert r.status_code == 202


def test_a_storage_outage_is_reported_legibly_not_as_a_bare_traceback(client, session, monkeypatch):
    """A bad storage key must be distinguishable from a missing blob.

    Both are 500 vs 404 respectively; without this the misconfiguration
    surfaces as an unhandled exception and looks like a code bug.
    """
    from app.api.v1 import video_extraction as ve

    def boom(key, container):
        raise RuntimeError("AuthenticationFailed")

    monkeypatch.setattr(ve, "blob_exists", boom)
    r = post(client)
    assert r.status_code == 500
    assert r.json() == {"error": "Storage unavailable"}
    assert session.query(Video).count() == 0, "no record for a video we never verified"
