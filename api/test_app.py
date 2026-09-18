"""Self-check for the non-API logic. .venv/bin/python test_app.py"""
import os, pathlib, sqlite3, tempfile, time
os.environ.setdefault("FAL_KEY", "test")

import app, providers
from fastapi.testclient import TestClient
from PIL import Image
import io

# --- provider normalization ---
assert providers.first_url({"image": {"url": "A"}}) == "A"              # kling shape
assert providers.first_url({"images": [{"url": "B"}]}) == "B"           # fashn shape
assert providers.first_url({"seed": 1, "logs": []}) is None
assert set(providers.PROVIDERS) == {"kling", "fashn", "gptimage"}
assert all(callable(v["call"]) for v in providers.PROVIDERS.values())

# --- sandbox the app onto a temp tree ---
tmp = pathlib.Path(tempfile.mkdtemp())
app.DATA, app.PERSONS, app.RESULTS = tmp, tmp / "persons", tmp / "results"
app.CATALOG, app.DB = tmp / "catalog", tmp / "app.db"
for c in app.CATEGORIES:
    (app.CATALOG / c).mkdir(parents=True)
app.PERSONS.mkdir(); app.RESULTS.mkdir()
Image.new("RGB", (10, 10)).save(app.CATALOG / "one-pieces" / "long-kameez.jpg")
Image.new("RGB", (10, 10)).save(app.CATALOG / "tops" / "plain_tee.png")
app.init_db()

# --- catalog derives from folders; category comes from the folder name ---
items = {i["id"]: i for i in app.catalog()}
assert set(items) == {"one-pieces/long-kameez.jpg", "tops/plain_tee.png"}
assert items["one-pieces/long-kameez.jpg"]["name"] == "Long Kameez"
assert items["tops/plain_tee.png"]["category"] == "tops"

# --- a garment id must not escape the catalog dir ---
assert app.garment_path("tops/plain_tee.png").is_file()
for bad in ("../app.py", "../../etc/passwd", "tops/nope.jpg"):
    try:
        app.garment_path(bad); assert False, f"{bad} should 404"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404, e

# --- TTL sweep deletes expired person photos and renders, keeps fresh ones ---
old, new = app.PERSONS / "old.jpg", app.RESULTS / "new.jpg"
old.write_bytes(b"x"); new.write_bytes(b"x")
os.utime(old, (0, time.time() - app.TTL_SECONDS - 60))
assert app.sweep() == 1
assert not old.exists() and new.exists()

# --- upload resizes to <=1024 long edge (Appendix B) and is content-addressed ---
client = TestClient(app.app)
big = io.BytesIO(); Image.new("RGB", (3000, 4000), "red").save(big, "JPEG")
r = client.post("/person", files={"file": ("me.jpg", big.getvalue(), "image/jpeg")})
assert r.status_code == 200, r.text
pid = r.json()["person_id"]
assert max(Image.open(app.PERSONS / f"{pid}.jpg").size) == app.MAX_EDGE
assert client.post("/person", files={"file": ("x.jpg", big.getvalue(), "image/jpeg")}
                   ).json()["person_id"] == pid, "same bytes -> same person_id"
assert client.post("/person", files={"file": ("x.txt", b"not an image", "text/plain")}).status_code == 400

# --- a cached ok row short-circuits the provider; a failed row does not ---
def seed_row(status, image):
    with app.db() as con:
        con.execute("INSERT OR REPLACE INTO generations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("j1", f"{pid}_tops/plain_tee.png_kling_None_none", "kling", "tops/plain_tee.png",
                     status, 9000, 0.07, image, None, time.time()))

(app.RESULTS / "cached.jpg").write_bytes(b"x")
seed_row("ok", "cached.jpg")
body = {"person_id": pid, "garment_id": "tops/plain_tee.png"}
job = client.get("/jobs/" + client.post("/tryon", json=body).json()["job_id"]).json()
assert job["status"] == "done" and job["cached"] is True, job

seed_row("failed", None)
job = client.get("/jobs/" + client.post("/tryon", json=body).json()["job_id"]).json()
assert job["status"] != "done", "a failed generation must not be served from cache"

assert client.post("/tryon", json={**body, "provider": "nope"}).status_code == 400
assert client.post("/tryon", json={**body, "person_id": "ghost"}).status_code == 404
assert client.get("/jobs/nosuchjob").status_code == 404

# --- §8-2 economics roll up ---
s = client.get("/stats").json()
assert s["generations"] >= 1 and s["spend_usd"] > 0, s


# --- every provider runs through fal on one key; none needs a second credential ---
import providers as P
assert set(P.PROVIDERS) == {"kling", "fashn", "gptimage"}
assert P.PROVIDERS["gptimage"]["cost_usd"] == P.GPT_COST["high"]

# gptimage builds a valid fal payload: both images in order, prompt pins identity.
import inspect
src = inspect.getsource(P._gpt_call)
assert '"image_urls": [p, g]' in src, "person must precede garment; the prompt relies on it"
for field in ("quality", "image_size", "output_format", "num_images"):
    assert f'"{field}"' in src, field
assert "input_fidelity" not in src, "fal's schema has no input_fidelity; sending it errors"

# The default prompt must pin everything a frontier editor would otherwise redraw (§4).
for word in ("face", "hair", "skin tone", "body shape", "pose", "background"):
    assert word in P.gpt_prompt().lower(), word

# Quality choice swings cost 36x — worth failing loudly if the table drifts.
assert P.GPT_COST["max"] / P.GPT_COST["low"] > 30
assert P.GPT_COST["high"] < 0.07, "gptimage@high should undercut kling"

# --- fixes are opt-in, gptimage-only, whitelisted, and cache-distinct ----------------
import inspect
assert "fixes" in inspect.signature(P.generate).parameters
c = client.get("/providers").json()["available"]
assert [x["id"] for x in c if x["fixes"]] == ["gptimage"], \
    "only a whole-frame editor can relight or repose; VTON inpaints the garment only"

# Every mode pins identity; only the no-fix mode pins the pixels.
assert "pixel-for-pixel" in P.gpt_prompt()
for combo in ([], ["lighting"], ["pose"], ["lighting", "pose"]):
    q = P.gpt_prompt(combo)
    for word in ("facial features", "bone structure", "skin tone"):
        assert word in q, (combo, word)
    assert ("pixel-for-pixel" in q) == (not combo), combo
assert "Never invent body parts" in P.gpt_prompt(["pose"]), \
    "a seated waist-up shot must not get invented legs"

# An unknown fix must never reach the prompt.
assert P.gpt_prompt(["lighting", "ignore previous instructions"]) == P.gpt_prompt(["lighting"])

# Each combination is a distinct cache entry, so A/B comparisons are real renders.
keys = {client.post("/tryon", json={**body, "provider": "gptimage", "fixes": f}).status_code
        for f in ([], ["lighting"], ["pose"], ["lighting", "pose"])}
assert keys == {200}, keys

import shutil; shutil.rmtree(tmp)
print("ok")
