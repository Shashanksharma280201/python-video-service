"""Storage facade.

Uses Azure Blob when AZURE_STORAGE_* credentials are present, otherwise falls
back to S3. Callers import the same names regardless of which backend is active.

Unlike the Node version the backend is resolved per call rather than at import,
so a test can switch configuration without reimporting the module.
"""

from app.config import get_settings
from app.storage.azure import AzureBackend
from app.storage.base import StorageBackend, UploadTarget
from app.storage.parse_url import (
    ForeignHostError,
    InvalidUrlError,
    StorageUrlError,
    parse_storage_url,
)
from app.storage.s3 import S3Backend

_azure = AzureBackend()
_s3 = S3Backend()


def get_backend() -> StorageBackend:
    return _azure if get_settings().use_azure else _s3


def s3_url(key: str) -> str:
    return get_backend().public_url(key)


def s3_key(url: str) -> str:
    return get_backend().key_from_url(url)


def exists(key: str, container: str | None = None) -> bool:
    return get_backend().exists(key, container)


def presigned_upload_url(key: str, content_type: str) -> UploadTarget:
    return get_backend().presigned_upload_url(key, content_type)


def presigned_download_url(
    key: str, expires_in: int = 6 * 3600, container: str | None = None
) -> str:
    return get_backend().presigned_download_url(key, expires_in, container)


__all__ = [
    "ForeignHostError",
    "InvalidUrlError",
    "StorageBackend",
    "StorageUrlError",
    "UploadTarget",
    "exists",
    "get_backend",
    "parse_storage_url",
    "presigned_download_url",
    "presigned_upload_url",
    "s3_key",
    "s3_url",
]
