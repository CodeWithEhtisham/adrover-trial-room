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
k = providers.PROVIDERS["kling"]["args"]("p", "g", "tops", None)
f = providers.PROVIDERS["fashn"]["args"]("p", "g", "tops", None)
assert k == {"human_image_url": "p", "garment_image_url": "g"}
assert f["model_image"] == "p" and f["category"] == "tops" and f["mode"] == "balanced"

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
                    ("j1", f"{pid}_tops/plain_tee.png_kling_None", "kling", "tops/plain_tee.png",
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

import shutil; shutil.rmtree(tmp)
print("ok")
