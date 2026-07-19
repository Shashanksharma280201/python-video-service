"""Database engine and session dependency.

The engine is built lazily on first use so the app imports (and /api/health
answers) even when the database is unreachable.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,  # Neon closes idle connections; revalidate before use
        future=True,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Tests override this with a SQLite-backed session."""
    with get_sessionmaker()() as session:
        yield session
