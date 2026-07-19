"""Storage facade behaviour.

Ported from youtube-clone/src/lib/storage/{azure,s3}.ts and s3.ts.

Network calls are not exercised here — URL construction, backend selection and
the PUT headers are, because those are what the client and the pipeline depend
on and they are pure functions of configuration.
"""

import base64

import pytest

from app.config import get_settings
from app.storage import get_backend, s3_key, s3_url

FAKE_KEY = base64.b64encode(b"0" * 32).decode()


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "stdatadevcentralindia")
    monkeypatch.setenv("AZURE_STORAGE_KEY", FAKE_KEY)
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "videosvc")
    monkeypatch.delenv("AZURE_STORAGE_ENDPOINT", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def s3(monkeypatch):
    for k in ("AZURE_STORAGE_ACCOUNT", "AZURE_STORAGE_KEY", "AZURE_STORAGE_CONTAINER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_S3_BUCKET", "video-testing")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    # Dummy credentials, deliberately. Without these boto3 falls through to the
    # developer's ~/.aws/credentials and the suite passes locally but fails in CI.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ─── backend selection ────────────────────────────────────────────────────────


def test_azure_is_used_when_all_three_credentials_are_present(azure):
    assert get_backend().name == "azure"


def test_s3_is_the_fallback(s3):
    assert get_backend().name == "s3"


def test_partial_azure_credentials_fall_back_to_s3(monkeypatch, s3):
    """A half-configured Azure must not silently produce broken URLs."""
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "acct")
    get_settings.cache_clear()
    assert get_backend().name == "s3"


# ─── url construction ─────────────────────────────────────────────────────────


def test_azure_public_url(azure):
    assert (
        s3_url("videos/a.mp4")
        == "https://stdatadevcentralindia.blob.core.windows.net/videosvc/videos/a.mp4"
    )


def test_azure_key_is_the_inverse_of_url(azure):
    assert s3_key(s3_url("videos/a.mp4")) == "videos/a.mp4"


def test_s3_public_url(s3):
    assert (
        s3_url("videos/a.mp4") == "https://video-testing.s3.ap-south-1.amazonaws.com/videos/a.mp4"
    )


def test_s3_key_is_the_inverse_of_url(s3):
    assert s3_key(s3_url("videos/a.mp4")) == "videos/a.mp4"


def test_endpoint_override_is_honoured(monkeypatch, azure):
    monkeypatch.setenv("AZURE_STORAGE_ENDPOINT", "http://azurite:10000/devstoreaccount1")
    get_settings.cache_clear()
    assert s3_url("a.mp4") == "http://azurite:10000/devstoreaccount1/videosvc/a.mp4"


# ─── presigned upload ─────────────────────────────────────────────────────────


def test_azure_upload_requires_the_block_blob_header(azure):
    """A single-PUT block blob fails without this header — the client must send it."""
    target = get_backend().presigned_upload_url("videos/a.mp4", "video/mp4")
    assert target.headers == {"x-ms-blob-type": "BlockBlob"}
    assert target.url.startswith(
        "https://stdatadevcentralindia.blob.core.windows.net/videosvc/videos/a.mp4?"
    )


def test_s3_upload_needs_no_extra_headers(s3):
    target = get_backend().presigned_upload_url("videos/a.mp4", "video/mp4")
    assert target.headers == {}
    assert "videos/a.mp4" in target.url


def test_azure_upload_url_is_signed(azure):
    url = get_backend().presigned_upload_url("videos/a.mp4", "video/mp4").url
    assert "sig=" in url
    assert "se=" in url  # expiry


# ─── presigned download ───────────────────────────────────────────────────────


def test_azure_download_url_is_signed_and_read_only(azure):
    url = get_backend().presigned_download_url("videos/a.mp4")
    assert "sig=" in url
    assert "sp=r" in url


def test_azure_download_can_target_a_non_default_container(azure):
    """Ingested videos may live in a tenant's own container."""
    url = get_backend().presigned_download_url("machine/pump.mp4", container="bpl-x")
    assert url.startswith(
        "https://stdatadevcentralindia.blob.core.windows.net/bpl-x/machine/pump.mp4?"
    )


def test_s3_download_url_is_signed(s3):
    url = get_backend().presigned_download_url("videos/a.mp4")
    assert "X-Amz-Signature=" in url
