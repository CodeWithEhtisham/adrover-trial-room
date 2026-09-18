"""Self-check for the spike's non-API logic. python test_run.py"""
import json, pathlib, shutil, tempfile, run

# first_url survives both provider response shapes and returns None on neither.
assert run.first_url({"image": {"url": "A"}}) == "A"
assert run.first_url({"images": [{"url": "B"}]}) == "B"
assert run.first_url({"seed": 1, "nested": {"deep": [{"x": {"url": "C"}}]}}) == "C"
assert run.first_url({"seed": 1, "logs": ["done"]}) is None

# Cache key is stable per content and distinct per provider/garment.
tmp = pathlib.Path(tempfile.mkdtemp())
a, b, c = tmp / "a.jpg", tmp / "b.jpg", tmp / "c.jpg"
a.write_bytes(b"person"); b.write_bytes(b"garment"); c.write_bytes(b"person")
assert run.key(a, b, "kling") == run.key(c, b, "kling"), "same bytes must hit cache"
assert run.key(a, b, "kling") != run.key(a, b, "fashn")
assert run.key(a, b, "kling") != run.key(b, a, "kling")

# Grid renders ok/failed/not-run without blowing up, and escapes garment names.
run.OUT = run.IMG = tmp
persons = [a]
garments = [(tmp / "<script>.jpg", "one-pieces")]
garments[0][0].write_bytes(b"x")
results = {run.key(a, garments[0][0], "kling"):
           {"provider": "kling", "status": "ok", "error": None,
            "latency_ms": 9100, "cost_usd": 0.07, "image": "x.jpg"}}
run.write_grid(results, persons, garments)
grid = (tmp / "results.html").read_text()
assert "9100ms" in grid and "$0.070" in grid
assert "not run" in grid, "fashn had no result, must render as not-run"
assert "<script>" not in grid, "garment filename must be escaped"

run.write_grid({run.key(a, garments[0][0], "kling"):
                {"provider": "kling", "status": "failed", "error": "ValueError: nope",
                 "latency_ms": 300, "cost_usd": 0.0, "image": None}}, persons, garments)
assert "FAILED" in (tmp / "results.html").read_text()

shutil.rmtree(tmp)
print("ok")
