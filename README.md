# AI Daily

One place for every AI story that matters, tiny to huge. It's a static, framework-free web app that
reads one JSON file per day. Dark mode is the default (there's a light toggle), it's built for phones
first, and it can be installed as a PWA.

```
index.html        page shell
styles.css        all styling (dark default, light via [data-theme="light"])
app.js            all logic; CONFIG block at the top (app name, data folder, time zone)
manifest.json     PWA manifest (name, icons)
sw.js             service worker: offline app shell, network-first for editions/
icons/            icon.svg, icon-192.png, icon-512.png
editions/         YYYY-MM-DD.json per day, latest.json (copy of newest), index.json (list of dates)
scripts/          add_edition.py (validate + publish), research helpers, screenshots.py
pipeline/         automated daily pipeline (python -m pipeline.run), see "Automated pipeline" below
data/ai_daily.db  SQLite history (sources, articles, videos, editions, edition_items); data/runs/ = run reports
.github/workflows/daily.yml   runs the pipeline every day at 00:15 UTC (05:45 IST) and commits the edition
research/         per-day working files (build script, URL verification logs)
RESEARCH_PLAYBOOK.md   how to research and publish the next edition
```

## Run it

You can host it anywhere static files are served: GitHub Pages, Netlify, S3, nginx, and so on.

```bash
python3 -m http.server 8765        # then open http://localhost:8765/
```

> If you double-click `index.html` and open it straight from disk (`file://`), most browsers block `fetch()`,
> so the page shows a hint to serve the folder instead. Any static server works.

## Features

- **Today in 60 seconds**: 3–5 highlight bullets for the day.
- Story cards show the headline (it links to the source), a 1–2 sentence summary, a category chip, an
  importance badge (Major / Notable / Minor), the source name and link, the publish time in the viewer's
  local zone (India shows as IST), tags, and a red **Watch** button when a verified video exists.
  YouTube videos also get a thumbnail from `https://i.ytimg.com/vi/<id>/hqdefault.jpg`.
- Filters: category chips with counts, **Major only**, **Videos only**, and search across headline,
  summary, source and tags. Clicking a tag searches for it, and clicking a card's category chip filters by it.
- Browse past days with the ‹ › buttons or the date dropdown. Deep links work, e.g. `/#2026-09-25`.
- Stories are ordered by importance tier (Major → Notable → Minor). Within a tier they keep the editor's
  order from the JSON, so put the biggest story first.

## Renaming the app

- Change `CONFIG.APP_NAME` in `app.js`. It updates the header, footer and page title at runtime.
- The installed-app label comes from `manifest.json` (`name`, `short_name`), and `<title>` in
  `index.html` is only the pre-JS fallback. Update both if you want a fully consistent rename.

## Edition JSON schema

Each day is saved as `editions/YYYY-MM-DD.json`:

```jsonc
{
  "date": "2026-09-25",                       // YYYY-MM-DD (edition day, India time)
  "generated_at": "2026-09-25T23:55:00+05:30", // ISO-8601 with offset
  "highlights": ["…", "…", "…"],              // 3–5 short bullets (≈ ≤140 chars each)
  "items": [
    {
      "id": "openai-agent-breached-australia-medicare", // unique lowercase slug
      "headline": "…",                          // required
      "summary": "…",                           // 1–2 sentences (warns > 420 chars)
      "category": "Policy & safety",            // one of the 9 categories below
      "importance": "Major",                    // "Major" | "Notable" | "Minor"
      "source_name": "The Guardian",
      "source_url": "https://…",                // original article (verified)
      "published_at": "2026-09-24T02:06:05Z",   // ISO-8601 datetime, or "YYYY-MM-DD" if time unknown
      "video_url": "https://www.youtube.com/watch?v=…", // or null
      "video_platform": "youtube",              // "youtube" | "x" | "vimeo" | "other" | null
      "tags": ["OpenAI", "agents"]
    }
  ]
}
```

**Categories:** `Model releases`, `Research & papers`, `Tools & products`, `Big Tech moves`, `Open source`,
`Funding & startups`, `Policy & safety`, `Hardware & chips`, `Other`.

`editions/index.json` looks like `{"latest": "2026-09-25", "dates": ["2026-09-25", …]}` (newest first).
`editions/latest.json` is a byte-for-byte copy of the newest edition.

## Publishing a new edition

```bash
python3 scripts/add_edition.py path/to/2026-09-26.json --check --check-urls   # validate + test every link
python3 scripts/add_edition.py path/to/2026-09-26.json                        # write + update latest/index
```

The script checks the schema and flags duplicate IDs or URLs. It also warns when a `source_url` already
appeared in an earlier edition, rewrites `latest.json` and `index.json`, and with `--check-urls` runs
oEmbed on every YouTube video. See **RESEARCH_PLAYBOOK.md** for the full daily routine.

## Screenshots / QA

```bash
python3 -m venv ~/pwvenv && ~/pwvenv/bin/pip install playwright && ~/pwvenv/bin/python -m playwright install chromium
~/pwvenv/bin/python scripts/screenshots.py http://localhost:8765/   # writes screenshots/*.png
```

## Automated pipeline (no agent needed)

GitHub Actions (`.github/workflows/daily.yml`) runs `python -m pipeline.run` every day at **00:15 UTC / 05:45 IST**
(and on demand via *Actions → Daily edition → Run workflow*). It commits `editions/` + `data/` as `github-actions[bot]`,
which redeploys GitHub Pages; the Android WebView app picks the new edition up automatically.

```
collect   ~70 sources in parallel: lab/company RSS (OpenAI, Google/DeepMind/Research, Anthropic, Meta, Microsoft, NVIDIA,
          Hugging Face, AWS, Apple ML), outlets (TechCrunch, The Verge, MIT TR, Ars, WIRED, Guardian, NYT, Bloomberg,
          CNBC, The Information, Techmeme…), India (ET, Mint, Inc42, YourStory, MediaNama, The Hindu, Indian Express),
          arXiv cs.AI/CL/LG, HF Daily Papers + trending models, Hacker News (Algolia, AI-filtered), Reddit
          r/MachineLearning + r/LocalLLaMA, and ~19 Google News queries (incl. India). Google News links are decoded
          to publisher URLs.
dedupe    normalised-URL hash + fuzzy titles; drops anything already published (SQLite history + recent headlines)
sweep     Gemini + Google Search grounding finds stories the feeds missed (only kept if the page loads and is dated in-window)
select    Gemini (structured JSON) clusters same-event coverage and picks 25-50 stories with category + importance
verify    every source URL is fetched: 404/410 dropped, bot-blocks (401/403/429…) keep the feed-provided link
write     Gemini writes headline / 1-2 sentence summary / tags from the fetched page + feed text only
videos    trusted YouTube channels (official labs, major outlets) via channel pages + YouTube search
          (Data API if YOUTUBE_API_KEY, else keyless); Gemini matches, every video must pass youtube.com/oembed
publish   validated with scripts/add_edition.py rules -> editions/<date>.json, latest.json, index.json, SQLite
```

Robustness: every Gemini stage has a heuristic fallback (feed titles/snippets, keyword categories), so a Gemini outage
still yields a `fallback` edition. A worse edition never replaces a better one for the same date, fewer than
`MIN_ITEMS` (12) stories means nothing is published and the job exits non-zero, and a failed run opens/updates a
`pipeline-failure` issue (closed automatically by the next successful run). Each edition records how it was made in
`generator` (`mode`: gemini | partial | fallback, model, source counts).

Configuration (repo **Settings → Secrets and variables → Actions**):

| name | kind | purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | secret | Google AI Studio key (free tier works) |
| `YOUTUBE_API_KEY` | secret, optional | YouTube Data API v3 search instead of keyless search |
| `GEMINI_MODEL` | variable, optional | primary model (default `gemini-3.8-flash`); `GEMINI_FALLBACK_MODELS` env lists fallbacks |

A run uses about 5-6 Gemini requests (sweep, select, 2× write, finalize). Free-tier daily quotas are per model, so when
one model is exhausted the client moves to the next in the fallback list.

Local use:

```bash
pip install -r requirements.txt
python -m pipeline.check_sources                   # which feeds work right now
python -m pipeline.run --no-llm --dry-run --out /tmp/ed.json   # heuristic-only test, writes nothing
GEMINI_API_KEY=... python -m pipeline.run          # full run: writes editions/ + data/
python -m pipeline.run --date 2026-09-27 --start 2026-09-26T00:30Z --end 2026-09-27T00:15Z   # backfill a day
```
