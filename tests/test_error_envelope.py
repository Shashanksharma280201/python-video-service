"""Every error must use the same envelope as the Node service.

The client has one error path. If some failures arrive as {"error": "..."} and
others as FastAPI's {"detail": [...]}, that path breaks on exactly the inputs
that are already going wrong — which is the worst time to discover it.

Node's behaviour, which these pin:
  - 401           -> {"error": "Unauthorized"}
  - unparseable body: `await request.json().catch(() => ({}))` treats it as an
    empty object, so it falls through to field validation and returns 400 with
    the SAME message a missing field gives. It is never a 422.
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
from app.models import Base

FAKE_KEY = base64.b64encode(b"0" * 32).decode()
REQUIRED = "machineId, resourceId, tenantId and videoURL are required"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "stdatadevcentralindia")
    monkeypatch.setenv("AZURE_STORAGE_KEY", FAKE_KEY)
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "videosvc")
    monkeypatch.setenv("SERVICE_API_KEY", "")
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as s:
        app.dependency_overrides[get_db] = lambda: s
        yield TestClient(app)
        app.dependency_overrides.clear()
    get_settings.cache_clear()


# ─── 401 ──────────────────────────────────────────────────────────────────────


def test_unauthorized_uses_the_error_key_not_detail(client, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "secret")
    get_settings.cache_clear()
    r = client.post("/api/v1/videoExtraction", json={})
    assert r.status_code == 401
    assert r.json() == {"error": "Unauthorized"}


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/v1/videoExtraction", "post"),
        ("/api/v1/response-status?resourceId=x", "get"),
        ("/api/v1/upload", "post"),
    ],
)
def test_every_gated_route_returns_the_same_401_shape(client, monkeypatch, path, method):
    monkeypatch.setenv("SERVICE_API_KEY", "secret")
    get_settings.cache_clear()
    r = client.post(path, json={}) if method == "post" else client.get(path)
    assert r.status_code == 401
    assert r.json() == {"error": "Unauthorized"}


# ─── malformed bodies ─────────────────────────────────────────────────────────


def test_an_unparseable_body_is_a_400_with_the_field_message(client):
    """Node treats a bad body as {} and falls through to field validation."""
    r = client.post(
        "/api/v1/videoExtraction",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json() == {"error": REQUIRED}


def test_a_missing_body_is_a_400(client):
    r = client.post("/api/v1/videoExtraction")
    assert r.status_code == 400
    assert r.json() == {"error": REQUIRED}


def test_a_json_array_body_is_a_400(client):
    r = client.post("/api/v1/videoExtraction", json=[1, 2, 3])
    assert r.status_code == 400
    assert r.json() == {"error": REQUIRED}


def test_a_json_string_body_is_a_400(client):
    r = client.post("/api/v1/videoExtraction", json="hello")
    assert r.status_code == 400
    assert r.json() == {"error": REQUIRED}


def test_upload_handles_a_malformed_body_the_same_way(client):
    r = client.post(
        "/api/v1/upload", content=b"{oops", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json() == {"error": "Title and filename are required"}


def test_no_response_ever_uses_fastapis_detail_envelope(client, monkeypatch):
    """A blanket check: the client parses `error`, so `detail` must not appear."""
    monkeypatch.setenv("SERVICE_API_KEY", "secret")
    get_settings.cache_clear()
    responses = [
        client.post("/api/v1/videoExtraction", json={}),
        client.post("/api/v1/videoExtraction", content=b"junk"),
        client.get("/api/v1/response-status"),
        client.get("/api/v1/nope"),
        client.post("/api/v1/upload", json={}),
    ]
    for r in responses:
        body = r.json()
        assert "detail" not in body, f"{r.request.url} leaked FastAPI's envelope: {body}"


# ─── unchanged happy paths ────────────────────────────────────────────────────


def test_a_well_formed_request_still_validates_fields_normally(client):
    r = client.post("/api/v1/videoExtraction", json={"machineId": "m"})
    assert r.status_code == 400
    assert r.json() == {"error": REQUIRED}


def test_response_status_still_reports_a_missing_resource_id(client):
    r = client.get("/api/v1/response-status")
    assert r.status_code == 400
    assert r.json() == {"error": "resourceId is required"}
