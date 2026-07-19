# python-video-service

Internal video extraction service. Takes a video in Azure Blob, returns
timestamped chapters, a tagged transcript and a structured machine-maintenance
guide.

This is a Python port of the Node/Next.js service. It is a **behavioural twin**:
the response format is identical, because the same client consumes both.

Design: [`docs/specs/2026-07-19-python-video-service-design.md`](docs/specs/2026-07-19-python-video-service-design.md)

## Status

Phase 2 — the full extraction pipeline is ported. All 16 Node modules have a
Python counterpart, built test-first. 324 tests pass. What remains is
orchestration: nothing yet runs the pipeline end to end.

| Phase | Work | State |
|---|---|---|
| 0 | Scaffold, config, health, auth, frozen schemas, CI | done |
| 1 | Models + Alembic, storage facade, `/upload` | done |
| 2 | Pipeline modules (16 files) ported test-first | done |
| 3 | Step decorator + Celery task graph | next |
| 4 | `/videoExtraction`, `/response-status`, response builder | |
| 5 | Parity run against the Node service | |
| 6 | AKS manifests, ingress, deploy pipeline | |

## Guardrails

Three tests exist specifically to catch silent divergence from the Node
service. They are the ones to understand before changing anything.

**`tests/test_prompt_parity.py`** — every prompt must reach the model
character-for-character as Node sends it. The expectations are extracted
programmatically from the TypeScript into `tests/fixtures/node_prompts.json`,
not retyped. A reworded prompt changes model output while every other test
still passes, so this is the only thing standing between the port and a slow,
unexplained quality drift.

**`tests/test_node_parity.py`** — a real captured Azure response (40 chapters,
220 transcript segments) must round-trip through the Pydantic models with zero
key drift.

**`tests/test_media_range_reads.py`** — ffmpeg must read remote video by HTTP
range, not download it whole. This is what keeps a multi-GB source out of the
worker. Measured reads are bounded in absolute terms (~2.6MB to probe, ~10MB
for a slice) regardless of source size.

## Database

```bash
uv run alembic upgrade head      # apply migrations
uv run alembic check             # models vs migrations drift check
```

`DATABASE_URL` drives both the app and Alembic; it is never written to
`alembic.ini`, so credentials stay out of the repo.

Two tables. `video` mirrors the Node service's Prisma schema column-for-column.
`workflow_step` is new — it is the resume ledger described above.

## Architecture

```
                    ┌──────────────┐
   client ──────►   │  FastAPI     │  ── enqueue ──►  Redis
                    │  (api pod)   │                    │
                    └──────┬───────┘                    │
                           │ read                       ▼
                    ┌──────▼───────────────────┐  ┌──────────────┐
                    │   Neon Postgres          │◄─┤ Celery worker│
                    └──────────────────────────┘  └──────┬───────┘
                                                          │
                                    Azure Blob            ◄┘
```

The API pod never processes video — it validates, enqueues and reads. The worker
owns all ffmpeg and OpenAI work and scales independently.

Long jobs survive pod restarts via a `workflow_step` table: each pipeline stage
records its output, and a restarted worker skips completed stages. A worker
killed at minute 90 of a four-hour video resumes at minute 90.

## Local development

```bash
cp .env.example .env      # fill in OPENAI_API_KEY and storage credentials
uv sync                   # uv fetches Python 3.12 if you don't have it
uv run pytest             # tests need no infrastructure
uv run uvicorn app.main:app --reload
```

Full stack with Postgres and Redis:

```bash
docker compose up --build
curl localhost:8000/api/health
```

## API

All `/api/v1/*` routes require `Authorization: Bearer <SERVICE_API_KEY>`.
`SERVICE_API_KEY` accepts a comma-separated list for rotation; when unset the
gate stays open for local development. `/api/health` is never gated.

| Route | Purpose |
|---|---|
| `POST /api/v1/videoExtraction` | Start extraction. Returns 202; poll for the result |
| `GET /api/v1/response-status` | Poll by `resourceId`. Full result inline on DONE |
| `POST /api/v1/upload` | Presigned upload URL |
| `GET /api/health` | Liveness + configured models |

The extraction flow is: upload the file, POST `/videoExtraction`, then poll
`/response-status` until `body.status` is `DONE`. Read `body.status` — never the
HTTP code — to decide whether to keep polling.

Full contract in the design doc, §5.

## Notes for contributors

- **The response shape is not yours to change.** `tests/test_contract.py` fails
  on any rename. If a field genuinely must change, that is a client conversation
  first.
- **Prompts are copied character-for-character** from the Node service. Rewording
  one changes model output and breaks parity.
- **Vision runs on `gpt-5.4-mini`, not the flagship.** The flagship rejects
  `image_url` content. This was verified by A/B probe; it is not an oversight.
