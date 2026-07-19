# Python Video Extraction Service — Design

**Date:** 2026-07-19
**Status:** Approved — Phase 0 in progress
**Source system:** `youtube-clone` (Next.js 14, branch `internal-service`)

## 1. Why

The client requires the video-extraction service in Python. This is a port, not a
redesign: the existing Node service is deployed, tested (45 tests) and validated
end-to-end on Azure. The Python service must be a behavioural twin.

**Hard requirement: the response data structure is identical.** The client
consumes the same JSON. Any drift is a defect, not an improvement.

## 2. Scope

**In scope** — the internal service:

- `POST /api/v1/videoExtraction` — start extraction (async, 202)
- `GET  /api/v1/response-status` — poll status, full result inline on DONE
- `POST /api/v1/upload` — presigned upload URL
- `GET  /api/health` — liveness + active model report
- The full 16-module extraction pipeline
- The durable step-based workflow

**Out of scope** — the React web UI and its supporting routes
(`/videos`, `/videos/[id]/search-chapter`, `/transcript`, `/view`). The existing
Next.js app keeps serving those.

## 3. Decisions

| Decision | Choice | Reason |
|---|---|---|
| Scope | Service only | The UI already works; duplicating it wastes 2-3 weeks |
| Database | Own Neon branch, same schema | Two ORMs writing one table is a real risk |
| Durability | Celery + Redis + `workflow_step` table | No Python equivalent of the `workflow` package; this is boring and operable |
| Fidelity | Exact clone | Enables output diffing against Node to prove correctness |
| Deployment | Same AKS cluster, new hostname | Reuses Blob account and team knowledge |

## 4. Architecture

```
                    ┌──────────────┐
   client ──────►   │  FastAPI     │  ── enqueue ──►  Redis
                    │  (api pod)   │                    │
                    └──────┬───────┘                    │
                           │ read                       ▼
                    ┌──────▼───────────────────┐  ┌──────────────┐
                    │   Neon Postgres          │◄─┤ Celery worker│
                    │   (own branch)           │  │  (worker pod)│
                    └──────────────────────────┘  └──────┬───────┘
                                                          │
                              Azure Blob (shared account) ◄┘
```

The API pod is stateless and never processes video — it validates, enqueues and
reads. The worker pod owns all ffmpeg and OpenAI work and scales independently.

### Module map

| Node | Python |
|---|---|
| `src/lib/pipeline/*.ts` (16 files, 1,572 lines) | `app/pipeline/*.py` |
| `src/workflows/transcribe-video.ts` (354 lines) | `app/worker/tasks.py` + `app/worker/steps.py` |
| `src/app/api/v1/*/route.ts` | `app/api/v1/*.py` |
| `src/middleware.ts` | `app/deps/auth.py` |
| `src/lib/s3.ts`, `src/lib/storage/*` | `app/storage/` |
| `src/lib/videoExtractionResponse.ts` | `app/schemas/extraction.py` + `app/services/extraction_response.py` |
| `prisma/schema.prisma` | `app/models.py` + `alembic/` |

The Node pipeline modules were deliberately written orchestration-agnostic (no
Workflow or Vercel imports), which is what makes the bulk of this mechanical.

## 5. The API contract

Frozen. Reproduced here so drift is detectable by reading, not by running.

### POST /api/v1/videoExtraction

Request: `{ machineId, resourceId, tenantId, videoURL }` — all required, all
non-empty strings.

| Status | When | Body |
|---|---|---|
| 200 | Already DONE | Full extraction response |
| 202 | Started, or still running | `{ resourceId, machineId, tenantId, status, chunks: [], chunkCount: 0 }` |
| 400 | Missing fields, or `videoURL` not on the configured storage account | `{ error }` |
| 404 | Blob absent from storage | `{ error: "Video file not found in storage" }` |
| 409 | Processing failed | `{ resourceId, status: "FAILED", error: "processing failed" }` |

Idempotent per `resourceId`: an existing resource is never reprocessed. A race
between two callers is resolved by the `external_id` unique constraint — the
loser attaches to the winner's run.

### GET /api/v1/response-status?resourceId=…

HTTP status answers "did the check work", **not** the job state. Callers read
`body.status`, never the HTTP code.

| Status | When | Body |
|---|---|---|
| 200 | DONE | Full extraction response |
| 200 | PROCESSING / NONE | `{ resourceId, machineId, tenantId, status, pollAfterMs: 5000 }` |
| 200 | FAILED | `{ resourceId, machineId, tenantId, status: "FAILED", error: "processing failed" }` |
| 400 | `resourceId` missing | `{ error: "resourceId is required" }` |
| 404 | Unknown `resourceId` | `{ resourceId, status: "NOT_FOUND" }` |

### The extraction response

Top level: `resourceId, machineId, tenantId, status, title, description,
createdAt, thumbnailUrl, guide, chunks, chunkCount, transcript`

Each chunk: `chunkId, start, end, mainTag, subTag, chunkTitle, transcript,
summarizedText, tools, thumbnailUrl, blobUrl, videoSummary, domainMetaData`

Each transcript segment: `start, end, text, mainTag, subTag`

`guide` is the 12-field `DomainData`: `machine, summary, overview, machineIntro,
preventiveMaintenance, errorCodes, troubleshooting, safety, tools, parts, specs,
glossary`.

All keys are camelCase. Pydantic models use a `to_camel` alias generator so the
Python-side snake_case never leaks into the wire format.

### Auth

`Authorization: Bearer <SERVICE_API_KEY>` on `/api/v1/*`. `SERVICE_API_KEY` may
be a comma-separated list for rotation. If unset, the gate stays open (dev
convenience). `/api/health` is not gated.

The Node version also allows same-origin browser requests; the Python service has
no UI, so that branch is dropped. This is the one intentional behavioural
difference and it cannot affect the client, which always sends a Bearer token.

## 6. Durability

One table the Node service does not have:

```sql
CREATE TABLE workflow_step (
  video_id   TEXT NOT NULL,
  step_key   TEXT NOT NULL,   -- "prepare" | "transcribe:600" | "vision" ...
  output     JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (video_id, step_key)
);
```

A `@step("name")` decorator wraps each pipeline stage: look up
`(video_id, step_key)`, return the cached output if present, otherwise run and
persist. This buys back the crash-resume the `workflow` package provided free.

Fan-out steps key on their offset (`transcribe:600`), so a worker killed at
minute 90 of a four-hour video resumes at minute 90, not zero.

Error mapping: `RetryableError` → Celery `autoretry_for` with `retry_backoff`;
`FatalError` → no retry, mark the video `FAILED`.

## 7. Proving parity

1. **Port pure functions test-first.** The 45 vitest tests become pytest tests
   with identical fixtures and expected values — `parse_chunk_summaries`,
   `parse_storage_url`, `build_extraction_response`, `price_for` (including the
   longest-prefix match that fixed a 17% cost inflation bug).
2. **Golden-file diff.** Run one video through both services; assert the JSON
   matches on everything except `resourceId`, `createdAt` and SAS tokens. A
   complete captured Azure response exists at
   `youtube-clone/API-CHANGES-async-status.md` §4 to diff against.

Because LLM output is not deterministic, the golden diff compares **structure**
— chunk count, field presence, types, timestamp alignment — not exact prose.

## 8. Phases

| Phase | Work | Estimate |
|---|---|---|
| 0 | Repo scaffold, docker-compose, config, health, CI, frozen schemas | 2 days |
| 1 | Models + Alembic, storage facade, auth, `/upload` | 3 days |
| 2 | Pipeline modules ported test-first | 5-7 days |
| 3 | Step decorator + Celery task graph | 5 days |
| 4 | `/videoExtraction`, `/response-status`, response builder | 2 days |
| 5 | Parity run vs Node, fix drift | 3 days |
| 6 | Dockerfile hardening, AKS manifests, ingress, GH Actions | 3 days |

**~4-5 weeks, one developer.** Phases 2 and 3 parallelise across two.

## 9. Risks

1. **Prompt drift.** The LLM prompts must be copied character-for-character or
   outputs diverge. Mitigation: all prompts live in `app/pipeline/prompts.py` as
   literal strings, diffed against the TS source in CI.
2. **ffmpeg range-reads from presigned URLs.** The Node service never downloads
   the full video — ffmpeg seeks slices over HTTP range requests, keeping disk
   use to ~2MB per step regardless of source size. Python shells out to the same
   system binary so this should carry over, but it is the first thing to
   smoke-test.
3. **Non-determinism.** See §7 — structural comparison only.

## 10. Open questions

- Should a bare-bones Azure deploy move earlier than Phase 6 so the client can
  see it running sooner?
- Is there a delivery date that would justify parallelising Phases 2 and 3?
