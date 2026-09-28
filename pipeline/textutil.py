"""Text helpers: URL normalisation, HTML cleaning, fuzzy titles, AI relevance, heuristic categorisation."""
import hashlib, html, re, unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_PARAMS = re.compile(r"^(utm_|mc_|fbclid|gclid|dclid|yclid|igshid|ref$|ref_src|ref_url|cmpid|ncid|taid|"
                             r"src$|source$|sr_share|smid|smtyp|guccounter|guce_|mbid|oc$|at_|cid$|__twitter|"
                             r"s_cid|share|pwapi_token|itid|soc_src|soc_trk|intcmp|ito$|trk|tracking|campaign|rss$|feed$|output$|tpcc)", re.I)


def normalize_url(url):
    """Canonical form used for dedupe: https, lowercase host without www, no tracking params/fragment/trailing slash."""
    if not url:
        return ""
    url = url.strip()
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if host == "arxiv.org":
        path = re.sub(r"^/(pdf|html)/", "/abs/", path)
        path = re.sub(r"v\d+$", "", path).removesuffix(".pdf")
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if not TRACKING_PARAMS.match(k)]
    if host.endswith("youtube.com") and path == "/watch":
        q = [(k, v) for k, v in q if k == "v"]
    query = urlencode(sorted(q))
    return urlunsplit(("https", host, path, query, ""))


def url_hash(url):
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:16]


def domain(url):
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def domain_lookup(url, table, default=None):
    """Find the value for the longest matching registrable suffix of url's host in table."""
    h = domain(url)
    parts = h.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in table:
            return table[cand]
    return default


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(s, limit=None):
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style|figure|figcaption)[^>]*>.*?</\1>", " ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(html.unescape(s))
    s = s.replace("\xa0", " ")
    s = _WS_RE.sub(" ", s).strip()
    s = re.sub(r"\s*(The post .{0,200} appeared first on .{0,80})$", "", s)
    s = re.sub(r"\s*(Continue reading|Read more|Read More)\s*(\.\.\.|…)?\s*$", "", s)
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def first_sentences(text, max_chars=300, n=2):
    text = clean_html(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])", text)
    out = ""
    for p in parts[:n]:
        if len(out) + len(p) + 1 > max_chars and out:
            break
        out = (out + " " + p).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


STOP = set("""a an the and or of for to in on at by with from as is are was were be been it its this that these those
into over after before about new says said say will can could may might has have had not no but than then more most up
out how why what who when where which while amid via vs just now first also report reports reportedly its it's your you
we our their they them his her he she i s""".split())


def title_tokens(t):
    t = unicodedata.normalize("NFKD", t or "").lower()
    t = re.sub(r"[’'`]s\b", "", t)
    toks = re.findall(r"[a-z0-9][a-z0-9.+-]*", t)
    return {x.strip(".-") for x in toks if x not in STOP and len(x) > 1}


def title_similarity(a, b):
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(jac, seq * 0.9)


def is_dup_title(a, b):
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return False
    jac = len(ta & tb) / len(ta | tb)
    if jac >= 0.6:
        return True
    if jac < 0.25:
        return False
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio() >= 0.88


def strip_source_suffix(title, source_name=None):
    """Google News titles end with ' - Publisher'."""
    if source_name and title.endswith(" - " + source_name):
        return title[: -len(source_name) - 3].strip()
    return re.sub(r"\s+[-–|]\s+[^-–|]{2,40}$", "", title).strip() if " - " in title else title


def slugify(s, maxlen=64):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > maxlen:
        s = s[:maxlen].rsplit("-", 1)[0]
    return s or "story"


AI_RE = re.compile(r"""(\bA\.?I\b|\bAI[- ]|\bAIs\b|artificial intelligence|machine learning|\bML\b|\bLLMs?\b|\bGPT|ChatGPT|OpenAI|
Anthropic|\bClaude\b|\bGemini\b|DeepMind|Copilot|\bLlama\b|Mistral|neural|chatbot|generative|agentic|\bagents?\b|Nvidia|
\bGPUs?\b|data ?cent(er|re)s?|Hugging ?Face|\bGrok\b|\bxAI\b|Perplexity|humanoid|robot|deep learning|DeepSeek|\bQwen\b|
Sarvam|Krutrim|IndiaAI|semiconductor|\bchips?\b|superintelligence|\bAGI\b|diffusion model|transformer|inference|
foundation model|frontier model|Midjourney|Stable Diffusion|\bSora\b|\bVeo\b|Kimi|Moonshot|Zhipu|MiniMax|Cohere|
Scale AI|Databricks|CoreWeave|\bTPUs?\b|HBM|deepfake|large language)""", re.X | re.I)


def looks_ai(*texts):
    return bool(AI_RE.search(" ".join(t or "" for t in texts)))


# ---------- heuristic categorisation (used by the no-LLM fallback) ----------
CAT_RULES = [
    ("Funding & startups", r"\b(raises?|raised|funding|series [a-f]\b|seed round|valuation|unicorn|venture|investors?|acquires?|acquisition|acquired|ipo|backed|startup)\b"),
    ("Policy & safety", r"\b(regulat\w*|law|lawsuit|sued|sues|court|judge|ruling|ban|bill|senate|congress|parliament|eu ai act|policy|government|ministry|safety|copyright|privacy|antitrust|ftc|doj|deepfake|misinformation|election|security|vulnerab\w*|jailbreak|guardrail|meity)\b"),
    ("Hardware & chips", r"\b(chips?|gpus?|tpus?|semiconductor|nvidia|amd|tsmc|intel|hbm|data ?cent(er|re)s?|supercomputer|accelerator|blackwell|rubin|wafer|foundry|power grid|gigawatt|server)\b"),
    ("Research & papers", r"\b(paper|study|research(ers)?|arxiv|benchmark|dataset|scientists?|university|nature|journal|preprint)\b"),
    ("Open source", r"\b(open[- ]source|open[- ]weights?|hugging ?face|github|apache 2\.0|mit license|gguf|ollama|llama\.cpp)\b"),
    ("Model releases", r"\b(model|gpt-?\d|gemini \d|claude \w+ \d|llama \d|launch(es|ed)? .*model|releases? .*model|frontier)\b"),
    ("Big Tech moves", r"\b(google|microsoft|apple|amazon|meta|alphabet|openai|nvidia|samsung|oracle|ibm|x\.com|tesla|xai)\b"),
    ("Tools & products", r"\b(app|feature|tool|launch(es|ed)?|rolls? out|update|assistant|agent|plugin|api|available|beta|preview)\b"),
]


def heuristic_category(title, text="", source_id=""):
    if source_id in ("hf-papers", "arxiv-ai", "arxiv-cl", "arxiv-lg"):
        return "Research & papers"
    if source_id == "hf-models":
        return "Open source"
    s = f"{title} {title} {text}".lower()
    for cat, rx in CAT_RULES:
        if re.search(rx, s):
            return cat
    return "Other"


KNOWN_ENTITIES = ["OpenAI", "ChatGPT", "Anthropic", "Claude", "Google", "Gemini", "DeepMind", "Meta", "Llama", "Microsoft",
                  "Copilot", "Nvidia", "AMD", "Intel", "TSMC", "Apple", "Amazon", "AWS", "xAI", "Grok", "Mistral",
                  "DeepSeek", "Qwen", "Alibaba", "Hugging Face", "Perplexity", "Samsung", "Tesla", "Oracle", "IBM",
                  "Sarvam", "Krutrim", "India", "China", "EU", "US", "UK", "agents", "robotics", "chips", "open source",
                  "funding", "regulation", "safety", "copyright", "research", "data centers"]


def heuristic_tags(title, text="", limit=4):
    s = f"{title} {text}"
    tags = []
    for e in KNOWN_ENTITIES:
        if re.search(r"(?<![A-Za-z])" + re.escape(e) + r"(?![A-Za-z])", s, re.I if e.islower() else 0):
            tags.append(e)
        if len(tags) >= limit:
            break
    return tags or ["AI"]
