#!/usr/bin/env python3
"""Garment compatibility spike: every person x garment x provider -> a results grid.

Answers one question: which garments do the hosted VTON models actually handle?
Throwaway. Not the product.

Usage:
    export FAL_KEY=...
    pip install -r requirements.txt
    python run.py            # prints cost estimate, asks before spending
    python run.py --yes      # skip the prompt
    open out/results.html
"""
import argparse, asyncio, base64, hashlib, html, json, mimetypes, os, pathlib, time, urllib.request

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT.parent / ".env")   # FAL_KEY
OUT = ROOT / "out"
IMG = OUT / "img"
RESULTS = OUT / "results.json"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_EDGE = 1024   # Appendix B-1: large inputs push render time toward the 17s end

# Appendix A. category is FASHN-only; Kling takes two images and nothing else.
PROVIDERS = {
    "kling": {
        "endpoint": "fal-ai/kling/v1-5/kolors-virtual-try-on",
        "cost": 0.07,
        "args": lambda p, g, cat: {"human_image_url": p, "garment_image_url": g},
    },
    "fashn": {
        "endpoint": "fal-ai/fashn/tryon/v1.6",
        "cost": 0.075,
        "args": lambda p, g, cat: {
            "model_image": p, "garment_image": g,
            "category": cat, "mode": "balanced",
            # Every scraped reference is an on-model shot, not a flat-lay. Telling FASHN
           # that beats letting it guess. Change if you add flat-lay product shots.
           "garment_photo_type": "model",
        },
    },
}


def prep_person(path):
    """Resize to <=1024px long edge before upload.

    Persons only. Garments keep full resolution on purpose — print and logo fidelity
    is one of the things this grid is measuring, and FASHN is supposed to win on it.
    Without this the latency column compares photo sizes, not providers.
    """
    from PIL import Image
    dest = OUT / "resized" / f"{sha(path)}.jpg"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        img = Image.open(path)
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        img.convert("RGB").save(dest, "JPEG", quality=92)
    return dest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def first_url(obj):
    """Providers disagree on response shape ({image:{url}} vs {images:[{url}]}).
    Pull the first url out of whatever came back."""
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


def discover():
    persons = sorted(p for p in (ROOT / "persons").iterdir() if p.suffix.lower() in EXTS)
    garments = []
    for cat_dir in sorted((ROOT / "garments").iterdir()):
        if not cat_dir.is_dir():
            continue
        for g in sorted(cat_dir.iterdir()):
            if g.suffix.lower() in EXTS:
                garments.append((g, cat_dir.name))
    return persons, garments


def key(person, garment, provider):
    return f"{sha(person)}_{sha(garment)}_{provider}"


async def run_one(fal_client, sem, k, person_url, garment_url, cat, provider):
    spec = PROVIDERS[provider]
    async with sem:
        t0 = time.time()
        try:
            res = await fal_client.subscribe_async(
                spec["endpoint"], arguments=spec["args"](person_url, garment_url, cat)
            )
            url = first_url(res)
            if not url:
                raise ValueError(f"no image url in response: {json.dumps(res)[:200]}")
            local = IMG / f"{k}.jpg"
            urllib.request.urlretrieve(url, local)
            status, err = "ok", None
        except Exception as e:
            local, status, err = None, "failed", f"{type(e).__name__}: {e}"
        return k, {
            "provider": provider, "category": cat, "status": status, "error": err,
            "latency_ms": int((time.time() - t0) * 1000),
            "cost_usd": spec["cost"] if status == "ok" else 0.0,
            "image": local.name if local else None,
        }


def write_grid(results, persons, garments):
    """One row per (person, garment). Inputs on the left, each provider's output beside it."""
    def img(src, cls=""):
        return f'<img class="{cls}" src="{html.escape(src)}" loading="lazy">'

    rows = []
    for person in persons:
        for garment, cat in garments:
            cells = []
            for provider in PROVIDERS:
                r = results.get(key(person, garment, provider))
                if not r:
                    cells.append('<td class="skip">not run</td>')
                elif r["status"] == "ok":
                    cells.append(
                        f'<td>{img("img/" + r["image"])}'
                        f'<div class="meta">{r["latency_ms"]}ms · ${r["cost_usd"]:.3f}</div></td>'
                    )
                else:
                    cells.append(f'<td class="fail"><b>FAILED</b><div class="meta">{html.escape(r["error"] or "")}</div></td>')
            rows.append(
                f'<tr><td class="in">{img(os.path.relpath(person, OUT))}<div class="meta">{html.escape(person.name)}</div></td>'
                f'<td class="in">{img(os.path.relpath(garment, OUT))}<div class="meta">{html.escape(garment.name)}<br>{cat}</div></td>'
                + "".join(cells) + "</tr>"
            )

    ok = [r for r in results.values() if r["status"] == "ok"]
    spend = sum(r["cost_usd"] for r in results.values())
    head = "".join(f"<th>{p}</th>" for p in PROVIDERS)
    (OUT / "results.html").write_text(f"""<!doctype html><meta charset=utf-8>
<title>AdRover garment compatibility spike</title>
<style>
body{{background:#111;color:#eee;font:14px/1.4 system-ui;margin:24px}}
table{{border-collapse:collapse}} td,th{{border:1px solid #333;padding:8px;vertical-align:top;text-align:center}}
img{{max-width:220px;max-height:330px;display:block;margin:0 auto}}
.in img{{max-width:150px;max-height:220px}} .meta{{color:#888;font-size:11px;margin-top:4px}}
.fail{{background:#3a1414;color:#ff9a9a}} .skip{{color:#555}}
h1{{font-size:18px}} .sum{{color:#aaa;margin-bottom:16px}}
</style>
<h1>Garment compatibility — {len(persons)} persons × {len(garments)} garments × {len(PROVIDERS)} providers</h1>
<p class="sum">{len(ok)}/{len(results)} succeeded · ${spend:.2f} spent</p>
<table><tr><th>person</th><th>garment</th>{head}</tr>{''.join(rows)}</table>
""")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="skip the spend confirmation")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--rebuild", action="store_true", help="regenerate results.html only, no API calls")
    args = ap.parse_args()

    IMG.mkdir(parents=True, exist_ok=True)
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    persons, garments = discover()
    if not persons or not garments:
        raise SystemExit("Drop photos into spike/persons/ and spike/garments/<tops|bottoms|one-pieces>/ first.")

    if args.rebuild:
        write_grid(results, persons, garments)
        print(f"wrote {OUT/'results.html'}")
        return

    todo = [(p, g, c, prov) for p in persons for g, c in garments for prov in PROVIDERS
            if key(p, g, prov) not in results]
    cached = len(persons) * len(garments) * len(PROVIDERS) - len(todo)
    est = sum(PROVIDERS[prov]["cost"] for _, _, _, prov in todo)
    print(f"{len(todo)} generations to run ({cached} cached) — estimated ${est:.2f}")
    if not todo:
        write_grid(results, persons, garments)
        return
    if not args.yes and input("spend it? [y/N] ").strip().lower() != "y":
        raise SystemExit("aborted")

    import fal_client

    # Upload each distinct input once, not once per job.
    # Data URIs, not fal storage uploads. fal_client 1.0.2 (latest) posts
    # /storage/upload/initiate?storage_type=gcs which the API rejects with "Invalid
    # storage type", and fal's upload CDN write-times-out from this network anyway.
    def ref(path):
        ct = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return f"data:{ct};base64," + base64.b64encode(path.read_bytes()).decode()

    urls = {}
    for path in persons:
        urls[path] = ref(prep_person(path))
        print(f"encoded {path.name} (resized)")
    for path in (g for g, _ in garments):
        urls[path] = ref(path)
        print(f"encoded {path.name}")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [run_one(fal_client, sem, key(p, g, prov), urls[p], urls[g], c, prov)
             for p, g, c, prov in todo]
    # as_completed, not gather: a spike that spends money saves every result as it lands,
    # so a Ctrl-C or a crash costs nothing already paid for.
    for done, fut in enumerate(asyncio.as_completed(tasks), 1):
        k, rec = await fut
        results[k] = rec
        RESULTS.write_text(json.dumps(results, indent=2))
        print(f"[{done}/{len(tasks)}] {k} {rec['status']} {rec['latency_ms']}ms")

    write_grid(results, persons, garments)
    ok = sum(r["status"] == "ok" for r in results.values())
    print(f"\n{ok}/{len(results)} ok · ${sum(r['cost_usd'] for r in results.values()):.2f} spent")
    print(f"open {OUT/'results.html'}")


if __name__ == "__main__":
    asyncio.run(main())
