# Phase 1 — working demo

Spec §7 Phase 1: single provider, hardcoded catalog, upload only, FastAPI job queue,
raw result screen. Goal is an honest end-to-end render, not polish.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --reload      # http://localhost:8000
.venv/bin/python test_app.py            # self-check, no API calls, no cost
```

Drop garment images into `catalog/tops/`, `catalog/bottoms/`, `catalog/one-pieces/` —
the folder name is the FASHN category and the filename is the display name. That is the
entire admin UI for now. Curate them from whatever the spike's grid shows works.

## Endpoints

| | |
|---|---|
| `POST /person` | multipart upload → `{person_id, image}`. Resized to 1024px long edge (App. B-1) and pushed to fal immediately so the transfer overlaps catalog browsing (App. B-2). |
| `GET /catalog` | derived from the `catalog/` folders |
| `POST /tryon` | `{person_id, garment_id, provider?, seed?}` → `{job_id}`. Cache hit returns instantly (§3). |
| `GET /jobs/{id}` | `queued → generating → done \| failed` |
| `GET /stats` | generations, spend, avg latency, failures — the §8-2 unit-economics input |

`TRYON_PROVIDER=fashn` switches the default. Per-request `provider` A/Bs both on the same
garment during a pitch, which is the whole reason §3 wants the interface on day one.

## What Phase 1 deliberately does not have

Webcam, pose overlay, quality gate, multi-garment chaining, retry/fallback, face
composite-back, upscaling, QR share, consent screen, admin upload — all Phase 2/3.

## Deviations from the §3 architecture, and when to undo them

Phase 1 runs on one box with one worker. These are the single-box equivalents; each is a
contained swap, not a rewrite.

| §3 says | Phase 1 uses | Swap when |
|---|---|---|
| Redis + ARQ worker pool | in-process asyncio tasks | you need multi-worker, or jobs to survive a restart |
| Postgres | sqlite (`data/app.db`) | multi-brand tenancy (Phase 3) |
| Cloudflare R2, presigned PUT | local `data/`, TTL sweep | you deploy to more than one box |
| Next.js PWA + MediaPipe | one static HTML file | the webcam capture screen lands (Phase 2) — MediaPipe is what pulls Next.js in |
| SSE job stream | 800ms polling | the generating screen grows real staged progress (§6) |

fal's queue API is already a durable queue, so Redis in Phase 1 would be a queue in front
of a queue. Say the word if you want the full infra now anyway.

**Kept, not simplified:** the §3 provider interface, `(person, garment, provider, seed)`
caching, per-generation cost logging, and the 24h TTL sweep on person photos and renders
(§8-7 — they are biometric-adjacent).
