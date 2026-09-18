# Running locally

Two separate things live here. You almost certainly want the first.

| | What it is | Cost |
|---|---|---|
| **`api/`** | The try-on app. Pick a garment, take a photo, see one render. | ~$0.07 per render |
| **`spike/`** | Batch experiment. Every garment × every provider → one HTML grid. | ~$4.50 for the full set |

Both read `FAL_KEY` from `.env` at the repo root. It is already set.

---

## The app

```bash
cd api
./serve.sh
```

That is the whole command. It starts two listeners, because the camera rule differs by
origin:

```
  laptop : http://localhost:8100          (no warning)
  phone  : https://10.178.37.222:8443     (accept the certificate warning once)
```

**On the laptop, use the http:// URL.** `http://localhost` already counts as a secure
context, so `getUserMedia` works with no certificate and no interstitial. The attached
Z-Star 1080p webcam is visible to Chrome.

**The phone needs https://.** `getUserMedia` silently refuses plain `http://` over a LAN —
no prompt, no error, just no camera. Hence the self-signed cert on 8443. The phone must be
on the same wifi and will warn once: *Advanced → Proceed*.

A laptop webcam sits about an arm away, so it frames head-and-torso. That is fine for
`tops` but cannot satisfy the full-body requirement for `bottoms` and `one-pieces` (§5) —
either back away from the desk or use the phone for those.

### Using it

1. Pick a garment.
2. Allow camera access. A traffic light checks **framing / pose / lighting** on the live
   preview and keeps the shutter locked until all three are green (§2). It adapts to the
   garment: `bottoms` and `one-pieces` demand legs and feet in frame, because a waist-up
   photo cannot wear trousers (§5).
3. Take the photo, retake until it is good.
4. **Try it on.** 10–15 seconds.
5. Drag the slider to compare, download, or scroll up and pick another garment.

The same photo + garment never bills twice — repeats come back instantly from cache (§3).

### First run on a fresh clone

```bash
cd api
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./serve.sh
```

---

## The spike

Answers "which garments do these models actually handle" in one grid. Needs person
photos, which the app does not.

```bash
# put 2-3 full-body, front-facing photos here first
cp ~/my-photo.jpg spike/persons/

cd spike
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py            # prints the estimated spend, asks before charging
```

Then open `spike/out/results.html`. `--rebuild` redraws the grid without spending
anything; results are cached, so a rerun only pays for new combinations.

To pull more garments: `python fetch_garments.py --dry-run` to preview, without `--dry-run`
to download.

---

## When it breaks

**Phone cannot reach the URL** — check both devices are on the same wifi. Some routers
have client isolation on guest networks, which blocks it entirely. Note the phone is port
**8443**, not 8100.

**Camera never prompts on the phone** — you are on `http://` instead of `https://`.

**Camera never prompts on the laptop** — check Chrome did not remember a "Block" for this
site: click the icon left of the address bar → Camera → Allow, then reload.

**Shutter stays locked** — read the red lines; they say what to physically change. The
gate refuses seated poses, arms against the torso, sideways stances and blown-out
backlight, because each of those produces a render that looks like a model failure but is
not (§8-3). If the gate itself fails to load, it unlocks the shutter and says so rather
than trapping you.

**"Quality gate unavailable"** — MediaPipe's wasm and model come from jsdelivr and
storage.googleapis.com on first load (~11s, then cached). Offline or blocked, you get the
shutter back with no checks.

**Camera is missing entirely** — confirm the OS sees it before blaming the browser:
```bash
ls /dev/video*                  # should list video0
lsusb | grep -i cam
```

**Certificate warning returns after it worked** — your LAN IP changed. `serve.sh` detects
this and reissues the cert automatically; accept the warning once more.

**`address already in use`** — something else holds 8100:
```bash
pkill -f "uvicorn[ ]app:app"     # brackets stop the pattern matching its own shell
```
Without the brackets `pgrep`/`pkill -f` matches the very shell running the command and
kills it instead. Two ports are in use now: 8100 (http) and 8443 (https).

**Every render fails** — check the key reaches fal:
```bash
cd api && .venv/bin/python -c "import app, fal_client.auth as a; print(a.fetch_credentials()[:8])"
```

**Spend so far** — `curl -s http://localhost:8100/stats`
