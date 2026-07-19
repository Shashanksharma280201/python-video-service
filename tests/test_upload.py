"""POST /api/v1/upload — ported from youtube-clone/src/app/api/v1/upload/route.ts.

Creates a video record and hands back a presigned URL for the client to PUT the
bytes to. No user or ownership: this is an internal service.
"""

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import Base, TranscriptStatus, Video

FAKE_KEY = base64.b64encode(b"0" * 32).decode()


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "stdatadevcentralindia")
    monkeypatch.setenv("AZURE_STORAGE_KEY", FAKE_KEY)
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "videosvc")
    monkeypatch.delenv("AZURE_STORAGE_ENDPOINT", raising=False)
    monkeypatch.setenv("SERVICE_API_KEY", "")
    get_settings.cache_clear()

    # StaticPool + check_same_thread: an in-memory SQLite database lives per
    # CONNECTION, and the endpoint runs on a different thread than the test, so
    # without this the route opens an empty second database.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as s:
        app.dependency_overrides[get_db] = lambda: s
        yield s
        app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def client(session) -> TestClient:
    return TestClient(app)


def post(client, **body):
    return client.post("/api/v1/upload", json=body)


# ─── happy path ───────────────────────────────────────────────────────────────


def test_returns_201_with_id_and_upload_target(client):
    r = post(client, title="Lube Pump", filename="pump.mp4", contentType="video/mp4")
    assert r.status_code == 201
    body = r.json()
    assert set(body.keys()) == {"id", "uploadUrl", "uploadHeaders"}
    assert body["id"]
    assert "pump.mp4" in body["uploadUrl"]


def test_azure_upload_headers_reach_the_client(client):
    """Without x-ms-blob-type the client's PUT fails — it must be told."""
    body = post(client, title="t", filename="a.mp4").json()
    assert body["uploadHeaders"] == {"x-ms-blob-type": "BlockBlob"}


def test_persists_the_video_as_pending(client, session: Session):
    body = post(client, title="Lube Pump", description="desc", filename="pump.mp4").json()
    v = session.get(Video, body["id"])
    assert v is not None
    assert v.title == "Lube Pump"
    assert v.description == "desc"
    assert v.transcript_status == TranscriptStatus.PENDING
    assert v.blob_url.endswith(".mp4")


def test_description_defaults_to_empty(client, session: Session):
    body = post(client, title="t", filename="a.mp4").json()
    assert session.get(Video, body["id"]).description == ""


def test_blob_url_is_the_public_url_not_the_signed_one(client, session: Session):
    """A SAS token stored in the DB would expire and poison every later read."""
    body = post(client, title="t", filename="a.mp4").json()
    v = session.get(Video, body["id"])
    assert "?" not in v.blob_url
    assert "sig=" not in v.blob_url
    assert v.blob_url.startswith("https://stdatadevcentralindia.blob.core.windows.net/videosvc/")


def test_key_is_namespaced_and_timestamped(client, session: Session):
    body = post(client, title="t", filename="a.mp4").json()
    v = session.get(Video, body["id"])
    key = v.blob_url.split("/videosvc/", 1)[1]
    assert key.startswith("videos/")
    assert key.endswith("-a.mp4")


def test_whitespace_in_filenames_is_replaced(client, session: Session):
    body = post(client, title="t", filename="my long name.mp4").json()
    v = session.get(Video, body["id"])
    assert " " not in v.blob_url
    assert v.blob_url.endswith("-my-long-name.mp4")


def test_two_uploads_of_the_same_filename_get_distinct_keys(client, session: Session):
    a = post(client, title="t", filename="a.mp4").json()["id"]
    b = post(client, title="t", filename="a.mp4").json()["id"]
    urls = {session.get(Video, a).blob_url, session.get(Video, b).blob_url}
    assert len(urls) == 2


def test_uploads_have_no_external_id_until_an_extraction_claims_them(client, session: Session):
    body = post(client, title="t", filename="a.mp4").json()
    assert session.get(Video, body["id"]).external_id is None


# ─── validation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"filename": "a.mp4"},
        {"title": "t"},
        {"title": "", "filename": "a.mp4"},
        {"title": "t", "filename": ""},
        {},
    ],
)
def test_title_and_filename_are_required(client, body):
    r = client.post("/api/v1/upload", json=body)
    assert r.status_code == 400
    assert r.json() == {"error": "Title and filename are required"}


def test_no_record_is_created_when_validation_fails(client, session: Session):
    client.post("/api/v1/upload", json={"title": "t"})
    assert session.scalars(select(Video)).all() == []


# ─── auth ─────────────────────────────────────────────────────────────────────


def test_requires_the_service_key_when_one_is_configured(client, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "secret")
    get_settings.cache_clear()
    r = client.post("/api/v1/upload", json={"title": "t", "filename": "a.mp4"})
    assert r.status_code == 401


def test_accepts_a_valid_service_key(client, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "secret")
    get_settings.cache_clear()
    r = client.post(
        "/api/v1/upload",
        json={"title": "t", "filename": "a.mp4"},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 201
