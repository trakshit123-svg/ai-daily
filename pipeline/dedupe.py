"""Dedupe candidates by URL hash and fuzzy titles, cluster coverage of the same story, score and rank."""
import math, datetime as dt
from .textutil import url_hash, normalize_url, title_tokens, is_dup_title, domain

UTC = dt.timezone.utc


def _rep_key(c):
    # Prefer primary sources, then real (non Google News) URLs, then editorial weight.
    return (1 if c.get("primary") else 0, 0 if c.get("gn_link") and c["url"] == c.get("gn_link") else 1, c["weight"])


def cluster(cands, prev_hashes=frozenset(), prev_titles=()):
    """Return list of clusters: dict(rep, members, sources, score). Drops stories seen in earlier editions."""
    # 1) exact URL dedupe
    by_hash = {}
    for c in cands:
        if not c.get("url") or not c.get("title"):
            continue
        c["url_hash"] = url_hash(c["url"])
        c["norm_url"] = normalize_url(c["url"])
        if c["url_hash"] in by_hash:
            o = by_hash[c["url_hash"]]
            o.setdefault("also", []).append(c["source_id"])
            if _rep_key(c) > _rep_key(o):
                c["also"] = o.get("also", []) + [o["source_id"]]
                by_hash[c["url_hash"]] = c
        else:
            by_hash[c["url_hash"]] = c
    uniq = sorted(by_hash.values(), key=lambda c: -c["weight"])

    # 2) fuzzy title clustering (greedy, highest weight first)
    clusters = []
    index = {}  # token -> cluster indexes, to avoid O(n^2) full comparisons
    df = {}
    for c in uniq:
        for t in title_tokens(c["title"]):
            df[t] = df.get(t, 0) + 1
    common = {t for t, n in df.items() if n > max(25, len(uniq) // 12)}
    for c in uniq:
        toks = title_tokens(c["title"])
        cand_idx = set()
        for t in toks - common:
            cand_idx.update(index.get(t, ()))
        hit = None
        for i in sorted(cand_idx):
            cl = clusters[i]
            if any(is_dup_title(c["title"], m["title"]) for m in cl["members"][:4]):
                hit = cl
                break
        if hit is None:
            hit = dict(members=[], idx=len(clusters))
            clusters.append(hit)
        hit["members"].append(c)
        for t in toks:
            index.setdefault(t, set()).add(hit["idx"])

    # 3) drop anything already published, score the rest
    out = []
    for cl in clusters:
        ms = cl["members"]
        if any(m["url_hash"] in prev_hashes for m in ms):
            continue
        if prev_titles and any(is_dup_title(ms[0]["title"], p) for p in prev_titles):
            continue
        rep = max(ms, key=_rep_key)
        srcs = {m["source_id"] for m in ms} | {s for m in ms for s in m.get("also", [])}
        doms = {domain(m["url"]) for m in ms if not m.get("gn_link")} | {m.get("source_name") for m in ms}
        coverage = max(len(srcs), len(doms))
        pts = max((m.get("points", 0) or 0) for m in ms)
        score = max(m["weight"] for m in ms) + 0.9 * math.log2(coverage) + (0.4 if rep.get("primary") else 0)
        if pts:
            score += min(1.0, pts / 400)
        if all(m.get("community") for m in ms):
            score -= 0.4
        out.append(dict(rep=rep, members=ms, sources=sorted(srcs), coverage=coverage, score=round(score, 3)))
    out.sort(key=lambda x: -x["score"])
    return out


def select_for_llm(clusters, limit):
    """Top clusters with a per-source cap so one noisy feed can't dominate."""
    per_src, picked = {}, []
    caps = {"arxiv": 6, "reddit": 6, "hf-models": 6, "hf-papers": 10, "hn": 20}
    for cl in clusters:
        sid = cl["rep"]["source_id"]
        fam = "arxiv" if sid.startswith("arxiv") else ("reddit" if sid.startswith("reddit") else ("gnews" if sid.startswith("gn-") else sid))
        cap = caps.get(fam, 40 if fam == "gnews" else 14)
        if per_src.get(fam, 0) >= cap:
            continue
        per_src[fam] = per_src.get(fam, 0) + 1
        picked.append(cl)
        if len(picked) >= limit:
            break
    for i, cl in enumerate(picked, 1):
        cl["cid"] = f"c{i}"
    return picked
