# Phase 5 — Parity run against the Node service

**Date:** 2026-07-20
**Video:** the same 15-minute clip the Node service processed as
`azure-newvid-001` (`videos/1784287840911-newvid-15min.mp4`, 121 MB, 15:00.00,
1920x1080, 16 kHz mono audio)
**Verdict: structural parity PASS.**

## What was compared

The Python service's live response against
`tests/fixtures/node_extraction_response.json` — a real DONE response captured
from the deployed Node service on Azure.

Comparison is **structural**, not textual. LLM output is not deterministic, so
matching prose would be a test of luck, not correctness. What must match is the
shape the client depends on.

## Result

| | python | node | |
|---|---|---|---|
| top-level key set | | | identical |
| chunk key set | | | identical |
| transcript key set | | | identical |
| guide key set | | | identical |
| chunks | 40 | 40 | = |
| chunkCount | 40 | 40 | = |
| transcript segments | 220 | 220 | = |
| chunkTitles populated | 40/40 | 40/40 | = |
| thumbnails present | 40/40 | 40/40 | = |
| chapters span | 0.0s → 900.0s | 0.0s → 900.0s | = |
| transcript span | 0.0s → 900.0s | 0.0s → 900.0s | = |

Frozen-contract validation (`ExtractionResponse.model_validate`): **PASS**.

Guide section counts differ, as expected from a second LLM pass:

| section | python | node |
|---|---|---|
| troubleshooting | 7 | 5 |
| safety | 4 | 3 |
| machineIntro | 4 | 5 |
| tools | 4 | 9 |
| parts | 8 | 11 |
| specs | 11 | 8 |
| glossary | 12 | 10 |
| errorCodes | 0 | 0 |
| preventiveMaintenance | 0 | 0 |

## Agreement on content

Chapter boundaries agree exactly for the first six chapters and 9/40 overall;
transcript segment starts agree within 0.5s for 193/220 (88%). Both services
independently identified the same moments early on:

| start | python | node |
|---|---|---|
| 0.0s | Book value mismatch | Book Value Mismatch |
| 14.3s | Right light reset | Right Light Reset |
| 32.0s | Teach pendant reset | Robot Arm Reset |
| 51.2s | Alarm test clears | Alarm Test Clears |
| 68.1s | Preparing to bleed | Try Moving Again |
| 86.6s | Adjusting set points | Jog Wheel Progress |

Later chapters diverge more, which is expected: boundaries come from LLM
tagging, so a different pass groups segments differently.

One difference worth noting in Python's favour: `guide.machine` came back as
"KUKA robot cell with a hydraulic B-axis/clamp system", where the Node run left
it empty.

## Cost and timing

```
GPT chat calls   : 50
prompt tokens    : 147,904
completion tokens:  11,811
Whisper calls    : 2  (15.00 min audio)
ESTIMATED COST   : $0.205543
wall clock       : 177s
```

Per pipeline step: 30 vision-locate, 10 tag-segments, 5 vision-batch,
2 chunk-summary, 1 each of analyze-video, reassign-other and domain-guide.

Two details this run confirmed that no unit test could:

- **Vision works.** 35 vision calls succeeded on `gpt-5.4-mini`. The flagship
  rejects `image_url`; routing vision to it is the bug this pins down.
- **The longest-prefix pricing fix matters in production.** The API returned
  DATED model ids — `gpt-5.4-mini-2026-03-17` and `gpt-5.4-2026-03-05`. Without
  longest-prefix matching, every one of those 49 mini calls would have been
  billed at flagship rates.

## Deviation from plan: S3, not Azure

The run used **S3** for storage rather than Azure Blob. This was an access
limit, not a choice: the signed-in Azure account lacks
`Microsoft.Storage/storageAccounts/listKeys/action`, so the storage account key
could not be retrieved, and the storage backend authenticates with a shared key.

AAD data-plane access WAS available, so the exact same source video was
downloaded from Azure and uploaded to S3 for the run. Everything that matters
was held constant: same video, same OpenAI key, same models, same prompts, same
chunk size.

What this leaves unverified is the Azure backend specifically — SAS generation
and blob reads — under a real workload. That code is unit-tested and is a direct
port, but it has not been exercised end to end. **It should be the first thing
checked in Phase 6**, when the service runs on AKS with the real account key.

## Cleanup

The uploaded video, its 40 thumbnails, the local copy of the source video, and
both throwaway containers were all removed. Nothing was written to the Node
service's database — the run used its own local Postgres.
