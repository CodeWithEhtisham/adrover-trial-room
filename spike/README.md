# Garment compatibility spike

Answers the §5 / §8-1 risk before anything else gets built: **which garments do the
hosted VTON models actually handle?** Throwaway harness, not product code.

## Run

`FAL_KEY` is read from the repo-root `.env` (see `.env.example`).

```bash
pip install -r requirements.txt
python run.py           # prints cost estimate, asks before spending
open out/results.html
```

Results are cached by `(person bytes, garment bytes, provider)` in `out/results.json`,
so reruns are free and only new images cost money. `--rebuild` regenerates the grid
with no API calls.

## Inputs

```
persons/                 2-4 full-body, front-facing, arms 15-20 deg clear of torso
garments/tops/           folder name IS the FASHN category
garments/bottoms/
garments/one-pieces/     put long kameez / saree / sherwani here
```

## What to load (the point of the spike)

Roughly 20 garments, deliberately skewed to the out-of-distribution cases:

- 5-6 Western control items (plain tee, button shirt, jeans, plain dress) — these
  should work; they establish the quality bar the rest is judged against
- 4-5 shalwar kameez, incl. at least two long ones
- 2-3 sherwani / kurta (structured, layered — expected weak per §8-5)
- 2-3 saree
- **2-3 with a dupatta** — the specific failure §5 predicts
- 2 with heavy print / logo / text — the case FASHN is supposed to win

## Reading the grid

`out/results.html` puts person + garment inputs beside each provider's output, with
latency and cost per cell. Judge by eye on three things and note them:

1. Did the garment survive (drape, hem length, dupatta present at all)?
2. Did the face survive? Any drift is disqualifying per §4.
3. Did it fail outright, and does one provider fail where the other doesn't?

The demo catalog gets curated from what column comes out clean. Everything else in
the MVP plan is contingent on this grid.

## Known confounds in the scraped set

`fetch_garments.py` pulls from the brands' public Shopify feeds. Two things about
those images will bend the results if you read the grid naively:

1. **Every reference is an on-model shot, not a flat-lay.** FASHN is told this
   explicitly (`garment_photo_type: "model"`); Kling has no such parameter and is
   weaker with on-model references. That is a handicap on Kling, not a verdict.
2. **The menswear shots have cluttered backgrounds and occluded hands** — a living
   room, an armchair, clasped hands, a turban. If the sherwani renders badly you
   cannot tell whether the model failed the garment or failed the photo. §3's catalog
   prep (background removal → white canvas) exists for exactly this. Run it on the
   garments before trusting any sherwani or menswear result.

Category labels are derived from product titles, so eyeball them. Pakistani listings
   name a whole 2-piece outfit after its shirt, so some full outfits land in `tops`.
