"""Split a stored blob/object URL into (container, key).

Ported from youtube-clone/src/lib/storage/parseUrl.ts.

The host check is the SSRF guard. Without it a caller could hand us any URL and
make the service fetch it from inside the cluster, so a URL that does not point
at our own storage account is refused before anything reads from it.
"""

from urllib.parse import urlparse

from app.config import get_settings


class StorageUrlError(Exception):
    """Base for URL rejections."""


class InvalidUrlError(StorageUrlError):
    """Not a parseable URL, or missing a container/key segment. ('bad-url')"""


class ForeignHostError(StorageUrlError):
    """Points at a host that is not our configured storage. ('foreign-host')"""


def _expected_host() -> str:
    s = get_settings()
    if s.use_azure:
        if s.azure_storage_endpoint:
            return urlparse(s.azure_storage_endpoint).netloc
        return f"{s.azure_storage_account}.blob.core.windows.net"
    return f"{s.aws_s3_bucket}.s3.{s.aws_region}.amazonaws.com"


def parse_storage_url(url: str) -> tuple[str, str]:
    """Return (container, key). Raises InvalidUrlError / ForeignHostError."""
    s = get_settings()

    parsed = urlparse(url)
    # urlparse is lenient where JS's URL constructor throws — demand both parts.
    if not parsed.scheme or not parsed.netloc:
        raise InvalidUrlError("bad-url")

    if parsed.netloc != _expected_host():
        raise ForeignHostError("foreign-host")

    path = parsed.path.lstrip("/")

    if s.use_azure:
        # With an endpoint override (Azurite: .../devstoreaccount1) the account
        # name is the FIRST path segment, so the container is the second. Strip
        # the configured prefix to normalize both forms.
        if s.azure_storage_endpoint:
            ep_path = urlparse(s.azure_storage_endpoint).path.strip("/")
            if ep_path and path.startswith(ep_path + "/"):
                path = path[len(ep_path) + 1 :]

        container, sep, key = path.partition("/")
        if not container or not sep or not key:
            raise InvalidUrlError("bad-url")
        return container, key

    # S3 virtual-hosted-style URLs carry the bucket in the host, so the whole
    # path is the key.
    if not path:
        raise InvalidUrlError("bad-url")
    return s.aws_s3_bucket, path
