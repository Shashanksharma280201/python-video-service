# Video Extraction Service — API Integration Guide

**Audience:** the team integrating this service.
**Version:** 1.0 · Last updated 2026-07-20

You give the service a video that already sits in our storage account. It gives
you back timestamped chapters, a tagged transcript, and a structured
machine-maintenance guide.

> **Deployment status:** this service is not on Azure yet. The contract below is
> final and tested, so you can build against it now, but the base URL is
> pending. Until then, the existing Node service at
> `https://video.dev.cin.ambypro.ai` serves the **same contract** — every field
> in this document behaves identically there, so you can integrate against it
> today and switch the base URL later with no code change.

---

## 1. The flow in three steps

```
1. Upload the video to blob storage        (POST /api/v1/upload, then PUT)
2. Start extraction                        (POST /api/v1/videoExtraction  -> 202)
3. Poll until it finishes                  (GET  /api/v1/response-status  -> DONE)
```

Extraction is **asynchronous**. Step 2 returns immediately — it never holds the
connection open, because a long video can take much longer than any HTTP
timeout allows. You learn it finished by polling step 3.

**Timing:** roughly **12 seconds of processing per minute of video**. A 15-minute
video took 177 seconds end to end. Poll every 5 seconds.

If your video is already in the storage account, skip step 1 entirely.

---

## 2. Authentication

Every `/api/v1/*` request needs a bearer token:

```
Authorization: Bearer <SERVICE_API_KEY>
```

Ask the platform team for the key. It accepts a comma-separated list on the
server side, so keys can be rotated without downtime — if you are told to switch
keys, both old and new will work during the overlap.

`GET /api/health` is not authenticated.

A missing or wrong key returns **401** with `{"error": "Unauthorized"}`.

**Every error, without exception, has the shape `{"error": "<message>"}`.** You
only need one error-handling path. There is no nested `detail` object and no
422 — a malformed body is reported as a 400 naming the missing fields.

---

## 3. Endpoints

### 3.1 `POST /api/v1/upload`

Returns a presigned URL to PUT the file to. Use this only if the video is not
already in storage.

**Request**

```json
{
  "title": "Hydraulic B-axis fault",
  "description": "optional",
  "filename": "pump-repair.mp4",
  "contentType": "video/mp4"
}
```

`title` and `filename` are required.

**Response — 201**

```json
{
  "id": "59114c0e45cf4056ba60b5849fda88b7",
  "uploadUrl": "https://stdatadevcentralindia.blob.core.windows.net/videosvc/videos/1784287840911-pump-repair.mp4?<sas>",
  "uploadHeaders": { "x-ms-blob-type": "BlockBlob" }
}
```

Then PUT the bytes to `uploadUrl`, **sending every header in `uploadHeaders`**:

```bash
curl -X PUT "$uploadUrl" \
  -H "x-ms-blob-type: BlockBlob" \
  -H "Content-Type: video/mp4" \
  --data-binary @pump-repair.mp4
```

> Azure rejects a single-PUT block-blob upload without `x-ms-blob-type`. Read
> the headers from the response rather than hardcoding them — they differ by
> storage backend.

The blob URL to use in the next step is `uploadUrl` **with the `?<sas>` query
string removed**.

| Status | Meaning |
|---|---|
| 201 | Created |
| 400 | `title` or `filename` missing |
| 401 | Bad or missing key |
| 500 | Upload could not be prepared |

---

### 3.2 `POST /api/v1/videoExtraction`

Starts extraction.

**Request** — all four fields required, all non-empty strings:

```json
{
  "machineId":  "machine-42",
  "resourceId": "your-unique-job-id",
  "tenantId":   "tenant-7",
  "videoURL":   "https://stdatadevcentralindia.blob.core.windows.net/videosvc/videos/pump-repair.mp4"
}
```

| Field | Notes |
|---|---|
| `resourceId` | **Your** id. This is the idempotency key and how you poll later. Must be unique per video. |
| `videoURL` | Must point at the configured storage account. Any other host is rejected. Do NOT include a SAS token. |
| `machineId`, `tenantId` | Stored and echoed back. Not interpreted. |

**Responses**

| Status | When | Body |
|---|---|---|
| **202** | Started, or already running | `{ resourceId, machineId, tenantId, status: "PROCESSING", chunks: [], chunkCount: 0 }` |
| **200** | This `resourceId` already finished | The full result (section 4) |
| **400** | Missing field, or `videoURL` not on our storage account | `{ "error": "..." }` |
| **404** | No such blob | `{ "error": "Video file not found in storage" }` |
| **409** | This `resourceId` previously failed | `{ resourceId, status: "FAILED", error: "processing failed" }` |
| **500** | Could not start processing / storage unreachable | `{ "error": "..." }` |

**Idempotency — important.** Calling again with the same `resourceId` never
reprocesses the video. It returns the current state instead. This is safe to
retry: if your request times out, just send it again. Two callers racing with
the same `resourceId` will both attach to the same single run.

To reprocess a video, use a **new** `resourceId`.

---

### 3.3 `GET /api/v1/response-status?resourceId=<id>`

Poll this until the job finishes.

**Read `body.status`, never the HTTP status code.**

The HTTP code answers "did the status check work", not "is the job done". A
successful check of a failed job is `200` with `status: "FAILED"` — treating
the HTTP code as job state will make you mark failed jobs as succeeded.

| HTTP | `body.status` | Meaning | What to do |
|---|---|---|---|
| 200 | `PROCESSING` / `NONE` | Still working | Wait `pollAfterMs` and poll again |
| 200 | `DONE` | Finished | Full result is in this same body |
| 200 | `FAILED` | Processing failed | Stop polling. Report it |
| 404 | `NOT_FOUND` | Unknown `resourceId` | Stop. You never started this job |
| 400 | — | `resourceId` missing | Fix the request |

**Still running:**

```json
{
  "resourceId": "your-unique-job-id",
  "machineId": "machine-42",
  "tenantId": "tenant-7",
  "status": "PROCESSING",
  "pollAfterMs": 5000
}
```

**Done** — the complete result is returned inline, so one polling loop gets you
everything. There is no second "fetch result" call.

---

### 3.4 `GET /api/health`

Unauthenticated. For liveness probes.

```json
{
  "status": "ok",
  "ts": "2026-07-20T00:31:54.123Z",
  "models": {
    "chatModel": "gpt-5.4",
    "chatModelMini": "gpt-5.4-mini",
    "visionModel": "gpt-5.4-mini",
    "transcribeModel": "whisper-1"
  },
  "storageBackend": "azure"
}
```

---

## 4. The result

Returned by `/response-status` on `DONE`, and by `/videoExtraction` when the
`resourceId` has already finished.

### Top level

| Field | Type | Notes |
|---|---|---|
| `resourceId` | string | The id you supplied |
| `machineId` | string \| null | Echoed back |
| `tenantId` | string \| null | Echoed back |
| `status` | string | `DONE` here |
| `title` | string | Derived from the filename |
| `description` | string | Usually `""` |
| `createdAt` | string | ISO 8601 UTC, e.g. `2026-07-20T00:31:54.123Z` |
| `thumbnailUrl` | string \| null | Presigned. First chapter's frame |
| `guide` | object | Machine-maintenance guide — section 4.3 |
| `chunks` | array | Chapters — section 4.1 |
| `chunkCount` | number | `chunks.length` |
| `transcript` | array | Full timestamped transcript — section 4.2 |

### 4.1 `chunks` — the chapters

A chapter is a contiguous stretch of the video about one thing. A 15-minute
video typically yields ~40. **Chapters and chunks are the same thing** — the
field is named `chunks` for historical reasons.

Real example:

```json
{
  "chunkId": "59114c0e45cf4056ba60b5849fda88b7-0",
  "start": 0.0,
  "end": 14.28,
  "mainTag": "compare",
  "subTag": "Book value versus setting",
  "chunkTitle": "Book value mismatch",
  "transcript": "1,000, but it's 1,300 in the book, but any time we mess this on here...",
  "summarizedText": "They notice the value on the machine should be set back to 1,300 instead of 1,000.",
  "tools": [],
  "thumbnailUrl": "https://.../thumbnails/<videoId>/segment-0.jpg?<sas>",
  "blobUrl": "https://.../videos/pump-repair.mp4?<sas>",
  "videoSummary": "This video works through a hydraulic motion problem on a KUKA-controlled machine...",
  "domainMetaData": { "machine": "...", "summary": "...", "overview": "...", "machineIntro": [] }
}
```

| Field | Type | Notes |
|---|---|---|
| `chunkId` | string | `<videoId>-<index>`. **Not stable across reprocessing** — see the warning below |
| `start`, `end` | number | Seconds from the start of the video |
| `mainTag` | string | Phase label, lowercase (`intro`, `diagnosis`, `repair`, …). Derived per video |
| `subTag` | string | Short description of this specific moment |
| `chunkTitle` | string | Short LLM-written label, 3-6 words |
| `transcript` | string | Flat text spoken during this chapter |
| `summarizedText` | string | One-sentence summary |
| `tools` | string[] | Physical tools named in this chapter. Often `[]` |
| `thumbnailUrl` | string \| null | Presigned frame at `start`. Null if extraction failed |
| `blobUrl` | string | Presigned source video. Same on every chunk |
| `videoSummary` | string | Video-level summary. Same on every chunk |
| `domainMetaData` | object | 4-field subset of `guide`. Kept for backward compatibility — **prefer top-level `guide`** |

> **Do not store `chunkId` as a durable reference.** It is index-based, so
> reprocessing the same video can assign the same id to a different moment. If
> you need to point at a moment, store `start` / `end`.

### 4.2 `transcript` — full timestamped transcript

Finer-grained than the per-chunk text: this is the transcript-view data.

```json
{
  "start": 0.0,
  "end": 9.28,
  "text": "1,000, but it's 1,300 in the book, but any time we mess this on here, let's get that",
  "mainTag": "compare",
  "subTag": "Book value versus setting"
}
```

Each segment carries the phase of the chapter it sits inside, so the transcript
view and the chapter list always agree. A 15-minute video yields ~220 segments.

### 4.3 `guide` — the machine-maintenance guide

The highest-value output: a self-service debugging guide built from the video.
Twelve fields, **always present** — empty sections are `[]` or `""`, never
`null`, so you can index them without guarding.

| Field | Type | Contents |
|---|---|---|
| `machine` | string | Name of the machine, inferred |
| `summary` | string | 2-3 sentences: the machine and what this video solves |
| `overview` | string | Narrative of how the machine works |
| `machineIntro` | GuideItem[] | Component-by-component explanation |
| `preventiveMaintenance` | Procedure[] | Routine tasks |
| `errorCodes` | DebugItem[] | Faults with a code (e.g. `E-041`) |
| `troubleshooting` | DebugItem[] | Problems without a code |
| `safety` | GuideItem[] | Hazards and precautions |
| `tools` | string[] | All tools mentioned |
| `parts` | string[] | All parts mentioned |
| `specs` | SpecItem[] | `{ label, value, start }` — torque, pressure, capacity |
| `glossary` | GlossaryTerm[] | `{ term, definition }` |

**DebugItem** — the core structure, one guided fix:

```json
{
  "code": "",
  "title": "Book value and machine setting do not match",
  "symptom": "You notice a setting is at 1,000 on the machine, but the book says 1,300.",
  "story": "Start here, because mismatched settings can send you chasing a hydraulic problem...",
  "fix": [
    {
      "text": "Identify the setting that is at 1,000 and compare it to the reference that says 1,300.",
      "expected": "You confirm there is a mismatch between the live setting and the reference value.",
      "visual": "the black rotary setting knob near the upper-right of the panel",
      "start": 0.0
    }
  ],
  "verify": "...",
  "ifNotResolved": "...",
  "tools": [],
  "difficulty": "Easy",
  "time": "~10 min",
  "start": 0.0
}
```

| Field | Notes |
|---|---|
| `code` | Error code, or `""` for a plain problem |
| `symptom` | One line, for fast matching against what the technician sees |
| `story` | 1-3 paragraphs teaching what is wrong and why |
| `fix` | Ordered steps. Each has `text`, `expected`, `visual`, `start` |
| `fix[].visual` | Where the part is **on screen**, from vision. May be `""` |
| `fix[].start` | Seconds — seek the player here. May be `null` |
| `difficulty` | `Easy` \| `Medium` \| `Hard` \| `""` |
| `time` | e.g. `~30 min`, or `""` |

**Procedure** — `{ title, detail, steps: Step[], tools, difficulty, time, start }`
**GuideItem** — `{ title, detail, steps: string[], start }`
**Step** — `{ text, expected, visual, start }`

> Everything in `guide` is derived **only from the video**. The model is
> instructed never to invent codes, specs or numbers. Sections the video does
> not cover come back empty — an empty `errorCodes` means the video mentioned
> none, not that extraction failed.

---

## 5. Presigned URLs expire

| URL | Valid for |
|---|---|
| `uploadUrl` (from `/upload`) | **1 hour** |
| `thumbnailUrl`, `blobUrl` (in the result) | **6 hours** |

Do not cache them, embed them in stored records, or hand them to a client that
may use them later. If you need a URL after expiry, poll `/response-status`
again — it returns freshly signed URLs every time.

Start the upload PUT promptly after calling `/upload`; a large file queued
behind an hour of other work will fail on an expired signature.

---

## 6. Complete example

```bash
BASE="https://<TBD>"          # ask the platform team
KEY="<SERVICE_API_KEY>"
BLOB="https://stdatadevcentralindia.blob.core.windows.net/videosvc/videos/pump-repair.mp4"

# start
curl -s -X POST "$BASE/api/v1/videoExtraction" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"machineId\":\"machine-42\",\"resourceId\":\"job-001\",\"tenantId\":\"tenant-7\",\"videoURL\":\"$BLOB\"}"
# -> 202 {"resourceId":"job-001", ... "status":"PROCESSING", ...}

# poll
while true; do
  BODY=$(curl -s "$BASE/api/v1/response-status?resourceId=job-001" -H "Authorization: Bearer $KEY")
  STATUS=$(echo "$BODY" | jq -r .status)
  echo "status: $STATUS"
  case "$STATUS" in
    DONE)      echo "$BODY" > result.json; break ;;
    FAILED)    echo "processing failed"; exit 1 ;;
    NOT_FOUND) echo "unknown resourceId"; exit 1 ;;
  esac
  sleep 5
done

jq '.chunkCount, .chunks[0].chunkTitle, .guide.machine' result.json
```

**Python:**

```python
import time, requests

BASE = "https://<TBD>"
HEADERS = {"Authorization": f"Bearer {KEY}"}

def extract(resource_id: str, video_url: str, machine_id: str, tenant_id: str) -> dict:
    r = requests.post(
        f"{BASE}/api/v1/videoExtraction",
        headers=HEADERS,
        json={"machineId": machine_id, "resourceId": resource_id,
              "tenantId": tenant_id, "videoURL": video_url},
        timeout=30,
    )
    if r.status_code == 409:
        raise RuntimeError(f"{resource_id} previously failed; use a new resourceId")
    r.raise_for_status()
    if r.status_code == 200:
        return r.json()                      # already finished

    while True:
        s = requests.get(
            f"{BASE}/api/v1/response-status",
            headers=HEADERS, params={"resourceId": resource_id}, timeout=30,
        )
        if s.status_code == 404:
            raise RuntimeError(f"unknown resourceId {resource_id}")
        s.raise_for_status()
        body = s.json()

        # Read body.status, NOT the HTTP code.
        if body["status"] == "DONE":
            return body
        if body["status"] == "FAILED":
            raise RuntimeError(f"processing failed for {resource_id}")

        time.sleep(body.get("pollAfterMs", 5000) / 1000)
```

---

## 7. Integration checklist

- [ ] Read `body.status`, never the HTTP status code, to decide whether to keep polling
- [ ] Handle `FAILED` — it arrives as **200**, not an error code
- [ ] Use a unique `resourceId` per video; reuse is a no-op, not a reprocess
- [ ] Retry a timed-out `/videoExtraction` freely — it is idempotent
- [ ] Send every header from `uploadHeaders` on the PUT
- [ ] Strip the SAS query string before passing a blob URL to `/videoExtraction`
- [ ] Do not persist `chunkId`, `thumbnailUrl` or `blobUrl` — use `start`/`end` and re-poll for URLs
- [ ] Treat empty guide sections as "the video did not cover this", not as an error
- [ ] Budget ~12s of processing per minute of video; poll every 5s

---

## 8. Reference values from a real run

A 15-minute (900s) maintenance video:

| | |
|---|---|
| chapters (`chunkCount`) | 40 |
| transcript segments | 220 |
| chapters with a thumbnail | 40 / 40 |
| response size | ~250 KB |
| processing time | 177s |
| guide sections populated | 7 troubleshooting, 4 safety, 4 machineIntro, 11 specs, 12 glossary |

Expect the response to be a few hundred KB. It is one JSON document with no
pagination.
