from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok_without_touching_infra():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ts"].endswith("Z")


def test_health_reports_the_configured_models():
    body = client.get("/api/health").json()
    # The flagship model rejects image_url; vision must stay on the mini variant.
    assert body["models"]["visionModel"] == "gpt-5.4-mini"
    assert body["models"]["transcribeModel"] == "whisper-1"
    assert body["storageBackend"] in {"azure", "s3"}
