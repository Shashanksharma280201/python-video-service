"""Shared shape both storage backends implement.

Ported from youtube-clone/src/lib/storage/types.ts. The facade in
app/storage/__init__.py swaps them without callers noticing.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class UploadTarget:
    # The URL the client PUTs the file bytes to.
    url: str
    # Headers the client MUST send on that PUT. Azure needs x-ms-blob-type;
    # empty for S3.
    headers: dict[str, str] = field(default_factory=dict)


class StorageBackend(Protocol):
    name: str

    def public_url(self, key: str) -> str:
        """Public URL persisted as Video.blob_url (not presigned)."""

    def key_from_url(self, url: str) -> str:
        """Inverse of public_url."""

    def exists(self, key: str, container: str | None = None) -> bool:
        """True when the object is present. Validates an ingest request."""

    def presigned_upload_url(self, key: str, content_type: str) -> UploadTarget: ...

    def presigned_download_url(
        self, key: str, expires_in: int = 6 * 3600, container: str | None = None
    ) -> str: ...

    def upload_file(self, local_path: str, key: str, content_type: str) -> str: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> None: ...
