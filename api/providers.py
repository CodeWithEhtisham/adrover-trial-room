"""Model providers behind one interface (spec §3).

Every provider takes the same thing — two local image paths plus a category — and returns
the same thing. The differences (field names, transport, response shape, billing model)
are normalized here and nowhere else. Adding a provider is one dict entry.

Two genuinely different families are represented (§4):
  * dedicated VTON (kling, fashn) — repaints only the garment region, so identity is
    preserved structurally. Flat per-generation price.
  * frontier image editor (gptimage) — regenerates the whole frame, so the face can
    drift. Token-billed, so cost varies per call and is read back from usage.
"""
import base64, mimetypes, os, pathlib, time

import fal_client


def _data_uri(path: pathlib.Path) -> str:
    """fal takes a URL or a data URI. We use data URIs because fal_client 1.0.2 posts
    /storage/upload/initiate?storage_type=gcs, which the API rejects with "Invalid
    storage type", and fal's upload CDN write-times-out from some networks anyway."""
    ct = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{ct};base64," + base64.b64encode(path.read_bytes()).decode()


def first_url(obj):
    """Providers disagree on response shape ({image:{url}} vs {images:[{url}]})."""
    if isinstance(obj, dict):
        if isinstance(obj.get("url"), str):
            return obj["url"]
        for v in obj.values():
            if (u := first_url(v)):
                return u
    elif isinstance(obj, list):
        for v in obj:
            if (u := first_url(v)):
                return u
    return None


# --- fal-hosted dedicated VTON -------------------------------------------------------

def _fal(endpoint, build_args):
    async def call(person, garment, category, seed, fixes=()):
        # fixes are ignored by the dedicated VTON providers: they inpaint the garment
        # region only, so they cannot relight or repose a photo even if asked.
        res = await fal_client.subscribe_async(
            endpoint,
            arguments=build_args(_data_uri(person), _data_uri(garment), category, seed),
        )
        url = first_url(res)
        if not url:
            raise ValueError(f"no image url in {endpoint} response")
        return {"url": url, "cost_usd": None}
    return call


# --- OpenAI frontier editor ----------------------------------------------------------

# Identity preservation is the entire product (§4), so the prompt's job is to pin
# everything except the clothing. Frontier editors redraw whatever you do not nail down.
GPT_BASE = (
    "Dress the person from the first image in the garment from the second image. "
    "Match the garment's exact colour, print, pattern and cut. Photorealistic, with "
    "natural fabric drape and lighting consistent with the original photo."
)

# Repeated in every mode, and deliberately phrased as a rule about the person rather than
# about pixels — the opt-in fixes below all need permission to change pixels.
GPT_IDENTITY = (
    " The person's identity is not a photographic property: keep their face, facial "
    "features, bone structure, skin tone and hair exactly as captured. Do not slim, "
    "reshape, beautify or age them. Improve the photo, never the person."
)

# Default: change nothing but the clothes. The safest thing this model can be asked to do.
GPT_LOCK = (
    " Keep the person's pose, body shape and the background pixel-for-pixel unchanged — "
    "only their clothing changes."
)

# Opt-in, and each one trades identity safety for something else. Only a whole-frame
# editor can honour these at all; dedicated VTON ignores them by construction.
GPT_FIXES = {
    "lighting": (
        " Correct the photograph itself: recover detail in blown-out highlights and "
        "backlit windows, lift crushed shadows, neutralise colour casts, and clean up "
        "webcam sensor noise and compression artefacts."
    ),
    "pose": (
        " Make a small pose correction so the garment reads clearly: if the arms are "
        "crossed or pressed flat against the torso, move them a few degrees away from "
        "the body, and straighten a noticeably slouched or twisted upper body. Keep the "
        "same stance, camera angle, distance and framing — this is a slight adjustment, "
        "not a new photograph. Never invent body parts that are outside the frame."
    ),
}


def gpt_prompt(fixes=()):
    """Base + identity lock, plus any opt-in fixes. With no fixes the background is
    pinned; asking for a fix necessarily unpins it, which is why they are opt-in."""
    fixes = [f for f in GPT_FIXES if f in set(fixes or ())]
    body = GPT_BASE + ("".join(GPT_FIXES[f] for f in fixes) if fixes else GPT_LOCK)
    return body + GPT_IDENTITY


# 1024x1024 list price on fal, which passes OpenAI's token rates through without markup.
# Real cost also moves with output resolution; these are the figures to sanity-check against.
GPT_COST = {"low": 0.0059, "medium": 0.0132, "high": 0.0527, "xhigh": 0.0937, "max": 0.2107}


def _gpt_call(person, garment, category, seed, fixes=()):
    """fal's GPT Image 2.5 Flare edit. Same shape as the other fal providers, but the
    price swings 36x across quality tiers, so the cost is reported per call."""
    quality = os.getenv("GPT_IMAGE_QUALITY", "high")
    inner = _fal(os.getenv("GPT_IMAGE_ENDPOINT", "openai/gpt-image-2.5/flare/edit"),
                 lambda p, g, cat, sd: {
                     "image_urls": [p, g],          # order matters, see GPT_PROMPT
                     "prompt": gpt_prompt(fixes),
                     "quality": quality,
                     "image_size": "auto",          # follow the person photo's aspect
                     "output_format": "jpeg",
                     "num_images": 1,
                 })

    async def call(*a):
        res = await inner(*a)
        return {**res, "cost_usd": GPT_COST.get(quality)}
    return call(person, garment, category, seed, fixes)


# --- registry ------------------------------------------------------------------------

PROVIDERS = {
    "kling": {
        "cost_usd": 0.07,          # flat, per Appendix A
        "call": _fal("fal-ai/kling/v1-5/kolors-virtual-try-on",
                     lambda p, g, cat, seed: {"human_image_url": p, "garment_image_url": g}),
    },
    "fashn": {
        "cost_usd": 0.075,
        "call": _fal("fal-ai/fashn/tryon/v1.6",
                     lambda p, g, cat, seed: {
                         "model_image": p, "garment_image": g, "category": cat,
                         "mode": "balanced",          # App. B: quality mode is not worth the wait
                         "garment_photo_type": "model",
                     }),
    },
    "gptimage": {
        # Varies with quality; the call reports the real figure. This is the `high` default.
        "cost_usd": GPT_COST["high"],
        "call": _gpt_call,
    },
}


def default_provider():
    """Read at call time — .env is loaded after this module is imported."""
    return os.getenv("TRYON_PROVIDER", "kling")


async def generate(provider, person: pathlib.Path, garment: pathlib.Path, category,
                   seed=None, fixes=()):
    """-> {url, latency_ms, cost_usd, provider}. Raises on failure; caller sets policy.

    `seed` is in the signature because §5's retry ladder needs it in Phase 2; no provider
    here accepts one today, so it only affects the cache key.
    """
    spec = PROVIDERS[provider]
    t0 = time.time()
    res = await spec["call"](person, garment, category, seed, fixes)
    return {
        **res,
        "cost_usd": res["cost_usd"] if res["cost_usd"] is not None else spec["cost_usd"],
        "latency_ms": int((time.time() - t0) * 1000),
        "provider": provider,
    }
