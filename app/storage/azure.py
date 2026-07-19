"""Azure Blob Storage backend.

Ported from youtube-clone/src/lib/storage/azure.ts. Clients are built lazily so
this module imports cleanly in an S3-only deployment.
"""

from datetime import UTC, datetime, timedelta

from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)

from app.config import get_settings
from app.storage.base import UploadTarget


class AzureBackend:
    name = "azure"

    # ─── config ───────────────────────────────────────────────────────────────

    @property
    def _account(self) -> str:
        return get_settings().azure_storage_account

    @property
    def _key(self) -> str:
        return get_settings().azure_storage_key

    @property
    def _container(self) -> str:
        return get_settings().azure_storage_container

    @property
    def _endpoint(self) -> str:
        """Overridable for sovereign clouds and Azurite."""
        s = get_settings()
        return (
            s.azure_storage_endpoint or f"https://{s.azure_storage_account}.blob.core.windows.net"
        )

    def _service(self) -> BlobServiceClient:
        return BlobServiceClient(account_url=self._endpoint, credential=self._key)

    def _blob(self, key: str, container: str | None = None):
        return self._service().get_blob_client(container or self._container, key)

    # ─── urls ─────────────────────────────────────────────────────────────────

    def public_url(self, key: str) -> str:
        return f"{self._endpoint}/{self._container}/{key}"

    def key_from_url(self, url: str) -> str:
        return url.replace(f"{self._endpoint}/{self._container}/", "", 1)

    def _sas_url(self, key: str, perms: BlobSasPermissions, expires_in: int, container: str) -> str:
        now = datetime.now(UTC)
        token = generate_blob_sas(
            account_name=self._account,
            container_name=container,
            blob_name=key,
            account_key=self._key,
            permission=perms,
            # A small backdate absorbs clock skew between us and Azure.
            start=now - timedelta(minutes=5),
            expiry=now + timedelta(seconds=expires_in),
        )
        return f"{self._endpoint}/{container}/{key}?{token}"

    def presigned_upload_url(self, key: str, content_type: str) -> UploadTarget:
        # create + write lets the client PUT the whole blob in one shot.
        url = self._sas_url(key, BlobSasPermissions(create=True, write=True), 3600, self._container)
        # Azure rejects a single-PUT block-blob upload without this header.
        return UploadTarget(url=url, headers={"x-ms-blob-type": "BlockBlob"})

    def presigned_download_url(
        self, key: str, expires_in: int = 6 * 3600, container: str | None = None
    ) -> str:
        return self._sas_url(
            key, BlobSasPermissions(read=True), expires_in, container or self._container
        )

    # ─── objects ──────────────────────────────────────────────────────────────

    def exists(self, key: str, container: str | None = None) -> bool:
        return self._blob(key, container).exists()

    def upload_file(self, local_path: str, key: str, content_type: str) -> str:
        with open(local_path, "rb") as fh:
            self._blob(key).upload_blob(
                fh, overwrite=True, content_settings=ContentSettings(content_type=content_type)
            )
        return self.public_url(key)

    def delete(self, key: str) -> None:
        self._blob(key).delete_blob(delete_snapshots="include")

    def delete_prefix(self, prefix: str) -> None:
        container = self._service().get_container_client(self._container)
        for item in container.list_blobs(name_starts_with=prefix):
            container.get_blob_client(item.name).delete_blob(delete_snapshots="include")
