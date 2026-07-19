"""AWS S3 backend — the fallback when Azure credentials are absent.

Ported from youtube-clone/src/lib/storage/s3.ts.
"""

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings
from app.storage.base import UploadTarget


class S3Backend:
    name = "s3"

    @property
    def _bucket(self) -> str:
        return get_settings().aws_s3_bucket

    def _client(self):
        return boto3.client("s3", region_name=get_settings().aws_region)

    # ─── urls ─────────────────────────────────────────────────────────────────

    def public_url(self, key: str) -> str:
        s = get_settings()
        return f"https://{s.aws_s3_bucket}.s3.{s.aws_region}.amazonaws.com/{key}"

    def key_from_url(self, url: str) -> str:
        s = get_settings()
        return url.replace(f"https://{s.aws_s3_bucket}.s3.{s.aws_region}.amazonaws.com/", "", 1)

    def presigned_upload_url(self, key: str, content_type: str) -> UploadTarget:
        url = self._client().generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=3600,
        )
        # S3 needs no extra headers beyond the Content-Type already signed in.
        return UploadTarget(url=url, headers={})

    def presigned_download_url(
        self, key: str, expires_in: int = 6 * 3600, container: str | None = None
    ) -> str:
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": container or self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    # ─── objects ──────────────────────────────────────────────────────────────

    def exists(self, key: str, container: str | None = None) -> bool:
        try:
            self._client().head_object(Bucket=container or self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def upload_file(self, local_path: str, key: str, content_type: str) -> str:
        self._client().upload_file(
            local_path, self._bucket, key, ExtraArgs={"ContentType": content_type}
        )
        return self.public_url(key)

    def delete(self, key: str) -> None:
        self._client().delete_object(Bucket=self._bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        client = self._client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
