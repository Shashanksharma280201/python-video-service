import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.deps.auth import require_service_key


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_gate_is_open_when_no_key_configured(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "")
    await require_service_key(authorization=None)  # does not raise


async def test_valid_bearer_token_passes(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "abc123")
    await require_service_key(authorization="Bearer abc123")


async def test_comma_separated_keys_support_rotation(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "old-key, new-key")
    await require_service_key(authorization="Bearer old-key")
    await require_service_key(authorization="Bearer new-key")


async def test_missing_or_wrong_token_is_rejected(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "abc123")
    for header in (None, "Bearer wrong", "abc123", "Basic abc123"):
        with pytest.raises(HTTPException) as e:
            await require_service_key(authorization=header)
        assert e.value.status_code == 401


async def test_bearer_prefix_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "abc123")
    await require_service_key(authorization="bearer abc123")
