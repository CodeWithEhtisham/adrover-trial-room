#!/usr/bin/env python3
"""Pull test garments from Pakistani brands' public Shopify product feeds.

These are the brands' copyrighted product photos, fetched for an internal
compatibility spike only. The client demo uses the client's own catalog (§9).

    python fetch_garments.py --per-category 8
    python fetch_garments.py --dry-run          # see what it would take, download nothing
"""
import argparse, json, pathlib, re, time, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).parent
GARMENTS = ROOT / "garments"
MANIFEST = ROOT / "garments" / "manifest.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; AdRover-spike/1.0; internal evaluation)"}

# Regional: the garments §5 predicts will fail.
BRANDS = ["generation.com.pk", "nishatlinen.com", "limelight.pk",
          "gulahmedshop.com", "bonanzasatrangi.com", "sanasafinaz.com"]

# Western-cut controls. Without these the grid has no baseline — a bad saree result
# means nothing if you cannot see a plain tee come out clean on the same person.
CONTROLS = ["outfitters.com.pk", "breakout.com.pk"]

# The womenswear brands above stock zero sherwani. Menswear is a separate catalog.
BRANDS += ["amiradnan.com", "junaidjamshed.com"]

# Pakistani brand catalogs are dominated by UNSTITCHED lawn — bolts of printed fabric,
# not garments. A try-on model cannot wear fabric, so these are the biggest source of
# junk in the test set and get dropped before anything else.
REJECT = re.compile(
    r"unstitch|un-stitch|fabric|yardage|bedding|bed sheet|duvet|cushion|towel|"
    r"fragrance|perfume|attar|lotion|cream|bag|clutch|purse|shoe|sandal|khussa|heel|"
    r"jewel|earring|neckl|bangle|mask|scarf only|stole|socks|scrunchie|gift card|home|"
    r"paranda|hair|braid|belt|cap|tie|wallet|sunglass|watch|fragrance|deo|"
    r"girls|boys|\bkids\b|infant|toddler|junior|baby",   # adult volunteers wear adult clothes
    re.I)

# Category per the model taxonomy (tops / bottoms / one-pieces), most specific first.
RULES = [
    ("one-pieces", r"saree|sari|lehenga|gharara|sharara|maxi|gown|frock|abaya|jumpsuit|dress|"
                   r"kaftan|angrakha|sherwani|anarkali|3 piece|three piece|2 piece|two piece|suit"),
    ("bottoms",    r"trouser|shalwar|salwar|palazzo|pant|capri|culotte|tights|churidar|jeans|"
                   r"short|jort|skirt"),
    ("tops",       r"kurti|kurta|shirt|top|blouse|tee|t-shirt|tunic|waistcoat|jacket|cardigan|"
                   r"hoodie|sweatshirt|polo"),
]

# §5's out-of-distribution list. Anything matching is prioritised — it is the whole
# reason the spike exists.
PRIORITY = re.compile(r"dupatta|saree|sari|sherwani|lehenga|gharara|sharara|kameez|"
                      r"anarkali|angrakha|kurta|shalwar", re.I)

# §5 names these specifically. Priority sorting alone buried dupatta at 1 of 24 while 29
# were available, so each gets a guaranteed quota before the categories are filled.
# A control must be a garment the models are known to handle, or it is not a baseline.
# §8-5: open and layered items are weak across the board, so they make bad controls.
CONTROL_PREF = re.compile(r"plain|basic|solid|tee|t-shirt|crew|polo|jeans|denim|"
                          r"straight|slim fit|midi|shift|shirt", re.I)

TARGETS = {"dupatta": 4, "saree|sari\\b": 2, "sherwani": 3,
           "lehenga|gharara|sharara": 1, "anarkali|angrakha": 1}


def get(url, timeout=25):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def slug(s, n=48):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:n]


def classify(text):
    for cat, pat in RULES:
        if re.search(pat, text, re.I):
            return cat
    return None


def products(domain, pages=3):
    for page in range(1, pages + 1):
        try:
            data = json.loads(get(f"https://{domain}/products.json?limit=250&page={page}"))
        except Exception as e:
            print(f"  ! {domain} page {page}: {e}")
            return
        if not data.get("products"):
            return
        yield from data["products"]
        time.sleep(1.0)   # polite; these are someone's shop


def collect():
    found = []
    seen = set()
    for domain in BRANDS + CONTROLS:
        origin = "control" if domain in CONTROLS else "regional"
        brand = domain.split(".")[0]
        n = 0
        for p in products(domain):
            text = " ".join([p.get("title", ""), p.get("product_type", ""),
                             " ".join(p.get("tags", []))])
            if REJECT.search(text) or not p.get("images"):
                continue
            cat = classify(text)
            if not cat:
                continue
            src = p["images"][0]["src"].split("?")[0]
            if src in seen:
                continue
            seen.add(src)
            found.append({
                "brand": brand, "title": p["title"].strip(), "category": cat,
                "origin": origin,
                "priority": origin == "regional" and bool(PRIORITY.search(text)),
                "src": f"{src}?width=1400",
                "product_url": f"https://{domain}/products/{p['handle']}",
                "name": f"{brand}_{slug(p['title'])}",
            })
            n += 1
        print(f"  {brand:<16} {origin:<8} {n} candidates")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=6, help="regional garments per category")
    ap.add_argument("--controls", type=int, default=2, help="western controls per category")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("fetching product feeds…")
    found = collect()

    # Out-of-distribution garments first, then round-robin across brands so one
    # catalog cannot fill a whole category.
    def round_robin(pool):
        """Interleave brands so one catalog cannot fill a whole category."""
        by_brand, order = {}, []
        for f in sorted(pool, key=lambda f: (not f["priority"], f["brand"])):
            by_brand.setdefault(f["brand"], []).append(f)
        while any(by_brand.values()):
            for b in list(by_brand):
                if by_brand[b]:
                    order.append(by_brand[b].pop(0))
        return order

    picked, manifest = [], []
    for term, quota in TARGETS.items():
        hits = round_robin([f for f in found if re.search(term, f["title"], re.I)])[:quota]
        picked += hits
        if len(hits) < quota:
            print(f"  ! only {len(hits)}/{quota} for '{term}' — none in these catalogs")
    taken = {f["src"] for f in picked}

    for cat, _ in RULES:
        in_cat = [f for f in found if f["category"] == cat and f["src"] not in taken]
        picked += round_robin([f for f in in_cat if f["origin"] == "regional"])[:args.per_category]
        ctl = sorted([f for f in in_cat if f["origin"] == "control"],
                     key=lambda f: not CONTROL_PREF.search(f["title"]))
        picked += round_robin(ctl[:12])[:args.controls]

    ood = sum(f["priority"] for f in picked)
    ctl = sum(f["origin"] == "control" for f in picked)
    print(f"\nselected {len(picked)} of {len(found)} candidates"
          f" — {ood} out-of-distribution, {ctl} controls")
    for f in picked:
        flag = "*" if f["priority"] else ("." if f["origin"] == "control" else " ")
        print(f"  {flag} {f['category']:<11} {f['name']}")
    if args.dry_run:
        return

    for f in picked:
        prefix = "control_" if f["origin"] == "control" else ""
        dest = GARMENTS / f["category"] / f"{prefix}{f['name']}.jpg"
        if not dest.exists():
            try:
                dest.write_bytes(get(f["src"]))
            except Exception as e:
                print(f"  ! {f['name']}: {e}")
                continue
            time.sleep(0.4)
        manifest.append({**f, "file": str(dest.relative_to(ROOT))})

    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"\n{len(manifest)} images in {GARMENTS}/  · provenance in {MANIFEST.name}")
    print("Review by eye and delete anything that is fabric rather than a worn garment.")


if __name__ == "__main__":
    main()
