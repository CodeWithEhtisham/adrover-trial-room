# AdRover — AI Virtual Try-On
## Prototype / MVP Technical Specification

**Version:** 1.0
**Date:** 18 September 2026
**Status:** Pre-development — for internal review and agency presentation

---

## Table of Contents

1. [Product Concept](#1-product-concept)
2. [Prototype Scope](#2-prototype-scope)
3. [Technical Architecture](#3-technical-architecture)
4. [AI Model Options](#4-ai-model-options)
5. [User Flow and Edge Cases](#5-user-flow-and-edge-cases)
6. [Prototype UI/UX](#6-prototype-uiux)
7. [Implementation Plan](#7-implementation-plan)
8. [Technical Challenges](#8-technical-challenges)
9. [Customer Demo Strategy](#9-customer-demo-strategy)
10. [Final Recommendation](#10-final-recommendation)
11. [Appendix A — fal.ai Integration Reference](#appendix-a--falai-integration-reference)
12. [Appendix B — Latency Budget](#appendix-b--latency-budget)

---

## 1. Product Concept

### The honest reframing

This is **a photo booth, not a magic mirror**.

Every viable virtual try-on model available today takes 5–20 seconds per still image. There is no real-time "stand in front of the camera and see yourself move in the outfit" product achievable at prototype cost or timeline.

If we pitch a live AR mirror and then demo a 10-second render, we lose the room. If we pitch an **AI fitting room**, the exact same demo lands as impressive and credible.

### Product definition

> A kiosk and web experience that takes one good photo of a customer, lets them browse a brand's catalog, and returns photorealistic images of *that customer* wearing selected items in 5–15 seconds, with side-by-side comparison and a QR code to take the results home.

### Two deployment shapes, one codebase

| Shape | Description | Advantage |
|---|---|---|
| **In-store kiosk** | Tablet on a stand, marked floor position, controlled lighting | We own input quality, so output quality is dramatically better. This is the demo. |
| **Brand website widget** | Embeddable iframe/SDK on a product page; customer uploads once, session persists | Scales without hardware; "Try this on" next to Add to Cart |

### The second product (commercial angle)

The same pipeline run in reverse is an easier sale:

**Brands feed flat-lay product shots → get on-model catalog imagery without a photoshoot.**

- No customer photos, therefore no privacy questions
- Immediate, measurable ROI (photoshoot cost savings are a number the client already has in their head)
- Same stack, same API, no additional engineering

**Strategy:** lead with consumer try-on because it is the flashy demo. Close with both, because catalog generation is what actually gets budget approved.

---

## 2. Prototype Scope

### Must-have features

| Feature | Rationale |
|---|---|
| Photo upload **and** webcam capture with pose/framing overlay | The overlay is the single biggest quality lever. Non-negotiable. |
| Pre-generation quality gate (full body visible, front-facing, arms clear of torso) | Stops ~80% of ugly failures before we spend money on them |
| 8–12 curated garments from **one** brand | Curated, not comprehensive |
| Single-garment try-on, async with real progress states | |
| Before/after slider | The moment that sells it |
| Outfit gallery — 3+ results side by side | The second moment that sells it |
| Download + QR share | Makes it feel like a shipped product, not an experiment |
| Basic admin catalog upload | So we can add the client's own items live in the meeting |

### Explicitly postponed — and we say so out loud

- Real-time video / AR mirror
- **Size and fit recommendation** — this will be the first question a retailer asks, and try-on models do not do it. Fit requires body measurement, which is an entirely separate stack. Have this answer ready.
- Shoes, jewellery, bags, glasses (different models, different pipelines)
- 3D garment simulation, fabric physics
- User accounts, checkout integration, analytics dashboards, native mobile apps
- Multi-person photos

---

## 3. Technical Architecture

### System shape

```
Next.js kiosk PWA (tablet, portrait)
  ├─ getUserMedia capture + MediaPipe Pose overlay (client-side, zero latency)
  ├─ presigned PUT direct to object storage
  └─ SSE/WebSocket for job status

FastAPI
  ├─ POST /sessions                      → session_id, consent record, TTL
  ├─ POST /sessions/{id}/person          → presign, then register + preprocess
  ├─ GET  /brands/{id}/catalog
  ├─ POST /tryon {person_id, garment_ids[], category} → job_id
  ├─ GET  /jobs/{id}  (+ SSE stream)
  └─ POST /admin/garments                → ingest, bg-removal, categorize

Redis + ARQ/Celery worker pool
  states: queued → preprocess → generate → postprocess → done | failed

Postgres
  brands, garments, sessions,
  generations(provider, seed, latency_ms, cost_usd, status)

Object storage: Cloudflare R2 or MinIO
  Lifecycle rule deletes person/ prefix after 24h
```

### The decision that matters most

Put every model behind a provider interface **from day one**.

```python
from typing import Protocol, Literal

class TryOnProvider(Protocol):
    name: str
    async def generate(
        self,
        person: Image,
        garment: Image,
        category: Literal["tops", "bottoms", "one-pieces"],
        seed: int | None = None,
    ) -> TryOnResult: ...
```

Implementations:

- `FalKlingProvider`
- `FalFashnProvider`
- `VertexVTOProvider`
- `GeminiEditProvider` (fallback only)
- `LocalFashnProvider` (production path)

**Why:** this model landscape turns over every few months, and we will want to A/B two providers on the same garment during the pitch. Making that a config change rather than a refactor is worth the hour it costs.

Note that input field names differ between providers (`human_image_url` / `garment_image_url` vs `model_image` / `garment_image`), so normalize inside the wrapper.

### Catalog prep pipeline

Offline, one-time per garment:

1. Background removal
2. White-canvas standardization
3. Category tagging (tops / bottoms / one-pieces)
4. Store **two** assets — a styled one for the UI card, a clean one for model input

Retailer photos usually arrive as on-model shots featuring the wrong model. FASHN's `product-to-model` and `model-swap` endpoints handle that conversion.

### Post-processing

- Upscale the body region. Output is 576×864 to 864×1296, which looks soft on a 4K kiosk screen.
- **Do not run face restoration.** It changes the face and destroys the "that's me" effect that the whole product depends on.
- Safer approach: composite the original face region back with a feathered mask at high opacity.

### Caching

Hash `(person_image_sha, garment_id, provider, seed)` and skip regeneration.

During a demo where the client tries the same jacket three times, this is the difference between instant and awkward.

### Deployment

- Docker Compose on a single VPS (no GPU needed — all inference is API-side)
- Vercel or the same box for frontend
- Cloudflare R2 for storage
- **No Kubernetes for a prototype**

---

## 4. AI Model Options

### Two genuinely different families

**Dedicated VTON models (inpainting-style)**
They repaint only the garment region and leave face, hair, hands and background untouched by construction. Identity preservation is structural, not prompted. **This is what we want.**

**Frontier image editors** (Gemini 3 Pro Image / Nano Banana Pro, Qwen-Image-Edit, Seedream)
They regenerate the whole frame. Better at unusual garments and layering, but they subtly redraw the face. For a customer standing at a kiosk looking at themselves, subtle face drift is disqualifying. Google's own tooling treats these as composition-only rather than true try-on.

→ Keep frontier editors as a **fallback for categories the dedicated models refuse**, never as the primary path.

### Hosted options

| Option | Notes | Price |
|---|---|---|
| **FASHN v1.6** (fal or FASHN direct) | 864×1296. ~5s performance mode, 8s balanced, 12–17s quality. Strongest on garment text, prints and logos. Accepts both on-model and flat-lay references. | ~$0.075/gen on fal |
| **Kling Kolors v1.5** (fal) | Cleanest, most photographic composites in head-to-head tests. Holds pose, skin tone, body shape. Simple two-image call. Commercial use permitted. | $0.07/gen |
| **Google Vertex `virtual-try-on-001`** | First-party, GA. Up to 4 outputs per request, output matches input resolution, English prompts only, default quota 50 req/min. Priced under Imagen. | ~$0.04/image tier |
| **FLUX Virtual Try-On Pro** (fal) | Takes a styling prompt — tucked in vs untucked, sleeves rolled | Per megapixel |

### Open source — read before anyone clones a repo

**Blocked for commercial use.** StableVITON, OOTDiffusion, CatVTON, IDM-VTON, VITON-HD and HR-VITON all ship under **CC BY-NC-SA**, which forbids commercial use and forces derivative works under the same license. The VITON-HD and DressCode datasets are themselves non-commercial, so even training from scratch on them does not free us.

> **Do not build the demo on these. Demos become products.**

**The exception, and it is a significant one.** FASHN open-sourced **VTON v1.5 under Apache-2.0**:

| Spec | Value |
|---|---|
| Architecture | MMDiT (Multimodal Diffusion Transformer) |
| Parameters | 972M |
| Weights size | ~2 GB |
| Output resolution | 576×864 |
| Categories | tops / bottoms / one-pieces |
| Inference | Maskless, pixel space |
| Hardware | Single L4 / A10 / 4090 |

⚠️ **Legal caveat to route past counsel:** the default human parser inherits the **NVIDIA SegFormer license**, which carries non-commercial restrictions. The full default inference stack is therefore *not* automatically clear for commercial use. The parser would need to be swapped.

### Recommendation

**Prototype:** fal with **Kling Kolors v1.5 as default**, **FASHN v1.6** for garments with prints, logos or text. One SDK, endpoint-string swap, commercial terms in place.

**Production:** migrate to **self-hosted FASHN VTON 1.5** once volume justifies a GPU. This turns $0.07/generation into GPU-hours.

---

## 5. User Flow and Edge Cases

```
Consent → Capture/Upload → Quality gate → Brand → Garment(s) → Queue → Result → Compare → QR
```

### Handling rules

**Upper body vs full body**
Gate on garment category. If the customer selects bottoms or a one-piece and the capture is waist-up, block with "step back a little" rather than generating something broken.

**Poses**
Front-facing, arms 15–20° away from torso, no crossed arms, no hands on hips. Enforce with the live overlay — MediaPipe gives wrist-to-hip distance for free.

**Lighting**
In a kiosk we control this. A cheap ring light and a plain backdrop will beat any model-level tuning we could do.

**Multiple garments**
Chain passes — tops, then bottoms, feeding output into input. **Cap at two.** Error compounds fast and a third pass visibly degrades the face and hands.

**Background and face**
Dedicated VTON preserves both. Offer background replacement as a toggle, not a default — customers trust the result less when the room changes.

**Failed generations**
Detect automatically:
1. Identity-similarity score between input and output face crops
2. Garment-similarity score against the catalog asset
3. Below threshold → retry with different seed
4. Still failing → retry with different provider
5. Still failing → serve pre-cached result

> **Never surface a failure during a live demo.**

### ⚠️ The risk nobody else will flag

These models are trained overwhelmingly on Western catalog data, and the category taxonomy is literally *tops / bottoms / one-pieces*.

**Shalwar kameez, sherwani, saree, and especially dupatta drape are out of distribution.** A long kameez may map acceptably to `one-pieces`. A dupatta will very likely fail.

**Action:** test 15–20 South Asian garments against two providers **in the first two days**, before committing to anything else. Then curate the demo catalog around what actually works, and tell the client honestly that regional garments need a fine-tune.

Saying this first makes us the expert. Having it discovered mid-demo makes us the vendor who oversold.

---

## 6. Prototype UI/UX

Portrait tablet layout, dark surface, brand accent color pulled from the client's logo so it reads as *their* product.

| Screen | Design notes |
|---|---|
| **Idle / attract** | Looping before/after reel, single "Try it on" CTA |
| **Consent** | One screen, plain language: "your photo is deleted after this session." Enterprise buyers notice this and it costs nothing. |
| **Capture** | Live feed with translucent body silhouette, traffic-light indicator (framing / lighting / pose), shutter disabled until all three are green. **This one screen does more for perceived quality than any model choice.** |
| **Catalog** | Horizontally scrolling cards, garment on white, name and price, multi-select up to 3 |
| **Generating** | **No spinner.** Show the customer's actual photo with a shimmer sweep and staged copy: *analyzing pose → matching fabric → rendering*. Ten seconds feels like two. |
| **Result** | Full-bleed image, draggable before/after handle, thumbnail strip of other outfits, "Try another" + QR panel |
| **Compare** | 2×2 grid of generated looks with shortlist hearts. This is the screenshot that goes in the agency's deck. |

---

## 7. Implementation Plan

### Phase 1 — Working demo (1–2 weeks)

- **Day 1–2: garment compatibility spike** (see §5 risk)
- Single provider, hardcoded 10-item catalog
- Upload only, no webcam
- FastAPI + Redis job queue
- Raw result screen

*Goal: an honest end-to-end render. Not polish.*

### Phase 2 — Realism and UX (1–2 weeks)

- Webcam + pose overlay + quality gate
- Provider abstraction with automatic fallback
- Retry logic, face composite-back, upscaling
- Full UI per §6, QR sharing

### Phase 3 — Multi-brand (2–3 weeks)

- Tenancy model
- Admin catalog ingestion with background removal and auto-categorization
- Kiosk mode with attract loop and session reset
- Per-brand theming
- Basic funnel analytics

### Phase 4 — Production readiness

- Self-hosted FASHN 1.5 on GPU, hosted API as burst overflow
- Rate limiting and abuse controls
- Formal retention and consent policy
- Observability on cost-per-generation and failure rate
- Embeddable widget for brand websites

---

## 8. Technical Challenges

Ranked by how much they will actually hurt.

**1. Regional garment coverage**
Highest risk, lowest awareness. Test first. See §5.

**2. Unit economics**
At $0.07/generation, a store doing 200 sessions/day × 3 outfits each is **~$1,300/month in inference alone, per store**.

Model this *before* pricing is discussed. It determines whether the product is a subscription, a per-generation charge, or a loss leader. Self-hosting is the answer at volume, and having a path to it is worth saying out loud.

**3. Input quality**
More variance comes from bad photos than from bad models. Solved by the capture overlay and controlled kiosk lighting.

**4. Hands, arms, occlusion**
Crossed arms, hands in pockets, a bag across the body — all break garment warping. Prevent at capture.

**5. Layering and structured garments**
Open jackets over shirts, lapels, blazers over kurtas. Weak across the board. Prefer closed, single-layer items in the demo catalog.

**6. Latency perception**
Fixable with UI, not with engineering. See §6 and Appendix B.

**7. Privacy and consent**
Customer photos are biometric-adjacent. Required:
- Ephemeral storage with hard TTL
- Explicit on-screen consent
- No training on customer images
- No face embeddings retained
- Clear "no minors without a guardian" policy

Brand and mall legal teams will ask. A written answer is a differentiator.

**8. Scaling**
Provider rate limits are real — Vertex defaults to 50 requests/min per project. Queue with backpressure and multi-provider spillover.

---

## 9. Customer Demo Strategy

- **Pre-bake the hero results.** Generate a golden set from 3–4 volunteers across 10 garments the day before, verified by eye. Open and close with those.
- **Live-generate exactly once**, on one willing volunteer, mid-demo, *after* the pre-baked reel has established the quality bar. Live generation is the credibility moment; it should not also be the quality moment.
- **Run offline-capable.** Local cache, phone hotspot backup. Venue wifi will fail.
- **Use the client's own catalog.** 8–10 of *their* SKUs, ingested the night before. This alone converts better than any feature.
- **Show the second product.** Take one of their flat-lay product shots and generate on-model catalog imagery in front of them.
- **Volunteer the limitations yourself.** Name the dupatta problem. Name that fit sizing isn't solved. Credibility buys the roadmap conversation.

---

## 10. Final Recommendation

### MVP scope
Guided capture + 10-item single-brand catalog + single-garment try-on + before/after + compare grid + QR share + minimal admin. Kiosk-first, web widget second.

### AI approach
fal as the gateway. **Kling Kolors v1.5** default (cost, pose fidelity, clean two-image API, commercial terms). **FASHN v1.6** for prints and logos. **Gemini 3 Pro Image** as last-resort fallback for garments the dedicated models won't handle. All behind the provider interface. Production path is self-hosted FASHN VTON 1.5 under Apache-2.0, with the human-parser licensing cleared first.

### Stack
Next.js PWA + MediaPipe → FastAPI + Redis/ARQ → Postgres → Cloudflare R2 → Docker Compose on a single VPS.

### Complexity
Moderate and well-bounded. A two-person team ships Phase 1 in 10–14 days, Phase 2 in another 10. The integration is not hard; the curation and the capture UX are where the work is.

### Biggest risks, in order
1. Regional garment coverage
2. Per-generation unit economics at store scale
3. Input photo quality
4. Expectation gap between "AI mirror" and what actually exists

### Build first
**The garment compatibility spike.** Twenty garments, two providers, one afternoon, a grid of results. Everything else in this plan is contingent on what that grid shows.

### One-paragraph description for the agency owner

> An AI fitting room for clothing retail. A customer takes one guided photo at an in-store kiosk or on the brand's website, picks items from the brand's catalog, and within seconds sees photorealistic images of themselves wearing each outfit, with side-by-side comparison and a QR code to save the looks. It runs on the customer's real photo, so their face, body and surroundings stay intact. The same engine also generates on-model catalog imagery from flat product shots, letting brands cut photoshoot costs. The prototype runs on a single tablet with a curated catalog and needs no hardware beyond a stand and a light.

---

## Appendix A — fal.ai Integration Reference

Both recommended models are live on fal and work with pay-as-you-go billing.

### Kling Kolors Virtual Try-On v1.5

| Field | Value |
|---|---|
| Endpoint | `fal-ai/kling/v1-5/kolors-virtual-try-on` |
| Price | $0.07 per generation |
| Licensing | Marked commercial use |
| Inputs | `human_image_url`, `garment_image_url` |

### FASHN Virtual Try-On v1.6

| Field | Value |
|---|---|
| Endpoint | `fal-ai/fashn/tryon/v1.6` |
| Price | $0.075 per generation |
| Output | 864×1296 |
| Inputs | `model_image`, `garment_image`, `category`, `mode`, `garment_photo_type`, `num_samples`, `segmentation_free`, `output_format` |

**Also available:** `fal-ai/fashn/tryon/v1.5` at 576×864 — slightly faster and a lower tier.

⚠️ **Deprecated:** the old `FASHN/tryon` path is no longer supported. Use `fal-ai/fashn/tryon/v1.6`.

### Sample call

```js
import { fal } from "@fal-ai/client";

const result = await fal.subscribe("fal-ai/fashn/tryon/v1.6", {
  input: {
    model_image: personUrl,
    garment_image: garmentUrl,
    category: "auto",
    mode: "balanced",
  },
  logs: true,
  onQueueUpdate: (update) => {
    if (update.status === "IN_PROGRESS") {
      update.logs.map((l) => l.message).forEach(console.log);
    }
  },
});
```

For long-running work, use the queue API with a webhook:

```js
const { request_id } = await fal.queue.submit("fal-ai/fashn/tryon/v1.6", {
  input: { model_image: personUrl, garment_image: garmentUrl },
  webhookUrl: "https://api.adrover.app/webhooks/fal",
});
```

---

## Appendix B — Latency Budget

### Documented model inference times

| Model | Mode | Time |
|---|---|---|
| FASHN v1.6 | performance | ~5s |
| FASHN v1.6 | balanced | ~8s |
| FASHN v1.6 | quality | 12–17s (varies with input resolution) |
| FASHN v1.5 | — | Slightly faster than v1.6 at 576×864 |
| Kling Kolors v1.5 | — | No published figure; ~15s observed in practice. Budget 10–15s. |

### Real end-to-end wall time

What the customer actually feels is more than the model number.

| Stage | Time |
|---|---|
| Upload photo to storage | 1–3s (worse on store wifi) |
| fal queue wait / cold start | 0–5s, spiky under load |
| Inference | 5–17s |
| Download + display | 1–2s |
| **Total** | **~8–25s** |

### Recommendation

Run FASHN in `balanced` mode and design the UI around **10–12 seconds**.

Do not use `quality` mode live — the extra realism is not visible on a tablet, and the wait is.

### Two optimizations with real impact

1. **Resize the person photo to ~1024px on the long edge before sending.** Large inputs push quality mode toward the 17s end.
2. **Fire the upload the moment the shutter is pressed**, not after garment selection, so the transfer overlaps with catalog browsing.

---

*End of document.*