"""Model providers behind one interface (spec §3).

Field names differ between providers (`human_image_url`/`garment_image_url` vs
`model_image`/`garment_image`), so normalization happens here and nowhere else.
Adding a provider is a dict entry; A/B-ing two is a query param.
"""
import os, time
import fal_client

# Appendix A. `seed` is in the signature because §5's retry ladder needs it in
# Phase 2 — neither fal endpoint accepts one today, so it only affects the cache key.
PROVIDERS = {
    "kling": {
        "endpoint": "fal-ai/kling/v1-5/kolors-virtual-try-on",
        "cost_usd": 0.07,
        "args": lambda person, garment, category, seed: {
            "human_image_url": person,
            "garment_image_url": garment,
        },
    },
    "fashn": {
        "endpoint": "fal-ai/fashn/tryon/v1.6",
        "cost_usd": 0.075,
        "args": lambda person, garment, category, seed: {
            "model_image": person,
            "garment_image": garment,
            "category": category,
            "mode": "balanced",  # Appendix B: quality mode is not worth the wait on a tablet
        },
    },
}

def default_provider():
    """Read at call time — .env is loaded after this module is imported."""
    return os.getenv("TRYON_PROVIDER", "kling")


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


async def generate(provider, person_url, garment_url, category, seed=None):
    """-> {url, latency_ms, cost_usd, provider}. Raises on failure; caller decides policy."""
    spec = PROVIDERS[provider]
    t0 = time.time()
    res = await fal_client.subscribe_async(
        spec["endpoint"], arguments=spec["args"](person_url, garment_url, category, seed)
    )
    url = first_url(res)
    if not url:
        raise ValueError(f"no image url in {provider} response")
    return {
        "url": url,
        "latency_ms": int((time.time() - t0) * 1000),
        "cost_usd": spec["cost_usd"],
        "provider": provider,
    }
