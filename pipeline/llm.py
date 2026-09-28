"""Minimal Gemini REST client: structured JSON output, Google Search grounding, retries, model fallback.

Only the standard `requests` package is used. The API key is sent in a header and never logged.
"""
import json, logging, os, random, re, time
import requests
from .config import GEMINI_MODEL, GEMINI_FALLBACK_MODELS, env_int

log = logging.getLogger("gemini")
API = "https://generativelanguage.googleapis.com/v1beta"


class GeminiUnavailable(Exception):
    pass


class Gemini:
    def __init__(self, api_key=None, models=None, max_calls=None, min_interval=None):
        self.key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "").strip()
        wanted = models or [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]
        self.models = list(dict.fromkeys(wanted))
        self.exhausted = set()
        self.calls = 0
        self.max_calls = max_calls or env_int("GEMINI_MAX_CALLS", 14)
        self.min_interval = min_interval if min_interval is not None else float(os.environ.get("GEMINI_MIN_INTERVAL", "4"))
        self._last = 0.0
        self.used_models = []
        self.available = None
        self.http = requests.Session()

    @property
    def enabled(self):
        return bool(self.key)

    def _headers(self):
        return {"x-goog-api-key": self.key, "Content-Type": "application/json"}

    def discover(self):
        """Keep only configured models the key can actually call; add a good flash model if none match."""
        if not self.enabled:
            return []
        try:
            names, token = [], None
            for _ in range(5):
                r = self.http.get(f"{API}/models", headers=self._headers(), params={"pageSize": 200, **({"pageToken": token} if token else {})}, timeout=30)
                if r.status_code != 200:
                    log.warning("ListModels HTTP %s: %s", r.status_code, r.text[:200])
                    return self.models
                d = r.json()
                names += [m["name"].split("/", 1)[1] for m in d.get("models", [])
                          if "generateContent" in m.get("supportedGenerationMethods", [])]
                token = d.get("nextPageToken")
                if not token:
                    break
            self.available = names
            keep = [m for m in self.models if m in names]
            if not keep:
                flash = sorted([n for n in names if re.match(r"^gemini-\d+(\.\d+)?-flash(-lite)?$", n)], reverse=True)
                keep = flash[:3]
                log.warning("configured models unavailable; using %s", keep)
            self.models = keep
            log.info("Gemini models in use (in order): %s", ", ".join(self.models))
        except requests.RequestException as e:
            log.warning("ListModels failed: %s", e)
        return self.models

    # ------------------------------------------------------------------
    def _pace(self):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    @staticmethod
    def _retry_delay(err_json, attempt):
        try:
            for d in err_json.get("error", {}).get("details", []):
                if d.get("@type", "").endswith("RetryInfo"):
                    return min(float(d.get("retryDelay", "10s").rstrip("s")) + 1, 65)
        except Exception:
            pass
        return min(60, (2 ** attempt) * 3 + random.random() * 2)

    @staticmethod
    def _is_daily_quota(err_json):
        s = json.dumps(err_json)
        return "PerDay" in s or "per day" in s.lower() or "limit: 0" in s

    def generate(self, prompt, system=None, schema=None, search=False, temperature=0.2, max_tokens=16384, label="call"):
        """Return (parsed_json_or_text, grounding_metadata, model). Raises GeminiUnavailable when all models fail."""
        if not self.enabled:
            raise GeminiUnavailable("GEMINI_API_KEY not set")
        last_err = None
        for model in self.models:
            if model in self.exhausted:
                continue
            gen = {"temperature": temperature, "maxOutputTokens": max_tokens}
            if schema is not None:
                gen["responseMimeType"] = "application/json"
                gen["responseSchema"] = schema
            if model.startswith("gemini-3"):
                gen["thinkingConfig"] = {"thinkingLevel": "low"}
            elif model.startswith("gemini-2.5"):
                gen["thinkingConfig"] = {"thinkingBudget": 1024 if "lite" not in model else 0}
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
            if system:
                body["systemInstruction"] = {"parts": [{"text": system}]}
            if search:
                body["tools"] = [{"google_search": {}}]
            attempt = 0
            while attempt < 5:
                if self.calls >= self.max_calls:
                    raise GeminiUnavailable(f"call budget ({self.max_calls}) reached")
                self._pace()
                self.calls += 1
                t0 = time.time()
                try:
                    r = self.http.post(f"{API}/models/{model}:generateContent", headers=self._headers(), json=body, timeout=180)
                except requests.RequestException as e:
                    last_err = f"{model}: {type(e).__name__}"
                    log.warning("[%s] %s network error: %s", label, model, type(e).__name__)
                    attempt += 1
                    time.sleep(min(30, 3 * 2 ** attempt))
                    continue
                dt_ = time.time() - t0
                if r.status_code == 200:
                    d = r.json()
                    cands = d.get("candidates") or []
                    if not cands or not cands[0].get("content", {}).get("parts"):
                        last_err = f"{model}: empty response ({(cands[0].get('finishReason') if cands else d.get('promptFeedback'))})"
                        log.warning("[%s] %s", label, last_err)
                        attempt += 2
                        continue
                    text = "".join(p.get("text", "") for p in cands[0]["content"]["parts"] if not p.get("thought"))
                    gm = cands[0].get("groundingMetadata") or {}
                    usage = d.get("usageMetadata", {})
                    log.info("[%s] %s ok in %.1fs (in=%s out=%s finish=%s)", label, model, dt_, usage.get("promptTokenCount"),
                             usage.get("candidatesTokenCount"), cands[0].get("finishReason"))
                    if model not in self.used_models:
                        self.used_models.append(model)
                    if schema is None:
                        return text, gm, model
                    try:
                        return json.loads(text), gm, model
                    except json.JSONDecodeError:
                        m = re.search(r"\{.*\}|\[.*\]", text, re.S)
                        try:
                            return json.loads(m.group(0)) if m else None, gm, model
                        except Exception:
                            pass
                        last_err = f"{model}: invalid JSON (finish={cands[0].get('finishReason')})"
                        log.warning("[%s] %s", label, last_err)
                        attempt += 2
                        continue
                try:
                    ej = r.json()
                except ValueError:
                    ej = {"error": {"message": r.text[:300]}}
                msg = ej.get("error", {}).get("message", "")[:300]
                last_err = f"{model}: HTTP {r.status_code} {msg}"
                if r.status_code == 429:
                    if self._is_daily_quota(ej):
                        log.warning("[%s] %s daily free-tier quota exhausted; trying next model", label, model)
                        self.exhausted.add(model)
                        break
                    delay = self._retry_delay(ej, attempt)
                    log.warning("[%s] %s rate limited; sleeping %.0fs", label, model, delay)
                    time.sleep(delay)
                    attempt += 1
                    continue
                if r.status_code in (500, 502, 503, 504):
                    attempt += 1
                    delay = min(45, 4 * 2 ** attempt + random.random() * 3)
                    log.warning("[%s] %s HTTP %s; retry in %.0fs", label, model, r.status_code, delay)
                    time.sleep(delay)
                    continue
                if r.status_code == 400 and "thinking" in msg.lower() and "thinkingConfig" in gen:
                    gen.pop("thinkingConfig")
                    log.info("[%s] %s: retrying without thinkingConfig", label, model)
                    continue
                if r.status_code == 400 and search and "search" in msg.lower():
                    log.warning("[%s] %s: search tool rejected: %s", label, model, msg)
                    break
                # 400/403/404: model unusable for this request -> next model
                log.warning("[%s] %s", label, last_err)
                if r.status_code in (403, 404):
                    self.exhausted.add(model)
                break
        raise GeminiUnavailable(last_err or "no Gemini model available")


# ---- schema helpers (OpenAPI subset used by responseSchema) ----
def S(type_, **kw):
    d = {"type": type_}
    d.update(kw)
    return d


def OBJ(props, required=None, order=None):
    return {"type": "OBJECT", "properties": props, "required": required or list(props), "propertyOrdering": order or list(props)}


def ARR(items, **kw):
    return {"type": "ARRAY", "items": items, **kw}
