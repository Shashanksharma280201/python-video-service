"""Database models.

`Video` mirrors youtube-clone/prisma/schema.prisma column-for-column so a row
means the same thing in both services. `WorkflowStep` is new: it is the resume
ledger that replaces the Node `workflow` package's free step durability.

Internal-service build: no users, likes or comments. A video is a standalone
record produced and consumed by the pipeline.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

# JSONB on Postgres, plain JSON elsewhere so tests run on SQLite with no server.
JsonCol = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TranscriptStatus(enum.StrEnum):
    """Values are the literal strings the client reads off the wire."""

    NONE = "NONE"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


def new_id() -> str:
    """Generate a primary key client-side.

    Prisma used cuid(); we use a uuid4 hex. The format is deliberately not
    replicated — the two services own separate databases, so ids never need to
    match. What matters is that the id exists BEFORE the insert, because the
    route enqueues work keyed on video.id.
    """
    return uuid.uuid4().hex


class Video(Base):
    __tablename__ = "video"

    def __init__(self, **kw):
        # Assign the id at construction, not at flush. The ingest route needs
        # video.id to enqueue the job, and depending on flush ordering for that
        # is the kind of coupling that breaks quietly later.
        kw.setdefault("id", new_id())
        super().__init__(**kw)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)

    # Supplied by the ingesting service. Our own id stays the primary key.
    # UNIQUE is load-bearing: it is what makes ingest idempotent when two
    # callers race past the existence check.
    external_id: Mapped[str | None] = mapped_column(String, unique=True, default=None)

    machine_id: Mapped[str | None] = mapped_column(String, default=None)
    tenant_id: Mapped[str | None] = mapped_column(String, default=None)

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    blob_url: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    transcript_status: Mapped[TranscriptStatus] = mapped_column(
        Enum(TranscriptStatus, native_enum=False, length=16),
        nullable=False,
        default=TranscriptStatus.NONE,
    )
    transcript: Mapped[str | None] = mapped_column(Text, default=None)
    transcript_segments: Mapped[list | None] = mapped_column(JsonCol, default=None)
    topic_segments: Mapped[list | None] = mapped_column(JsonCol, default=None)

    # Denormalized first-chapter thumbnail, so a feed query never has to pull
    # the heavy transcript/topic JSON.
    thumbnail_url: Mapped[str | None] = mapped_column(String, default=None)

    # The structured machine-maintenance guide — see schemas.extraction.DomainData.
    domain_data: Mapped[dict | None] = mapped_column(JsonCol, default=None)


class WorkflowStep(Base):
    """One completed pipeline stage and its output.

    Before running a stage the worker looks for its row; if present it reuses the
    stored output instead of redoing the work. That is what lets a worker killed
    at minute 90 of a four-hour video resume at minute 90.

    Fan-out stages carry their offset in the key (`transcribe:600`) so each slice
    resumes independently.
    """

    __tablename__ = "workflow_step"

    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    step_key: Mapped[str] = mapped_column(String, primary_key=True)
    output: Mapped[dict | list] = mapped_column(JsonCol, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
