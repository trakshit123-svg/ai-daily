"""AI Daily pipeline entry point.

    python -m pipeline.run                      # today's edition (India date), window = last WINDOW_HOURS
    python -m pipeline.run --date 2026-09-27 --start 2026-09-26T00:30Z --end 2026-09-27T00:15Z   # backfill
    python -m pipeline.run --no-llm --dry-run   # heuristic-only test run that writes nothing

Exit codes: 0 = published (or kept a better existing edition), 1 = nothing publishable, 2 = bad arguments.
"""
import argparse, datetime as dt, json, logging, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ED_DIR, RUN_DIR, DB_PATH, WINDOW_HOURS, MIN_ITEMS, TARGET_MAX, MAX_CANDIDATES_FOR_LLM, CATEGORIES, IMPORTANCE
from .collect import collect
from .dedupe import cluster, select_for_llm
from .db import DB, seed_from_files
from .llm import Gemini, GeminiUnavailable
from . import editor, videos as vid
from .textutil import slugify, clean_html, url_hash, normalize_url

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import add_edition  # noqa: E402  (schema validator shared with the manual workflow)

log = logging.getLogger("run")
IST = ZoneInfo("Asia/Kolkata")
UTC = dt.timezone.utc


def parse_when(s):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def iso_z(d):
    return d.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z") if d else None


def unique_id(base, used):
    s, n = slugify(base), 2
    out = s
    while out in used:
        out = f"{s}-{n}"
        n += 1
    used.add(out)
    return out


def existing_edition(date):
    f = ED_DIR / f"{date}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def better_existing(old, new):
    """Never replace a good edition with a worse one."""
    if not old or not old.get("items"):
        return False
    om = (old.get("generator") or {}).get("mode", "manual")
    nm = new["generator"]["mode"]
    rank = {"manual": 3, "gemini": 3, "partial": 2, "fallback": 1}
    if rank.get(nm, 0) < rank.get(om, 0):
        return True
    return len(new["items"]) < 0.6 * len(old["items"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="edition date YYYY-MM-DD (default: today in India)")
    ap.add_argument("--start", help="window start (ISO, UTC if no offset)")
    ap.add_argument("--end", help="window end (ISO); default now")
    ap.add_argument("--hours", type=float, default=WINDOW_HOURS)
    ap.add_argument("--no-llm", action="store_true", help="skip Gemini entirely (fallback path)")
    ap.add_argument("--no-sweep", action="store_true", help="skip the Gemini grounded search sweep")
    ap.add_argument("--no-videos", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="don't write editions or the DB")
    ap.add_argument("--force", action="store_true", help="overwrite today's edition even if it looks better")
    ap.add_argument("--out", help="also write the edition JSON here (handy with --dry-run)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(name)-8s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    t_start = time.time()
    now = dt.datetime.now(UTC)
    end = parse_when(a.end) if a.end else now
    start = parse_when(a.start) if a.start else end - dt.timedelta(hours=a.hours)
    date = a.date or now.astimezone(IST).date().isoformat()
    live = (now - end) < dt.timedelta(hours=2)
    log.info("AI Daily edition %s | window %s -> %s (%.1fh)%s", date, iso_z(start), iso_z(end),
             (end - start).total_seconds() / 3600, "" if live else " [backfill]")

    db_path = DB_PATH if not a.dry_run else Path("/tmp/ai_daily_dryrun.db")
    if a.dry_run and DB_PATH.exists():
        db_path.write_bytes(DB_PATH.read_bytes())
    db = DB(db_path)
    seed_from_files(db)
    report = dict(date=date, window_start=iso_z(start), window_end=iso_z(end), started_at=iso_z(now), stages={})

    # 1) collect ------------------------------------------------------------------------------
    cands, statuses = collect(start, end, live=live)
    ok_sources = sum(s["ok"] for s in statuses)
    log.info("collected %d raw candidates from %d/%d sources", len(cands), ok_sources, len(statuses))
    report["sources"] = statuses
    if not a.dry_run:
        db.record_sources(statuses)

    prev_hashes = db.previous_hashes(date)
    prev_titles = db.previous_headlines(date)
    clusters = cluster(cands, prev_hashes, prev_titles)
    log.info("%d unique stories after dedupe (%d previous-edition URLs excluded)", len(clusters), len(prev_hashes))

    gem = Gemini(api_key="" if a.no_llm else None)
    stages_ok = {}
    if gem.enabled:
        gem.discover()
        if not gem.models:
            log.warning("no usable Gemini model; falling back to heuristics")
            gem.key = ""
    else:
        log.warning("Gemini disabled (%s)", "--no-llm" if a.no_llm else "GEMINI_API_KEY not set")

    # 2) Gemini grounded sweep for anything the feeds missed -------------------------------------
    if gem.enabled and not a.no_sweep and live:
        extra = editor.grounded_sweep(gem, start, end, [c["rep"]["title"] for c in clusters[:60]])
        stages_ok["sweep"] = True
        if extra:
            cands.extend(extra)
            clusters = cluster(cands, prev_hashes, prev_titles)
            log.info("%d unique stories after adding sweep results", len(clusters))
        report["stages"]["sweep_added"] = len(extra)

    if len(clusters) < 5:
        log.error("only %d candidate stories; refusing to publish", len(clusters))
        db.close(vacuum=False)
        return 1

    llm_clusters = select_for_llm(clusters, MAX_CANDIDATES_FOR_LLM)

    # 3) select -------------------------------------------------------------------------------
    picks = None
    if gem.enabled:
        try:
            picks = editor.llm_select(gem, llm_clusters, date, prev_titles[::2])
            stages_ok["select"] = bool(picks)
        except GeminiUnavailable as e:
            log.warning("Gemini select failed: %s", e)
            stages_ok["select"] = False
    if not picks:
        picks = editor.fallback_select(llm_clusters)
    elif len(picks) < 25 and len(llm_clusters) > 60:
        used = {id(cl) for p in picks for cl in p["clusters"]}
        extra = [p for p in editor.fallback_select([c for c in llm_clusters if id(c) not in used], limit=25 - len(picks))]
        for p in extra:
            p["importance"] = "Minor" if p["importance"] == "Major" else p["importance"]
            p["topup"] = True
        picks += extra
        log.info("topped up with %d heuristic picks", len(extra))

    # 4) resolve + verify links -------------------------------------------------------------------
    picks = editor.resolve_all(picks[: TARGET_MAX + 8])[:TARGET_MAX]
    for i, p in enumerate(picks, 1):
        p["key"] = f"s{i}"
    log.info("%d picks with verified links", len(picks))

    # 5) write --------------------------------------------------------------------------------
    written = {}
    if gem.enabled and picks:
        written = editor.llm_write(gem, picks)
        stages_ok["write"] = len(written) >= 0.8 * len(picks)
    used_ids, items, article_ids, members = set(), [], {}, {}
    for p in picks:
        w = written.get(p["key"])
        if w:
            w = dict(w)
            if w.get("category") not in CATEGORIES:
                w["category"] = p.get("category") or "Other"
            if w.get("importance") not in IMPORTANCE:
                w["importance"] = p.get("importance") or "Notable"
        else:
            w = editor.fallback_write(p)
        m, page = p["member"], p["page"]
        pub = m.get("published_at") or page.get("published")
        headline = clean_html(w["headline"]).strip()[:200]
        summary = clean_html(w.get("summary") or "")
        if len(summary) > 420:
            summary = summary[:417].rsplit(" ", 1)[0] + "…"
        iid = unique_id(headline, used_ids)
        src_name = m.get("source_name") or page.get("site_name") or ""
        if src_name in ("", "Hacker News") or "." in src_name and " " not in src_name:
            src_name = page.get("site_name") or src_name
        items.append({
            "id": iid, "headline": headline, "summary": summary, "category": w["category"], "importance": w["importance"],
            "source_name": src_name[:80] or "Source", "source_url": p["url"], "published_at": iso_z(pub) or date,
            "video_url": None, "video_platform": None,
            "tags": [clean_html(t)[:40] for t in (w.get("tags") or [])][:5],
        })
        members[iid] = (p, w)
    # cap Major at 12
    majors = [it for it in items if it["importance"] == "Major"]
    for it in majors[12:]:
        it["importance"] = "Notable"

    if len(items) < MIN_ITEMS:
        log.error("only %d publishable items (< MIN_ITEMS=%d); not publishing", len(items), MIN_ITEMS)
        db.close(vacuum=False)
        return 1

    # 6) highlights + videos -----------------------------------------------------------------
    used_videos = db.used_video_ids(date)
    vcands = []
    if not a.no_videos:
        queries = [(iid, w.get("video_query")) for iid, (p, w) in members.items()
                   if w.get("video_query") and next(i for i in items if i["id"] == iid)["importance"] != "Minor"]
        try:
            vcands = [v for v in vid.gather_candidates(queries, start) if v["video_id"] not in used_videos]
        except Exception as e:  # noqa: BLE001
            log.warning("video search failed: %s", e)
    highlights, vmatch = None, {}
    if gem.enabled:
        try:
            fin = editor.llm_finalize(gem, items, vcands[:80])
            highlights = [clean_html(h)[:160] for h in fin.get("highlights", []) if h.strip()][:5]
            vmatch = {x["item_id"]: x["video_id"] for x in fin.get("videos", []) if x.get("item_id") and x.get("video_id")}
            stages_ok["finalize"] = True
        except GeminiUnavailable as e:
            log.warning("Gemini finalize failed: %s", e)
            stages_ok["finalize"] = False
    if not highlights or len(highlights) < 3:
        highlights = editor.fallback_highlights(items)
    if not gem.enabled or stages_ok.get("finalize") is False:
        vmatch = vid.heuristic_match(items, vcands)
    vc_by_id = {v["video_id"]: v for v in vcands}
    video_ids, taken = {}, set()
    for it in items:
        v_id = vmatch.get(it["id"])
        if not v_id or v_id in taken or v_id not in vc_by_id:
            continue  # only videos we actually found in trusted channels
        ok = vid.verify(v_id, vc_by_id[v_id].get("channel_id"))
        if not ok:
            continue
        taken.add(v_id)
        it["video_url"], it["video_platform"] = ok["url"], "youtube"
        if not a.dry_run:
            video_ids[it["id"]] = db.upsert_video(v_id, ok["url"], ok["title"], ok["author_name"], ok["author_url"])
    log.info("videos: %d candidates, %d attached", len(vcands), len(taken))

    # 7) assemble + validate -----------------------------------------------------------------
    llm_used = any(stages_ok.get(k) for k in ("select", "write", "finalize"))
    all_ok = gem.enabled and all(stages_ok.get(k) for k in ("select", "write", "finalize"))
    mode = "gemini" if all_ok else ("partial" if llm_used else "fallback")
    ed = {
        "date": date,
        "generated_at": now.astimezone(IST).replace(microsecond=0).isoformat(),
        "highlights": highlights,
        "items": items,
        "generator": {"mode": mode, "model": ", ".join(gem.used_models) or None, "sources_ok": ok_sources,
                      "sources_total": len(statuses), "candidates": len(clusters),
                      "window": [iso_z(start), iso_z(end)], "pipeline": "ai-daily-pipeline/1"},
    }
    errs, warns = add_edition.validate(ed)
    for w_ in warns:
        log.info("validate: WARN %s", w_)
    if errs:
        for e in errs:
            log.error("validate: %s", e)
        db.close(vacuum=False)
        return 1
    report.update(mode=mode, models=gem.used_models, gemini_calls=gem.calls, items=len(items),
                  videos=sum(1 for i in items if i["video_url"]), candidates=len(clusters), raw_candidates=len(cands),
                  sources_ok=ok_sources, sources_total=len(statuses), stages=dict(report["stages"], **stages_ok))
    log.info("edition %s: %d items, %d videos, mode=%s, gemini calls=%d", date, len(items), report["videos"], mode, gem.calls)

    if a.out:
        Path(a.out).write_text(json.dumps(ed, indent=2, ensure_ascii=False) + "\n")
    if a.dry_run:
        log.info("dry run: nothing written (%.0fs)", time.time() - t_start)
        db.close(vacuum=False)
        return 0

    old = existing_edition(date)
    if not a.force and better_existing(old, ed):
        log.warning("existing %s edition (%d items, mode=%s) looks better; keeping it", date, len(old["items"]),
                    (old.get("generator") or {}).get("mode", "manual"))
        report["kept_existing"] = True
    else:
        (ED_DIR / f"{date}.json").write_text(json.dumps(ed, indent=2, ensure_ascii=False) + "\n")
        add_edition.rebuild_index()
        for p_, it in zip(picks, items):
            m = p_["member"]
            article_ids[it["id"]] = db.upsert_article(it["source_url"], m["title"], m["source_id"], m.get("source_name"),
                                                      m.get("published_at"), m.get("snippet"), p_["page"].get("status"), p_["verified"])
        db.save_edition(ed, dict(mode=mode, model=ed["generator"]["model"], candidates=len(clusters), sources_ok=ok_sources,
                                 sources_total=len(statuses), window_start=iso_z(start), window_end=iso_z(end)),
                        article_ids, video_ids)
    # remember what we considered so tomorrow's run can dedupe (candidate rows are pruned after 10 days)
    for cl in llm_clusters:
        r = cl["rep"]
        if not (r.get("gn_link") and r["url"] == r["gn_link"]):
            db.upsert_article(r["url"], r["title"], r["source_id"], r.get("source_name"), r.get("published_at"), None, None, "candidate")
    db.conn.commit()
    db.prune()
    db.close()

    report["finished_at"] = iso_z(dt.datetime.now(UTC))
    report["seconds"] = round(time.time() - t_start)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / f"{date}.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    for f in sorted(RUN_DIR.glob("*.json"))[:-14]:
        f.unlink()
    log.info("done in %.0fs", time.time() - t_start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
