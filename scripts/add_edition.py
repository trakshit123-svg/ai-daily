#!/usr/bin/env python3
"""Validate an AI Daily edition JSON and publish it.

Usage:
  python3 scripts/add_edition.py path/to/edition.json            # validate + write editions/<date>.json, latest.json, index.json
  python3 scripts/add_edition.py path/to/edition.json --check    # validate only
  python3 scripts/add_edition.py --check-urls path/to/edition.json  # also HEAD/GET every source_url and video (network)
  python3 scripts/add_edition.py --rebuild-index                 # rebuild index.json/latest.json from files on disk

Only the Python standard library is used.
"""
import argparse, datetime as dt, json, re, sys, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ED_DIR = ROOT / "editions"
CATEGORIES = ["Model releases", "Research & papers", "Tools & products", "Big Tech moves", "Open source",
              "Funding & startups", "Policy & safety", "Hardware & chips", "Other"]
IMPORTANCE = ["Major", "Notable", "Minor"]
PLATFORMS = ["youtube", "x", "vimeo", "other"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def parse_ts(s):
    if DATE_RE.match(s):
        return dt.date.fromisoformat(s)
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def validate(ed):
    errs, warns = [], []
    def need(cond, msg):
        if not cond: errs.append(msg)
    need(isinstance(ed, dict), "edition must be an object")
    if errs: return errs, warns
    for k in ["date", "generated_at", "highlights", "items"]:
        need(k in ed, f"missing top-level key '{k}'")
    if errs: return errs, warns
    need(isinstance(ed["date"], str) and DATE_RE.match(ed["date"]), "date must be YYYY-MM-DD")
    try: parse_ts(ed["generated_at"])
    except Exception: errs.append("generated_at must be ISO-8601")
    hl = ed["highlights"]
    need(isinstance(hl, list) and all(isinstance(h, str) and h.strip() for h in hl), "highlights must be a list of non-empty strings")
    if isinstance(hl, list) and not (3 <= len(hl) <= 5): warns.append(f"highlights should have 3-5 bullets (has {len(hl)})")
    items = ed["items"]
    need(isinstance(items, list) and items, "items must be a non-empty list")
    if not isinstance(items, list): return errs, warns
    if not (25 <= len(items) <= 50): warns.append(f"aim for 25-50 items (has {len(items)})")
    ids, urls, heads = set(), {}, {}
    for n, it in enumerate(items):
        p = f"items[{n}]"
        if not isinstance(it, dict): errs.append(f"{p} must be an object"); continue
        for k in ["id", "headline", "summary", "category", "importance", "source_name", "source_url", "published_at", "video_url", "video_platform", "tags"]:
            if k not in it: errs.append(f"{p} missing '{k}'")
        iid = it.get("id")
        if not isinstance(iid, str) or not re.match(r"^[a-z0-9][a-z0-9-]*$", iid or ""): errs.append(f"{p}.id must be a lowercase slug")
        elif iid in ids: errs.append(f"{p}.id duplicate '{iid}'")
        ids.add(iid)
        if not (isinstance(it.get("headline"), str) and it["headline"].strip()): errs.append(f"{p}.headline required")
        if not isinstance(it.get("summary"), str): errs.append(f"{p}.summary must be a string")
        elif len(it["summary"]) > 420: warns.append(f"{p}.summary is long ({len(it['summary'])} chars); keep to 1-2 sentences")
        if it.get("category") not in CATEGORIES: errs.append(f"{p}.category '{it.get('category')}' not in {CATEGORIES}")
        if it.get("importance") not in IMPORTANCE: errs.append(f"{p}.importance must be one of {IMPORTANCE}")
        su = it.get("source_url")
        if not (isinstance(su, str) and su.startswith(("http://", "https://"))): errs.append(f"{p}.source_url must be http(s)")
        else:
            key = su.split("#")[0].rstrip("/")
            if key in urls: errs.append(f"{p}.source_url duplicates items[{urls[key]}]")
            urls[key] = n
        if not (isinstance(it.get("source_name"), str) and it["source_name"].strip()): errs.append(f"{p}.source_name required")
        try: parse_ts(it.get("published_at") or "")
        except Exception: errs.append(f"{p}.published_at must be ISO-8601 datetime or YYYY-MM-DD")
        vu, vp = it.get("video_url"), it.get("video_platform")
        if vu is None:
            if vp is not None: errs.append(f"{p}.video_platform must be null when video_url is null")
        else:
            if not (isinstance(vu, str) and vu.startswith("https://")): errs.append(f"{p}.video_url must be https or null")
            if vp not in PLATFORMS: errs.append(f"{p}.video_platform must be one of {PLATFORMS}")
        if not (isinstance(it.get("tags"), list) and all(isinstance(t, str) for t in it["tags"])): errs.append(f"{p}.tags must be a list of strings")
        h = re.sub(r"[^a-z0-9 ]", "", (it.get("headline") or "").lower())
        if h in heads: warns.append(f"{p} headline looks duplicated with items[{heads[h]}]")
        heads[h] = n
    return errs, warns


def check_urls(ed):
    """Network check: every source_url should answer <400; YouTube videos must pass oEmbed."""
    bad = []
    for it in ed["items"]:
        for kind, u in (("source", it["source_url"]), ("video", it.get("video_url"))):
            if not u: continue
            target = u
            if kind == "video" and ("youtube.com" in u or "youtu.be" in u):
                target = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(u, safe="")
            try:
                r = urllib.request.urlopen(urllib.request.Request(target, headers={"User-Agent": UA}), timeout=25)
                status = r.status
            except Exception as e:
                status = getattr(e, "code", str(e)[:60])
            ok = isinstance(status, int) and status < 400
            # Some publishers (Reuters, Meta, OpenAI) block scripted requests with 401/403 even though the page exists.
            print(f"{'OK ' if ok else 'BAD'} {status} {kind:6} {it['id']}: {u}")
            if not ok: bad.append((it["id"], kind, u, status))
    return bad


def previous_urls(exclude_date):
    seen = {}
    for f in sorted(ED_DIR.glob("????-??-??.json")):
        if f.stem == exclude_date: continue
        try:
            for it in json.loads(f.read_text())["items"]:
                seen[it["source_url"].split("#")[0].rstrip("/")] = f.stem
        except Exception: pass
    return seen


def rebuild_index():
    dates = sorted((f.stem for f in ED_DIR.glob("????-??-??.json")), reverse=True)
    if not dates: sys.exit("no editions found")
    (ED_DIR / "index.json").write_text(json.dumps({"latest": dates[0], "dates": dates}, indent=2) + "\n")
    (ED_DIR / "latest.json").write_text((ED_DIR / f"{dates[0]}.json").read_text())
    print(f"index.json: {len(dates)} edition(s); latest = {dates[0]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edition", nargs="?")
    ap.add_argument("--check", action="store_true", help="validate only, don't write")
    ap.add_argument("--check-urls", action="store_true", help="also verify URLs over the network")
    ap.add_argument("--rebuild-index", action="store_true")
    a = ap.parse_args()
    ED_DIR.mkdir(exist_ok=True)
    if a.rebuild_index and not a.edition:
        return rebuild_index()
    if not a.edition: ap.error("edition path required")
    ed = json.loads(Path(a.edition).read_text())
    errs, warns = validate(ed)
    if isinstance(ed, dict) and isinstance(ed.get("items"), list) and ed.get("date"):
        prev = previous_urls(ed["date"])
        for it in ed["items"]:
            k = str(it.get("source_url", "")).split("#")[0].rstrip("/")
            if k in prev: warns.append(f"'{it.get('id')}' source_url already used in edition {prev[k]} (dedupe!)")
    for w in warns: print("WARN ", w)
    for e in errs: print("ERROR", e)
    if errs: sys.exit(f"\n{len(errs)} error(s); edition not written.")
    cats = {}
    for it in ed["items"]: cats[it["category"]] = cats.get(it["category"], 0) + 1
    print(f"Valid: {ed['date']} · {len(ed['items'])} items · {sum(1 for i in ed['items'] if i['video_url'])} videos · {cats}")
    if a.check_urls:
        bad = check_urls(ed)
        print(f"{len(bad)} URL(s) failed network check (401/403 from bot-blocking publishers may be false alarms).")
    if a.check: return
    out = ED_DIR / f"{ed['date']}.json"
    out.write_text(json.dumps(ed, indent=2, ensure_ascii=False) + "\n")
    print("wrote", out.relative_to(ROOT))
    rebuild_index()


if __name__ == "__main__":
    main()
