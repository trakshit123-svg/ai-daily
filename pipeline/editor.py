"""Editorial layer: Gemini (grounded sweep, select/cluster, write, finalize) with a heuristic fallback for every stage."""
import concurrent.futures as cf, datetime as dt, logging, math, re
from .config import CATEGORIES, IMPORTANCE, TARGET_MIN, TARGET_MAX, REPUTABLE_DOMAINS
from .llm import GeminiUnavailable, S, OBJ, ARR
from .textutil import (clean_html, first_sentences, heuristic_category, heuristic_tags, is_dup_title, title_tokens,
                       domain, domain_lookup, looks_ai)
from . import article as art

log = logging.getLogger("editor")
UTC = dt.timezone.utc

SYSTEM = """You are the editor of "AI Daily", a factual daily digest of artificial-intelligence news.
Hard rules:
- Use ONLY facts contained in the source text supplied to you. Never add facts, numbers, names, dates or links that are not in that text.
- If a claim is reported rather than confirmed, hedge it ("reportedly", "according to <outlet>").
- Plain, specific, non-clickbait English. No emojis. No marketing language."""

IMPORTANCE_GUIDE = """Importance:
- Major: frontier model or flagship product launch, big policy/legal decision, serious safety incident, >= $1B deal/round, or anything the whole AI world will talk about. Usually 6-12 per day.
- Notable: meaningful launches, $50M+ rounds, strong open-weight releases, important papers, significant India AI news.
- Minor: small tools, niche papers, small rounds, leaks, follow-ups, commentary with some news value."""

CATEGORY_GUIDE = ("Categories (pick exactly one): " + "; ".join(CATEGORIES) +
                  ". Robotics usually goes to Other unless it's an open model (Open source). Chips, data centers and compute deals go to Hardware & chips.")


def _fmt_ts(d):
    return d.astimezone(UTC).strftime("%Y-%m-%d %H:%MZ") if d else "unknown"


# =====================================================================================
# 1) Grounded deep sweep (Gemini + Google Search) -> extra candidates with verified pages
# =====================================================================================
def grounded_sweep(gem, since, until, known_titles, max_items=25):
    prompt = f"""Search the web for important artificial-intelligence news published between {_fmt_ts(since)} and {_fmt_ts(until)} (UTC).
Cover: AI labs (OpenAI, Google/DeepMind, Anthropic, Meta, Microsoft, xAI, Mistral, DeepSeek, Alibaba/Qwen, Nvidia), model and product launches,
research papers, open-source releases, funding rounds and acquisitions, policy/regulation/lawsuits/safety, chips and data centers, and India's AI ecosystem.
We already have these stories, so focus on ones that are NOT in this list:
{chr(10).join('- ' + t for t in known_titles[:60])}

Return a plain bulleted list of up to {max_items} distinct stories, one line each: "<headline> — <publisher> — <date>". Only include stories whose articles were published in that window."""
    try:
        text, gm, model = gem.generate(prompt, search=True, temperature=0.3, max_tokens=4096, label="sweep")
    except GeminiUnavailable as e:
        log.warning("grounded sweep skipped: %s", e)
        return []
    chunks = [c.get("web", {}) for c in gm.get("groundingChunks", []) if c.get("web", {}).get("uri")]
    log.info("sweep: %d grounding sources", len(chunks))

    def resolve(ch):
        url = ch["uri"]
        if "grounding-api-redirect" in url or "vertexaisearch" in url:
            url = art.resolve_redirect(url)
        if not url or "vertexaisearch" in url:
            return None
        page = art.fetch_article(url)
        if not page["ok"] or not page["title"]:
            return None  # search-found links must load for us, we have no feed text to fall back on
        pub = page["published"]
        if not pub or not (since - dt.timedelta(hours=6) <= pub <= until + dt.timedelta(hours=1)):
            return None
        if not looks_ai(page["title"], page["description"]):
            return None
        site = page["site_name"] or ch.get("title") or domain(url)
        return dict(title=page["title"], url=page["final_url"] or url, source_id="gemini-search", source_name=site,
                    published_at=pub, snippet=page["description"] or page["text"][:500], primary=False,
                    weight=domain_lookup(url, REPUTABLE_DOMAINS, 1.1), page=page, via="gemini-search")

    out = []
    with cf.ThreadPoolExecutor(8) as ex:
        for c in ex.map(resolve, chunks[:40]):
            if c:
                out.append(c)
    log.info("sweep: %d verified in-window candidates", len(out))
    return out


# =====================================================================================
# 2) Selection / clustering
# =====================================================================================
SELECT_SCHEMA = OBJ({
    "picks": ARR(OBJ({
        "primary_id": S("STRING", description="candidate id of the best source for this story"),
        "related_ids": ARR(S("STRING"), description="other candidate ids covering the same event"),
        "importance": S("STRING", enum=IMPORTANCE),
        "category": S("STRING", enum=CATEGORIES),
    }, required=["primary_id", "related_ids", "importance", "category"]))
})


def _cand_line(cl):
    r = cl["rep"]
    snip = clean_html(r.get("snippet", ""), 220)
    extra = f" | covered by {cl['coverage']} sources" if cl["coverage"] > 1 else ""
    pts = f" | HN {r['points']} pts" if r.get("points") else ""
    return f"{cl['cid']} | {_fmt_ts(r.get('published_at'))} | {r['source_name']}{extra}{pts} | {r['title']}" + (f" — {snip}" if snip else "")


def llm_select(gem, clusters, edition_date, prev_headlines):
    n = len(clusters)
    lo, hi = min(TARGET_MIN, max(10, n // 3)), TARGET_MAX
    prompt = f"""Build the AI Daily edition for {edition_date}. Below are {n} candidate items gathered from feeds in the last ~24 hours
(format: id | published | source | title — snippet).

Tasks:
1. Group candidates that cover the SAME event (put the others in related_ids). Each event appears once.
2. Choose the {lo}-{hi} most newsworthy AI stories (aim for ~35-45 when there is enough real news). Prefer: primary sources (the company/lab/paper itself)
   or reputable outlets; genuine news over opinion, explainers, listicles, stock tips, product deals, or generic "AI is changing X" features.
   Keep a healthy mix across categories; include India AI stories when present (aim for 2+); include a few notable research papers and open-source releases.
3. primary_id must be the single best candidate to link for that event.
4. Skip anything that repeats these stories from recent editions unless the candidate reports a clearly NEW development:
{chr(10).join('   - ' + h for h in prev_headlines[:90])}

{IMPORTANCE_GUIDE}
{CATEGORY_GUIDE}

Order picks by editorial priority (biggest first).

CANDIDATES:
{chr(10).join(_cand_line(cl) for cl in clusters)}"""
    data, _, model = gem.generate(prompt, system=SYSTEM, schema=SELECT_SCHEMA, temperature=0.2, max_tokens=12000, label="select")
    by_id = {cl["cid"]: cl for cl in clusters}
    picks, used = [], set()
    for p in (data or {}).get("picks", []):
        pid = p.get("primary_id")
        if pid not in by_id or pid in used:
            continue
        rel = [x for x in p.get("related_ids", []) if x in by_id and x not in used and x != pid]
        used.update([pid, *rel])
        picks.append(dict(clusters=[by_id[pid]] + [by_id[x] for x in rel],
                          importance=p.get("importance") if p.get("importance") in IMPORTANCE else "Notable",
                          category=p.get("category") if p.get("category") in CATEGORIES else None))
    log.info("select: %d picks from %d candidates (%s)", len(picks), n, model)
    return picks


def _caps(t):
    return {w.lower().strip("’'s") for w in re.findall(r"\b[A-Z][A-Za-z0-9.’'-]+", t or "")} - {"the", "a", "how", "why", "what", "ai", "a.i.", "as", "in", "on"}


def loose_same(a, b):
    """Same-event heuristic for the no-LLM path: shared named entities + some word overlap."""
    ta, tb = title_tokens(a), title_tokens(b)
    inter = ta & tb
    ents = _caps(a) & _caps(b)
    jac = len(inter) / max(1, len(ta | tb))
    return (len(inter) >= 3 and jac >= 0.3) or (len(ents) >= 2 and len(inter) >= 3 and jac >= 0.18)


def fallback_select(clusters, limit=40):
    """Heuristic selection: loose-merge same-event clusters, then take the best with category/India diversity."""
    merged = []
    for cl in clusters:  # clusters are sorted by score
        for m in merged:
            if loose_same(cl["rep"]["title"], m[0]["rep"]["title"]):
                m.append(cl)
                break
        else:
            merged.append([cl])
    picks, per_cat = [], {}
    india = [g for g in merged if re.search(r"\bIndia|Indian|Bengaluru|Mumbai|Delhi|Sarvam|Krutrim|IndiaAI\b", g[0]["rep"]["title"])]
    order = merged[:]
    for g in india[:3]:  # guarantee some India coverage
        order.remove(g)
        order.insert(min(len(order), 12), g)
    for g in order:
        r = g[0]["rep"]
        if r.get("community") and len(g) == 1 and g[0]["coverage"] < 2:
            continue
        cat = heuristic_category(r["title"], r.get("snippet", ""), r["source_id"])
        if per_cat.get(cat, 0) >= 12:
            continue
        per_cat[cat] = per_cat.get(cat, 0) + 1
        picks.append(dict(clusters=g, importance=None, category=cat))
        if len(picks) >= limit:
            break
    # importance from rank / coverage
    for i, p in enumerate(picks):
        cov = max(c["coverage"] for c in p["clusters"]) + len(p["clusters"]) - 1
        p["importance"] = "Major" if (i < 10 and cov >= 3) else ("Notable" if i < 28 or cov >= 2 else "Minor")
    return picks


# =====================================================================================
# 3) Resolve + verify links for picks, fetch article text
# =====================================================================================
def resolve_pick(pick):
    """Choose a verified URL for the pick; attach page info. Returns pick or None."""
    members = [m for cl in pick["clusters"] for m in cl["members"]]
    members.sort(key=lambda m: (1 if m.get("primary") else 0, 0 if m.get("gn_link") else 1, m["weight"]), reverse=True)
    tried = 0
    for m in members:
        if tried >= 4:
            break
        url = m["url"]
        if m.get("gn_link") and url == m["gn_link"]:
            dec = art.decode_gnews(m["gn_link"])
            if not dec:
                continue
            url = m["url"] = dec
        tried += 1
        page = m.get("page") or art.fetch_article(url)
        if page["gone"]:
            log.info("drop 404/410: %s", url)
            continue
        if not page["ok"] and not page["blocked"]:
            continue
        if not page["ok"] and m.get("via") == "gemini-search":
            continue
        pick["member"], pick["page"], pick["url"] = m, page, url
        pick["verified"] = "ok" if page["ok"] else f"blocked:{page['status']}"
        return pick
    return None


def resolve_all(picks, workers=12):
    out = []
    with cf.ThreadPoolExecutor(workers) as ex:
        for p in ex.map(resolve_pick, picks):
            if p:
                out.append(p)
    # final URL dedupe (Google News links may decode to a URL we already have)
    seen, uniq = set(), []
    from .textutil import url_hash
    for p in out:
        h = url_hash(p["url"])
        if h in seen:
            continue
        seen.add(h)
        uniq.append(p)
    return uniq


def source_text(p, limit=1800):
    m, page = p["member"], p["page"]
    parts = []
    if page.get("title") and page["title"] != m["title"]:
        parts.append("Page title: " + page["title"])
    if m.get("snippet"):
        parts.append("Feed summary: " + clean_html(m["snippet"], 600))
    if page.get("description") and page["description"] not in (m.get("snippet") or ""):
        parts.append("Page description: " + page["description"])
    if page.get("text"):
        parts.append("Article text: " + page["text"][:limit])
    rel = [x["title"] for cl in p["clusters"] for x in cl["members"] if x is not m][:4]
    if rel:
        parts.append("Other outlets' headlines for the same event: " + " | ".join(rel))
    return "\n".join(parts) or "(no text available beyond the headline)"


# =====================================================================================
# 4) Writing
# =====================================================================================
WRITE_SCHEMA = OBJ({
    "items": ARR(OBJ({
        "id": S("STRING"),
        "headline": S("STRING", description="specific, factual, <= 110 characters"),
        "summary": S("STRING", description="1-2 sentences, <= 320 characters, only facts from the source text"),
        "category": S("STRING", enum=CATEGORIES),
        "importance": S("STRING", enum=IMPORTANCE),
        "tags": ARR(S("STRING"), description="2-5 short entity/topic tags"),
        "video_query": S("STRING", description="3-6 word YouTube search query for this exact story, or empty"),
    }, required=["id", "headline", "summary", "category", "importance", "tags", "video_query"]))
})


def llm_write(gem, picks, batch=20):
    out = {}
    for i in range(0, len(picks), batch):
        chunk = picks[i:i + batch]
        blocks = []
        for p in chunk:
            m = p["member"]
            blocks.append(f"""### id: {p['key']}
Source: {m['source_name']} ({domain(p['url'])}) | published {_fmt_ts(m.get('published_at') or p['page'].get('published'))}
Suggested category: {p.get('category') or 'decide'} | suggested importance: {p.get('importance') or 'decide'}
Original headline: {m['title']}
{source_text(p)}""")
        prompt = f"""Write AI Daily entries for the {len(chunk)} stories below. For each id return:
- headline: specific and factual (<= 110 chars), rewritten in your words, no clickbait.
- summary: 1-2 plain sentences (<= 320 chars) saying what happened and why it matters, using ONLY facts present in that story's text.
  If the text is thin, stay close to the headline rather than guessing. Hedge reported/unconfirmed claims.
- category, importance (you may adjust the suggestions), 2-5 tags (company/model names, topics like "India", "agents").
- video_query: a short YouTube search query that would find a video about this exact story (empty if unlikely to have one).
{IMPORTANCE_GUIDE}
{CATEGORY_GUIDE}

{chr(10).join(blocks)}"""
        try:
            data, _, model = gem.generate(prompt, system=SYSTEM, schema=WRITE_SCHEMA, temperature=0.3, max_tokens=16000,
                                          label=f"write {i // batch + 1}")
        except GeminiUnavailable as e:
            log.warning("write batch %d failed: %s", i // batch + 1, e)
            continue
        for it in (data or {}).get("items", []):
            if it.get("id") and it.get("headline"):
                out[it["id"]] = it
    return out


def fallback_write(p):
    m, page = p["member"], p["page"]
    title = clean_html(m["title"])
    if m.get("via") == "hf_models":
        title = f"Trending on Hugging Face: {m['url'].split('huggingface.co/')[-1]}"
    text = page.get("description") or m.get("snippet") or page.get("text") or ""
    summary = first_sentences(text, 300)
    if summary and summary.lower().startswith(title.lower()[:40]):
        summary = first_sentences(text[len(title):], 300) or summary
    cat = p.get("category") or heuristic_category(title, text, m["source_id"])
    return dict(headline=title[:160], summary=summary, category=cat, importance=p.get("importance") or "Minor",
                tags=heuristic_tags(title, text), video_query=" ".join(list(title_tokens(title))[:5]))


# =====================================================================================
# 5) Finalize: highlights + video matching
# =====================================================================================
FINAL_SCHEMA = OBJ({
    "highlights": ARR(S("STRING"), description="3-5 bullets, each <= 140 characters"),
    "videos": ARR(OBJ({"item_id": S("STRING"), "video_id": S("STRING")}, required=["item_id", "video_id"])),
})


def llm_finalize(gem, items, video_cands):
    lines = [f"{it['id']} | {it['importance']} | {it['headline']} — {it['summary']}" for it in items]
    vlines = [f"{v['video_id']} | {v['channel']} | {v.get('published') or ''} | {v['title']}" + (f" — {v['desc'][:160]}" if v.get("desc") else "")
              for v in video_cands]
    prompt = f"""Final pass on today's AI Daily edition.

1. highlights: 3-5 short bullets (<= 140 chars each) covering the day's biggest threads, based only on the stories below.
   Include an India bullet if there is notable India news.
2. videos: match stories to videos from the candidate list ONLY when the video is clearly about that exact story/announcement
   (same product, model, event or company news). Generic or loosely related videos must NOT be matched. Each video at most once.
   It's fine to return an empty list.

STORIES (id | importance | headline — summary):
{chr(10).join(lines)}

VIDEO CANDIDATES (video_id | channel | published | title — description):
{chr(10).join(vlines) if vlines else '(none)'}"""
    data, _, _ = gem.generate(prompt, system=SYSTEM, schema=FINAL_SCHEMA, temperature=0.2, max_tokens=4000, label="finalize")
    return data or {}


def fallback_highlights(items):
    top = [i for i in items if i["importance"] == "Major"] + [i for i in items if i["importance"] == "Notable"]
    hl = []
    for it in top:
        h = it["headline"]
        if len(h) > 140:
            h = h[:137].rsplit(" ", 1)[0] + "…"
        if not any(is_dup_title(h, x) for x in hl):
            hl.append(h)
        if len(hl) >= 4:
            break
    while len(hl) < 3 and len(items) > len(hl):
        hl.append(items[len(hl)]["headline"][:140])
    return hl
