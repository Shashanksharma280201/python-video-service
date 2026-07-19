"""Wire-format base model.

Every response model inherits from this so Python-side snake_case never leaks
into the JSON the client sees. Field names are snake_case; the wire is camelCase.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


def to_node_iso(dt: datetime) -> str:
    """Format a datetime exactly as JavaScript's Date.toJSON() does.

    Node emits `2026-07-06T14:22:10.000Z` — always UTC, always three-digit
    milliseconds, always a literal Z. Pydantic's default differs (offset form,
    variable fractional digits), and the client parses this field, so we pin it.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{dt.microsecond // 1000:03d}Z"
