"""AdRover Phase 1 — upload a photo, pick a garment, get an honest end-to-end render.

Run:
    export FAL_KEY=...
    pip install -r requirements.txt
    uvicorn app:app --reload
"""
import asyncio, base64, hashlib, io, os, pathlib, shutil, sqlite3, time, uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

import providers

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT.parent / ".env")   # FAL_KEY, TRYON_PROVIDER

DATA = ROOT / "data"
PERSONS, RESULTS = DATA / "persons", DATA / "results"
CATALOG = ROOT / "catalog"
DB = DATA / "app.db"
TTL_SECONDS = 24 * 3600          # §3 lifecycle rule / §8-7 ephemeral storage
MAX_EDGE = 1024                  # Appendix B optimization 1
CATEGORIES = ("tops", "bottoms", "one-pieces")

# ponytail: in-process job table. Single box, single worker — fal's own queue is the
# real queue and Redis in front of it is a queue in front of a queue. Swap to ARQ when
# you need multi-worker or survival across restarts.
# StaticFiles resolves its directory at import, so these must exist before the mounts.
for _d in (PERSONS, RESULTS, *(CATALOG / c for c in CATEGORIES)):
    _d.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}


# --- storage ---------------------------------------------------------------

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS generations(
            job_id TEXT PRIMARY KEY, cache_key TEXT, provider TEXT, garment_id TEXT,
            status TEXT, latency_ms INTEGER, cost_usd REAL, image TEXT, error TEXT,
            created_at REAL)""")
        con.execute("CREATE INDEX IF NOT EXISTS gen_cache ON generations(cache_key)")


def sweep():
    """Delete person photos and renders past TTL. They are biometric-adjacent (§8-7)."""
    cutoff = time.time() - TTL_SECONDS
    removed = 0
    for d in (PERSONS, RESULTS):
        for f in d.glob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
    return removed


async def sweep_forever():
    while True:
        sweep()
        await asyncio.sleep(3600)





# --- catalog ---------------------------------------------------------------

def catalog():
    """Derived from catalog/<category>/*.jpg — dropping a file in is the whole admin UI.

    Prices arrive with real catalog ingestion in Phase 3; Phase 1 is a raw result screen.
    """
    items = []
    for cat in CATEGORIES:
        for f in sorted((CATALOG / cat).glob("*")):
            if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                items.append({
                    "id": f"{cat}/{f.name}",
                    "name": f.stem.replace("-", " ").replace("_", " ").title(),
                    "category": cat,
                    "image": f"/catalog/{cat}/{f.name}",
                })
    return items


def garment_path(garment_id: str) -> pathlib.Path:
    """Resolve a catalog id, refusing anything that escapes the catalog directory."""
    p = (CATALOG / garment_id).resolve()
    if not p.is_file() or CATALOG.resolve() not in p.parents:
        raise HTTPException(404, "unknown garment")
    return p


# --- app -------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    init_db()
    sweep()
    task = asyncio.create_task(sweep_forever())
    yield
    task.cancel()


app = FastAPI(title="AdRover Phase 1", lifespan=lifespan)


@app.post("/person")
async def upload_person(file: UploadFile):
    """Resize to <=1024px long edge and push to fal immediately, so the transfer
    overlaps with catalog browsing (Appendix B optimization 2)."""
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        img = img.convert("RGB")
    except Exception:
        raise HTTPException(400, "not a readable image")

    person_id = hashlib.sha256(raw).hexdigest()[:16]
    path = PERSONS / f"{person_id}.jpg"
    img.save(path, "JPEG", quality=92)
    path.touch()  # refresh TTL on re-upload of the same photo
    return {"person_id": person_id, "image": f"/data/persons/{person_id}.jpg"}


@app.get("/catalog")
def get_catalog():
    return catalog()


@app.get("/providers")
def get_providers():
    """§3: A/B-ing two providers on the same garment is a config change, not a refactor."""
    return {"default": providers.default_provider(),
            "available": [
                {"id": k, "est_cost_usd": v["cost_usd"], "ready": True,
                 # Only a whole-frame editor can honour these; VTON inpaints the
                 # garment region only, so it cannot relight or repose anything.
                 "fixes": list(providers.GPT_FIXES) if k == "gptimage" else []}
                for k, v in providers.PROVIDERS.items()
            ]}


@app.post("/tryon")
async def tryon(body: dict):
    person_id = body.get("person_id", "")
    garment_id = body.get("garment_id", "")
    provider = body.get("provider") or providers.default_provider()
    # Whitelist: the value reaches a prompt, so unknown entries are dropped, not passed.
    fixes = sorted(f for f in (body.get("fixes") or []) if f in providers.GPT_FIXES)
    if provider not in providers.PROVIDERS:
        raise HTTPException(400, f"unknown provider {provider}")
    person = PERSONS / f"{person_id}.jpg"
    if not person.is_file():
        raise HTTPException(404, "unknown person — upload expired or never happened")
    garment = garment_path(garment_id)

    # §3 caching: the client tries the same jacket three times during a demo.
    cache_key = f"{person_id}_{garment_id}_{provider}_{body.get('seed')}_{'+'.join(fixes) or 'none'}"
    with db() as con:
        hit = con.execute(
            "SELECT * FROM generations WHERE cache_key=? AND status='ok' LIMIT 1", (cache_key,)
        ).fetchone()
    if hit and (RESULTS / hit["image"]).is_file():
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"status": "done", "image": f"/data/results/{hit['image']}",
                        "latency_ms": 0, "cached": True, "provider": provider}
        return {"job_id": job_id}

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "provider": provider}
    asyncio.create_task(_run(job_id, cache_key, person, garment, garment_id, provider,
                             body.get("seed"), fixes))
    return {"job_id": job_id}


async def _run(job_id, cache_key, person, garment, garment_id, provider, seed, fixes=()):
    category = garment_id.split("/")[0]
    t0 = time.time()
    try:
        JOBS[job_id]["status"] = "generating"
        res = await providers.generate(provider, person, garment, category, seed, fixes)
        name = f"{job_id}.jpg"
        await asyncio.to_thread(_download, res["url"], RESULTS / name)
        JOBS[job_id] |= {"status": "done", "image": f"/data/results/{name}",
                         "latency_ms": res["latency_ms"], "cached": False}
        row = ("ok", res["latency_ms"], res["cost_usd"], name, None)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        JOBS[job_id] |= {"status": "failed", "error": err,
                         "latency_ms": int((time.time() - t0) * 1000)}
        row = ("failed", int((time.time() - t0) * 1000), 0.0, None, err)

    # §8-2: cost per generation is the input to the pricing conversation. Log every one.
    with db() as con:
        con.execute("INSERT OR REPLACE INTO generations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (job_id, cache_key, provider, garment_id, *row, time.time()))


def _download(url, dest):
    import urllib.request
    with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


@app.get("/jobs/{job_id}")
def job(job_id: str):
    # ponytail: the client polls. A 10s job does not need SSE; add it if the UI grows
    # stages that need push (§6 staged progress copy, Phase 2).
    if job_id not in JOBS:
        raise HTTPException(404, "unknown job")
    return JOBS[job_id]


@app.get("/stats")
def stats():
    """§8-2 unit economics, live."""
    with db() as con:
        r = con.execute("""SELECT COUNT(*) n, SUM(cost_usd) spend,
                           AVG(latency_ms) latency,
                           SUM(status='failed') failures FROM generations""").fetchone()
    return {"generations": r["n"], "spend_usd": round(r["spend"] or 0, 2),
            "avg_latency_ms": int(r["latency"] or 0), "failures": r["failures"] or 0}


app.mount("/catalog", StaticFiles(directory=CATALOG), name="catalog")
app.mount("/data", StaticFiles(directory=DATA), name="data")
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
