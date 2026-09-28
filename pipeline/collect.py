"""Collect candidate stories from all sources (RSS/Atom, Google News, HN Algolia, HF, Reddit, Techmeme)."""
import calendar, concurrent.futures as cf, datetime as dt, json, logging, math, re, time
import urllib.parse
import feedparser
from . import net
from .config import SOURCES, GNEWS_QUERIES, GNEWS_PER_QUERY, GNEWS_WEIGHT, FEED_UA, REPUTABLE_DOMAINS, LOW_QUALITY_DOMAINS
from .textutil import clean_html, looks_ai, strip_source_suffix, domain, domain_lookup

log = logging.getLogger("collect")
UTC = dt.timezone.utc


def _ts(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("created_parsed")
    if not t:
        return None
    return dt.datetime.fromtimestamp(calendar.timegm(t), UTC)


def _cand(src, title, url, published, snippet="", **extra):
    return dict(title=clean_html(title)[:300], url=url, source_id=src["id"], source_name=src["name"],
                published_at=published, snippet=clean_html(snippet, 700), weight=src["weight"],
                primary=bool(src.get("primary")), **extra)


def _in_window(ts, since, until):
    return ts is not None and since <= ts <= until + dt.timedelta(minutes=30)


def _parse_feed(url, ua=None):
    r = net.get(url, timeout=25, headers={"User-Agent": ua or FEED_UA,
                                          "Accept": "application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8"})
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    f = feedparser.parse(r.content)
    if not f.entries and f.bozo:
        raise RuntimeError(f"unparseable feed ({type(f.bozo_exception).__name__})")
    return f


def fetch_rss(src, since, until):
    try:
        f = _parse_feed(src["url"])
    except RuntimeError as e:
        if "HTTP 4" in str(e):  # some publishers block bot UAs; retry once as a browser
            f = _parse_feed(src["url"], ua=net.UA)
        else:
            raise
    out = []
    for e in f.entries:
        ts = _ts(e)
        if not _in_window(ts, since, until):
            continue
        title, link = e.get("title", ""), e.get("link", "")
        body = e.get("summary", "") or (e.get("content") or [{}])[0].get("value", "")
        if src["ai_filter"] and not looks_ai(title, body[:600]):
            continue
        if src["id"].startswith("arxiv"):
            if "announce type: new" not in body.lower() and "announce type: new" not in str(e.get("arxiv_announce_type", "")).lower():
                if e.get("arxiv_announce_type", "new") not in ("new",):
                    continue
            body = re.sub(r"^arXiv:\S+\s+Announce Type:\s*\S+\s*Abstract:\s*", "", clean_html(body))
        if link:
            out.append(_cand(src, title, link, ts, body, feed_count=len(f.entries)))
    return out


def fetch_techmeme(src, since, until):
    f = _parse_feed(src["url"])
    out = []
    for e in f.entries:
        ts = _ts(e)
        if not _in_window(ts, since, until):
            continue
        summary = e.get("summary", "")
        links = [u for u in re.findall(r'href="([^"]+)"', summary) if "techmeme.com" not in u]
        if not links:
            continue
        title = clean_html(e.get("title", ""))
        m = re.search(r"\s*\(([^()]*)\)\s*$", title)
        pub = None
        if m:
            pub = m.group(1).split("/")[-1].strip()
            title = title[: m.start()].strip()
        if src["ai_filter"] and not looks_ai(title):
            continue
        c = _cand(src, title, links[0].replace("&amp;", "&"), ts, "", via="techmeme")
        if pub:
            c["source_name"] = pub
        c["weight"] = max(src["weight"], domain_lookup(c["url"], REPUTABLE_DOMAINS, 1.3)) + 0.4  # Techmeme front page = signal
        out.append(c)
    return out


def fetch_reddit(src, since, until):
    f = _parse_feed(src["url"])
    out = []
    for e in f.entries:
        ts = _ts(e)
        if not _in_window(ts, since, until):
            continue
        content = (e.get("content") or [{}])[0].get("value", "") or e.get("summary", "")
        m = re.search(r'<a href="([^"]+)">\[link\]</a>', content)
        link = m.group(1) if m else e.get("link")
        if re.search(r"(redd\.it|reddit\.com|imgur\.com|v\.redd)", link or ""):
            link = e.get("link")
        text = re.sub(r"submitted by .*$", "", clean_html(content))
        out.append(_cand(src, e.get("title", ""), link, ts, text, via="reddit", community=True))
    return out


def fetch_hn(src, since, until):
    params = {"tags": "story", "hitsPerPage": 200,
              "numericFilters": f"created_at_i>{int(since.timestamp())},created_at_i<{int(until.timestamp()) + 1800},points>25"}
    r = net.get(src["url"], params=params, timeout=25)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        url, title = h.get("url"), h.get("title") or ""
        if not url or not looks_ai(title):
            continue
        ts = dt.datetime.fromtimestamp(h["created_at_i"], UTC)
        c = _cand(src, title, url, ts, h.get("story_text") or "", via="hn", points=h.get("points", 0),
                  hn_url=f"https://news.ycombinator.com/item?id={h['objectID']}")
        c["source_name"] = domain(url)
        pts = h.get("points", 0) or 0
        c["weight"] = domain_lookup(url, REPUTABLE_DOMAINS, 1.1) + min(1.5, math.log10(max(pts, 10)) - 1.2)
        out.append(c)
    return out


def fetch_hf_papers(src, since, until):
    r = net.get(src["url"], timeout=25)
    r.raise_for_status()
    out = []
    for p in r.json():
        pp = p.get("paper", {})
        t = p.get("publishedAt") or pp.get("submittedOnDailyAt") or pp.get("publishedAt")
        sub = pp.get("submittedOnDailyAt") or t
        try:
            ts = dt.datetime.fromisoformat(sub.replace("Z", "+00:00"))
        except Exception:
            continue
        # Daily Papers are stamped at 00:00 of the day they were featured.
        if not (since - dt.timedelta(hours=24) <= ts <= until):
            continue
        aid = pp.get("id")
        if not aid:
            continue
        up = pp.get("upvotes", 0) or 0
        c = _cand(src, pp.get("title", ""), f"https://arxiv.org/abs/{aid}", ts, pp.get("summary", "") or pp.get("ai_summary", ""),
                  upvotes=up, via="hf_papers")
        c["published_at"] = ts
        c["source_name"] = "arXiv"
        c["weight"] = src["weight"] + min(1.2, up / 40)
        out.append(c)
    out.sort(key=lambda c: -c.get("upvotes", 0))
    return out


def fetch_hf_models(src, since, until):
    r = net.get(src["url"], timeout=25)
    r.raise_for_status()
    out = []
    for m in r.json():
        try:
            ts = dt.datetime.fromisoformat(m.get("createdAt", "").replace("Z", "+00:00"))
        except Exception:
            continue
        if not (since - dt.timedelta(hours=48) <= ts <= until):  # new models trend a day or two after upload
            continue
        mid = m.get("id") or m.get("modelId")
        if not mid:
            continue
        tags = [t for t in m.get("tags", []) if not t.startswith(("region:", "endpoints", "autotrain", "deploy:"))][:12]
        c = _cand(src, f"{mid} (trending on Hugging Face)", f"https://huggingface.co/{mid}", ts,
                  f"Hugging Face model {mid}. Pipeline: {m.get('pipeline_tag')}. Likes: {m.get('likes')}. Tags: {', '.join(tags)}.",
                  via="hf_models", trending=m.get("trendingScore", 0))
        c["source_name"] = "Hugging Face"
        out.append(c)
    return out[: src.get("cap") or 8]


def gnews_url(query, region, since, until, live=True):
    if live:
        hours = max(1, math.ceil((until - since).total_seconds() / 3600))
        q = f"{query} when:{hours}h" if hours <= 48 else f"{query} when:{math.ceil(hours / 24)}d"
    else:
        q = f"{query} after:{(since - dt.timedelta(days=1)).date()} before:{(until + dt.timedelta(days=1)).date()}"
    hl, gl, ceid = ("en-IN", "IN", "IN:en") if region == "IN" else ("en-US", "US", "US:en")
    return f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"


def fetch_gnews(src, since, until):
    f = _parse_feed(src["url"], ua=net.UA)
    out = []
    for e in f.entries[: GNEWS_PER_QUERY * 2]:
        ts = _ts(e)
        if not _in_window(ts, since, until):
            continue
        s = e.get("source") or {}
        pub, pub_home = s.get("title") or "", s.get("href") or ""
        if pub_home and domain_lookup(pub_home, {d: 1 for d in LOW_QUALITY_DOMAINS}):
            continue
        title = strip_source_suffix(e.get("title", ""), pub)
        c = _cand(src, title, e.get("link"), ts, "", via="gnews", gn_link=e.get("link"), publisher_home=pub_home)
        c["source_name"] = pub or "Google News"
        c["weight"] = domain_lookup(pub_home, REPUTABLE_DOMAINS, 0.9) * 0.85
        out.append(c)
        if len(out) >= GNEWS_PER_QUERY:
            break
    return out


FETCHERS = {"rss": fetch_rss, "techmeme": fetch_techmeme, "reddit": fetch_reddit, "hn": fetch_hn,
            "hf_papers": fetch_hf_papers, "hf_models": fetch_hf_models, "gnews": fetch_gnews}


def all_sources(since, until, live=True):
    srcs = [dict(s) for s in SOURCES]
    for qid, q, region in GNEWS_QUERIES:
        srcs.append(dict(id=qid, name=f"Google News: {q}", kind="gnews", url=gnews_url(q, region, since, until, live),
                         weight=GNEWS_WEIGHT, ai_filter=False, cap=GNEWS_PER_QUERY, primary=False))
    return srcs


def collect(since, until, live=True, workers=16):
    """Return (candidates, statuses). statuses: list of dicts per source with ok/error/count."""
    srcs = all_sources(since, until, live)
    cands, statuses = [], []

    def run(src):
        t0 = time.time()
        try:
            items = FETCHERS[src["kind"]](src, since, until)
            cap = src.get("cap")
            if cap and src["kind"] not in ("gnews", "hf_models"):
                items = items[:cap] if src["kind"] != "hn" else sorted(items, key=lambda c: -c.get("points", 0))[:cap]
            return src, items, None, time.time() - t0
        except Exception as e:  # noqa: BLE001 - any source may fail; never fatal
            return src, [], f"{type(e).__name__}: {str(e)[:160]}", time.time() - t0

    with cf.ThreadPoolExecutor(workers) as ex:
        for src, items, err, secs in ex.map(run, srcs):
            statuses.append(dict(id=src["id"], name=src["name"], kind=src["kind"], url=src["url"], weight=src["weight"],
                                 ok=err is None, error=err, count=len(items), secs=round(secs, 1)))
            cands.extend(items)
            log.info("%-22s %s %3d items %s", src["id"], "OK " if err is None else "ERR", len(items), err or "")
    return cands, statuses
