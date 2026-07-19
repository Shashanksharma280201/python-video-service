"""Service-key gate for /api/v1/*.

Mirrors youtube-clone/src/middleware.ts, minus its same-origin branch — this
service has no browser UI, and the client always sends a Bearer token.

SERVICE_API_KEY may be a comma-separated list so keys can be rotated with no
downtime. If it is unset the gate stays OPEN, which is a dev convenience: set it
in every shared environment.
"""

from fastapi import Header, HTTPException

from app.config import get_settings


async def require_service_key(authorization: str | None = Header(default=None)) -> None:
    keys = get_settings().api_keys
    if not keys:
        return

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if token not in keys:
        raise HTTPException(status_code=401, detail="Unauthorized")
