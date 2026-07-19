# syntax=docker/dockerfile:1

# One image, two roles: the api pod runs uvicorn, the worker pod overrides the
# command to run celery. Same code, same deps — nothing can drift between them.

FROM python:3.12-slim-bookworm AS base
WORKDIR /app

# ffmpeg is the pipeline's workhorse. The system build (not a static one) is
# required because it reads from presigned HTTPS URLs over HTTP range requests —
# that is what keeps multi-GB videos off disk.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Deps first for layer caching — source changes must not reinstall the world.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY app ./app
COPY alembic* ./
RUN uv sync --no-dev

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
