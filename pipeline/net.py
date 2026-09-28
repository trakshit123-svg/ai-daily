"""HTTP helpers (requests session with retries, polite timeouts)."""
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from .config import UA

_session = None


def session():
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(total=2, connect=2, read=1, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504],
                      allowed_methods=["GET", "HEAD", "POST"], raise_on_status=False)
        ad = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
        s.mount("https://", ad)
        s.mount("http://", ad)
        s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        _session = s
    return _session


def get(url, timeout=20, headers=None, retries_429=1, **kw):
    """GET with one polite retry on 429."""
    for attempt in range(retries_429 + 1):
        r = session().get(url, timeout=timeout, headers=headers or {}, **kw)
        if r.status_code == 429 and attempt < retries_429:
            ra = r.headers.get("Retry-After")
            time.sleep(min(float(ra) if ra and ra.isdigit() else 4.0, 15))
            continue
        return r
    return r
