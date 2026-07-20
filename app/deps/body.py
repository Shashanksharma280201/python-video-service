"""Lenient JSON body parsing.

Mirrors the Node routes, which do `await request.json().catch(() => ({}))` — an
unparseable body becomes an empty object and falls through to the route's own
field validation, producing a 400 with a message naming the missing fields.

FastAPI's default is a 422 carrying its own `detail` envelope. That is a better
generic API, but it is not the contract this client already integrates against,
and a caller sending a slightly wrong body would get a shape it cannot parse.
"""

from typing import Any

from fastapi import Request


async def json_body(request: Request) -> dict[str, Any]:
    """The request body as a dict. Anything unparseable becomes {}."""
    try:
        parsed = await request.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
