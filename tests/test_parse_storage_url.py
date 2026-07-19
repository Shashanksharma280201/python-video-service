"""Ported from youtube-clone/tests/parseStorageUrl.test.ts.

Same cases, same expected values. parse_storage_url is the SSRF guard: without
the host check a caller could make the service fetch arbitrary URLs from inside
the cluster, so the rejection cases matter as much as the happy path.
"""

import pytest

from app.config import get_settings
from app.storage.parse_url import ForeignHostError, InvalidUrlError, parse_storage_url


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "stdatadevcentralindia")
    monkeypatch.setenv("AZURE_STORAGE_KEY", "k")
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
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ─── azure ────────────────────────────────────────────────────────────────────


def test_splits_container_and_key_from_a_tenant_container_url(azure):
    out = parse_storage_url(
        "https://stdatadevcentralindia.blob.core.windows.net/bpl-x/machine/pump.mp4"
    )
    assert out == ("bpl-x", "machine/pump.mp4")


def test_rejects_a_url_pointing_at_a_different_account(azure):
    with pytest.raises(ForeignHostError):
        parse_storage_url("https://someoneelse.blob.core.windows.net/c/x.mp4")


def test_rejects_a_url_with_no_container_segment(azure):
    with pytest.raises(InvalidUrlError):
        parse_storage_url("https://stdatadevcentralindia.blob.core.windows.net/")


def test_rejects_a_container_with_no_key(azure):
    with pytest.raises(InvalidUrlError):
        parse_storage_url("https://stdatadevcentralindia.blob.core.windows.net/videosvc/")


def test_honours_the_endpoint_override_for_a_local_emulator(monkeypatch, azure):
    monkeypatch.setenv("AZURE_STORAGE_ENDPOINT", "http://azurite:10000/devstoreaccount1")
    get_settings.cache_clear()
    out = parse_storage_url("http://azurite:10000/devstoreaccount1/videosvc/a.mp4")
    assert out == ("videosvc", "a.mp4")


def test_rejects_a_malformed_url(azure):
    with pytest.raises(InvalidUrlError):
        parse_storage_url("not a url")


def test_keeps_nested_key_paths_intact(azure):
    out = parse_storage_url(
        "https://stdatadevcentralindia.blob.core.windows.net/videosvc/a/b/c/d.mp4"
    )
    assert out == ("videosvc", "a/b/c/d.mp4")


# ─── s3 ───────────────────────────────────────────────────────────────────────


def test_reads_bucket_and_key_from_a_virtual_hosted_s3_url(s3):
    out = parse_storage_url("https://video-testing.s3.ap-south-1.amazonaws.com/videos/a.mp4")
    assert out == ("video-testing", "videos/a.mp4")


def test_rejects_a_foreign_s3_bucket_host(s3):
    with pytest.raises(ForeignHostError):
        parse_storage_url("https://other-bucket.s3.ap-south-1.amazonaws.com/x.mp4")


def test_rejects_an_s3_url_with_an_empty_path(s3):
    with pytest.raises(InvalidUrlError):
        parse_storage_url("https://video-testing.s3.ap-south-1.amazonaws.com/")
