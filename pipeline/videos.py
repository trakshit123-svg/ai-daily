"""Find YouTube videos for stories: official-channel RSS + YouTube search (Data API if YOUTUBE_API_KEY, else keyless).
Only trusted channels are considered, and every attached video must pass https://www.youtube.com/oembed."""
import concurrent.futures as cf, datetime as dt, json, logging, os, re, urllib.parse
import feedparser
from . import net
from .config import TRUSTED_CHANNELS, CHANNEL_RSS_HANDLES
from .textutil import title_tokens

log = logging.getLogger("videos")
UTC = dt.timezone.utc
BY_ID = {cid: (h, name) for h, cid, name in TRUSTED_CHANNELS}
BY_HANDLE = {h.lower(): (cid, name) for h, cid, name in TRUSTED_CHANNELS}


def channel_rss(since):
    out = []

    def one(h):
        cid, name = BY_HANDLE[h.lower()]
        try:
            r = net.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", timeout=20)
            if r.status_code != 200:  # the uploads feed is flaky (404s); fall back to the channel's /videos page
                return channel_page(cid, name, (dt.datetime.now(UTC) - since).total_seconds() / 86400)
            f = feedparser.parse(r.content)
            res = []
            for e in f.entries:
                t = e.get("published_parsed")
                ts = dt.datetime(*t[:6], tzinfo=UTC) if t else None
                if not ts or ts < since:
                    continue
                vid = e.get("yt_videoid") or e.get("link", "").split("v=")[-1][:11]
                res.append(dict(video_id=vid, title=e.get("title", ""), channel=name, channel_id=cid,
                                published=ts.strftime("%Y-%m-%d %H:%MZ"), desc=(e.get("summary") or "")[:300], via="rss"))
            return res
        except Exception as e:  # noqa: BLE001
            log.debug("channel rss %s failed: %s", h, e)
            return []

    with cf.ThreadPoolExecutor(8) as ex:
        for res in ex.map(one, CHANNEL_RSS_HANDLES):
            out.extend(res)
    return out


_AGO = re.compile(r"(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w|months?|mo|years?|yrs?|y)\s+ago", re.I)
_UNIT = {"s": 1 / 86400, "m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30, "y": 365}


def _age_days(text):
    m = _AGO.search(text or "")
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2).lower()
    key = "mo" if u.startswith("mo") else ("m" if u.startswith("mi") or u == "m" else u[0])
    return n * _UNIT[key]


def _yt_initial(url):
    r = net.get(url, timeout=20, headers={"Cookie": "CONSENT=YES+1; SOCS=CAI", "Accept-Language": "en-US,en"})
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
    return json.loads(m.group(1)) if m else None


def _find(x, key, out):
    if isinstance(x, dict):
        if key in x:
            out.append(x[key])
            return out
        for v in x.values():
            _find(v, key, out)
    elif isinstance(x, list):
        for v in x:
            _find(v, key, out)
    return out


def channel_page(cid, name, max_age_days):
    d = _yt_initial(f"https://www.youtube.com/channel/{cid}/videos")
    res = []
    for lv in _find(d or {}, "lockupViewModel", [])[:15]:
        try:
            md = lv["metadata"]["lockupMetadataViewModel"]
            parts = [p for row in md["metadata"]["contentMetadataViewModel"]["metadataRows"] for p in row.get("metadataParts", [])]
            age = None
            for p in parts:
                age = _age_days(p.get("accessibilityLabel") or p.get("text", {}).get("content", "")) if age is None else age
            if age is None or age > max_age_days:
                continue
            res.append(dict(video_id=lv["contentId"], title=md["title"]["content"], channel=name, channel_id=cid,
                            published=f"{age:.1f} days ago", desc="", via="channel-page"))
        except (KeyError, TypeError):
            continue
    return res


def search_keyless(query, max_age_days=3):
    d = _yt_initial("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
    if not d:
        return []
    out = []

    def walk(x):
        if isinstance(x, dict):
            if "videoRenderer" in x:
                v = x["videoRenderer"]
                runs = v.get("ownerText", {}).get("runs", [{}])
                cid = runs[0].get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId")
                age = _age_days(v.get("publishedTimeText", {}).get("simpleText", ""))
                if cid in BY_ID and age is not None and age <= max_age_days:
                    out.append(dict(video_id=v["videoId"], title="".join(t.get("text", "") for t in v.get("title", {}).get("runs", [])),
                                    channel=BY_ID[cid][1], channel_id=cid, published=v.get("publishedTimeText", {}).get("simpleText", ""),
                                    desc="".join(t.get("text", "") for t in (v.get("detailedMetadataSnippets") or [{}])[0].get("snippetText", {}).get("runs", [])),
                                    via="search"))
                return
            for vv in x.values():
                walk(vv)
        elif isinstance(x, list):
            for vv in x:
                walk(vv)
    walk(d)
    return out[:5]


def search_api(query, key, since):
    r = net.get("https://www.googleapis.com/youtube/v3/search", timeout=20, params=dict(
        part="snippet", q=query, type="video", maxResults=10, order="relevance", key=key,
        publishedAfter=(since - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), relevanceLanguage="en"))
    if r.status_code != 200:
        raise RuntimeError(f"YouTube API HTTP {r.status_code}")
    out = []
    for it in r.json().get("items", []):
        sn = it["snippet"]
        if sn.get("channelId") in BY_ID:
            out.append(dict(video_id=it["id"]["videoId"], title=sn.get("title", ""), channel=BY_ID[sn["channelId"]][1],
                            channel_id=sn["channelId"], published=sn.get("publishedAt", ""), desc=sn.get("description", ""), via="api"))
    return out[:5]


def gather_candidates(items_with_queries, since, max_searches=14):
    """items_with_queries: list of (item_id, query). Returns unique trusted-channel video candidates."""
    vids = {v["video_id"]: v for v in channel_rss(since - dt.timedelta(hours=24))}
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    queries = [(iid, q) for iid, q in items_with_queries if q][:max_searches]

    def one(q):
        try:
            return search_api(q, key, since) if key else search_keyless(q)
        except Exception as e:  # noqa: BLE001
            log.debug("youtube search failed for %r: %s", q, e)
            return []

    with cf.ThreadPoolExecutor(6) as ex:
        for (iid, q), res in zip(queries, ex.map(one, [q for _, q in queries])):
            for v in res:
                v.setdefault("for_item", iid)
                vids.setdefault(v["video_id"], v)
    log.info("video candidates: %d (%s search)", len(vids), "API" if key else "keyless")
    return list(vids.values())


def verify(video_id, channel_id=None):
    """oEmbed check. Returns dict(title, author_name, author_url) if the video exists and belongs to a trusted channel."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r = net.get("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe=""), timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:  # noqa: BLE001
        return None
    au = (d.get("author_url") or "").rstrip("/")
    handle = au.rsplit("/@", 1)[-1].lower() if "/@" in au else None
    cid = au.rsplit("/channel/", 1)[-1] if "/channel/" in au else None
    trusted = (handle and handle in BY_HANDLE) or (cid and cid in BY_ID)
    if not trusted and channel_id and channel_id in BY_ID:
        # oEmbed returns the channel's current handle; accept if the display name matches the trusted entry
        trusted = d.get("author_name", "").strip().lower() == BY_ID[channel_id][1].strip().lower()
    if not trusted:
        log.info("reject video %s: channel %s not trusted", video_id, au)
        return None
    return dict(url=url, title=d.get("title", ""), author_name=d.get("author_name", ""), author_url=au)


def heuristic_match(items, vids):
    """Conservative no-LLM matching: strong token overlap incl. a distinctive (capitalised/numeric) token."""
    matches, used = {}, set()
    for it in items:
        if it["importance"] == "Minor":
            continue
        ht = title_tokens(it["headline"])
        distinctive = {t for t in re.findall(r"\b[A-Z][A-Za-z0-9.+-]{2,}|\b\w*\d\w*\b", it["headline"])}
        distinctive = {t.lower() for t in distinctive} - {"the", "new", "how", "why", "what", "ai"}
        best, best_score = None, 0
        for v in vids:
            if v["video_id"] in used:
                continue
            vt = title_tokens(v["title"])
            inter = ht & vt
            if len(inter) < 3 or not (inter & distinctive):
                continue
            score = len(inter) / max(1, len(ht | vt))
            if score > best_score and score >= 0.3:
                best, best_score = v, score
        if best:
            matches[it["id"]] = best["video_id"]
            used.add(best["video_id"])
    return matches
