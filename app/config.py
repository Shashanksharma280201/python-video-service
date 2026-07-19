"""Environment-driven settings.

Mirrors the env var names of the Node service so a single .env can drive both
during the parity phase. Nothing here reaches out to a network at import time —
the API pod must boot even when Neon or Blob is briefly unavailable.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/videoservice"
    redis_url: str = "redis://localhost:6379/0"

    # Comma-separated list. Empty means the gate stays open (dev convenience).
    service_api_key: str = ""

    openai_api_key: str = ""
    chat_model: str = "gpt-5.4"
    chat_model_mini: str = "gpt-5.4-mini"
    # The flagship model rejects image_url content; the mini variant accepts it.
    # Verified by A/B probe against the live API — do not "simplify" to chat_model.
    vision_model: str = "gpt-5.4-mini"
    transcribe_model: str = "whisper-1"

    azure_storage_account: str = ""
    azure_storage_key: str = ""
    azure_storage_container: str = ""
    azure_storage_endpoint: str = ""

    aws_s3_bucket: str = ""
    aws_region: str = ""

    chunk_minutes: int = 10

    @property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.service_api_key.split(",") if k.strip()]

    @property
    def use_azure(self) -> bool:
        return bool(
            self.azure_storage_account and self.azure_storage_key and self.azure_storage_container
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
