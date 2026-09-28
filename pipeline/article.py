"""Resolve and verify article URLs; extract title/description/date/body text from the page."""
import datetime as dt, json, logging, re, urllib.parse
from html.parser import HTMLParser
from . import net
from .textutil import clean_html

log = logging.getLogger("article")
BLOCKED = {401, 403, 429, 202, 999, 451, 400, 406}   # anti-bot answers: page probably exists
GONE = {404, 410}


# ---------------- Google News link decoding ----------------
def decode_gnews(link):
    """news.google.com/rss/articles/<id> -> publisher URL (None if it can't be decoded)."""
    try:
        aid = urllib.parse.urlparse(link).path.rstrip("/").split("/")[-1]
        r = net.get(f"https://news.google.com/rss/articles/{aid}", timeout=20)
        sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
        if not (sg and ts):
            return None
        inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                 f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts.group(1)},"{sg.group(1)}"]')
        body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner]]]))
        r2 = net.session().post("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body, timeout=20,
                                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        chunk = r2.text.split("\n\n", 1)[1]
        arr = json.loads(chunk)[:-2]
        url = json.loads(arr[0][2])[1]
        return url if url.startswith("http") else None
    except Exception as e:  # noqa: BLE001
        log.debug("gnews decode failed: %s", e)
        return None


def resolve_redirect(url):
    """Follow a redirect link (e.g. Gemini grounding redirect) to its destination without downloading."""
    try:
        r = net.session().head(url, allow_redirects=False, timeout=15)
        loc = r.headers.get("Location")
        if loc:
            return loc
        r = net.session().get(url, allow_redirects=True, timeout=20, stream=True)
        r.close()
        return r.url
    except Exception:
        return None


# ---------------- page parsing ----------------
class _Extract(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "noscript", "form", "svg", "figure", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.title, self.paras, self.times, self.ld = {}, "", [], [], []
        self._stack_skip, self._in_title, self._in_p, self._buf, self._in_ld = 0, False, False, [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if k and a.get("content"):
                self.meta.setdefault(k, a["content"])
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical" and a.get("href"):
            self.meta.setdefault("canonical", a["href"])
        elif tag == "title":
            self._in_title = True
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld = True
            self._buf = []
            return
        if tag in self.SKIP:
            self._stack_skip += 1
        elif tag in ("p", "h2", "li") and not self._stack_skip:
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self._in_ld and tag == "script":
            self.ld.append("".join(self._buf))
            self._in_ld = False
            self._buf = []
            return
        if tag in self.SKIP and self._stack_skip:
            self._stack_skip -= 1
        elif tag in ("p", "h2", "li") and self._in_p:
            t = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if len(t) > 60:
                self.paras.append(t)
            self._in_p = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ld or self._in_p:
            self._buf.append(data)


def _ld_dates(blobs):
    pub, head = None, None
    for b in blobs:
        for m in re.finditer(r'"datePublished"\s*:\s*"([^"]+)"', b):
            pub = pub or m.group(1)
        for m in re.finditer(r'"headline"\s*:\s*"([^"]+)"', b):
            head = head or m.group(1)
    return pub, head


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None


def fetch_article(url, max_bytes=1_500_000):
    """Return dict(status, ok, blocked, final_url, title, description, site_name, published, text)."""
    res = dict(url=url, status=None, ok=False, blocked=False, gone=False, final_url=url, title="", description="",
               site_name="", published=None, text="")
    try:
        r = net.get(url, timeout=(8, 15), stream=True, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        res["status"], res["final_url"] = r.status_code, r.url
        if r.status_code in GONE:
            res["gone"] = True
            r.close()
            return res
        if r.status_code in BLOCKED or r.status_code >= 500:
            res["blocked"] = True
            r.close()
            return res
        if r.status_code >= 400:
            r.close()
            return res
        ctype = r.headers.get("Content-Type", "")
        raw = b""
        for chunk in r.iter_content(65536):
            raw += chunk
            if len(raw) > max_bytes:
                break
        r.close()
        res["ok"] = True
        if "html" not in ctype and "xml" not in ctype:
            return res
        enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
        page = raw.decode(enc, "ignore")
        if re.search(r"(?i)<title>\s*(Just a moment|Attention Required|Access denied|Are you a robot)", page):
            res["ok"], res["blocked"] = False, True
            return res
        p = _Extract()
        try:
            p.feed(page)
        except Exception:
            pass
        m = p.meta
        ld_pub, ld_head = _ld_dates(p.ld)
        res["title"] = clean_html(m.get("og:title") or m.get("twitter:title") or ld_head or p.title)[:300]
        res["description"] = clean_html(m.get("og:description") or m.get("description") or m.get("twitter:description"))[:600]
        res["site_name"] = clean_html(m.get("og:site_name"))[:80]
        res["canonical"] = m.get("canonical") or m.get("og:url")
        for cand in (m.get("article:published_time"), ld_pub, m.get("datepublished"), m.get("date"),
                     m.get("parsely-pub-date"), m.get("sailthru.date"), m.get("dc.date"), m.get("citation_date"),
                     m.get("citation_publication_date"), *(p.times[:1])):
            d = parse_date(cand)
            if d:
                res["published"] = d
                break
        body = " ".join(p.paras)
        res["text"] = clean_html(body, 2500)
        return res
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        res["blocked"] = True  # timeouts / TLS quirks: treat like a bot block (feed link is still trusted)
        return res
