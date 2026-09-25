# AI Daily: Research Playbook

How to put together the next day's edition. It should take about 60–90 minutes of agent time.
The golden rule: **never invent a story, link, quote, number or video.** Every item has to trace back to a
page you actually opened. If you can't verify something, leave it out, or set `video_url` to `null`.

---

## 0. Setup

```bash
cd /workspace/ai-news-daily
python3 -m http.server 8765 &            # local preview
D=$(TZ=Asia/Kolkata date +%F)             # edition date, India time
mkdir -p research/$D
```

- **Window:** stories first published in roughly the **last 24–48 hours** before the edition date (IST).
  An older story (up to ~72h) is allowed only when it's big and wasn't in an earlier edition. Mark it with
  the tag `catch-up` and mention the real date in the summary (e.g. "(Launched Tue 22 Sep.)").
- **Target:** 25–50 items, with roughly 8–12 Major, 15–20 Notable, and the rest Minor. Aim for at least
  6 categories and at least 2 India items.

## 1. Sources to sweep (in this order)

**Tools available:** `WebSearch` (it works well with dated queries), `WebFetch`, and `curl`/Python from the
Shell. The `user-Infobip-*` MCP servers only search Infobip's own docs, so they're useless for news. Skip them.

1. **Daily AI roundups** to catch as much as possible early (open them with WebFetch, then find the original source for each story):
   - `https://tldr.tech/ai/<YYYY-MM-DD>` (today's and yesterday's)
   - `https://aiweekly.co/ai-news-today/edition/<YYYY-MM-DD>`
   - WebSearch: `AI news roundup <Month D YYYY>`, `AI news <Month D YYYY>`
2. **Labs, official first:** OpenAI (`openai.com/index`, `openai.com/news`), Google (`blog.google`, `deepmind.google/blog`,
   `developers.googleblog.com`), Anthropic (`anthropic.com/news`), Meta (`ai.meta.com/blog`, `research.meta.ai/blog`, `about.fb.com/news`),
   Microsoft (`blogs.microsoft.com`, `microsoft.ai/news`), xAI/SpaceXAI (`x.ai/news`), Apple (`apple.com/newsroom`, `machinelearning.apple.com`),
   Amazon (`aboutamazon.com`, `aws.amazon.com/blogs/machine-learning`), Nvidia (`nvidianews.nvidia.com`, `blogs.nvidia.com`),
   Mistral (`mistral.ai/news`), DeepSeek (`api-docs.deepseek.com/updates`), Qwen/Alibaba (`qwenlm.github.io`, `alizila.com`),
   Hugging Face (`huggingface.co/blog`), Perplexity, Cohere, AI21, Stability, Black Forest Labs, Moonshot/Kimi, Zhipu, MiniMax.
3. **Per-lab WebSearch queries** (always include the date or month and year):
   `OpenAI announces <Month D YYYY>`, `ChatGPT new feature <Month D YYYY>`, `Anthropic news <Month D YYYY>`,
   `Google DeepMind Gemini announcement <Month D YYYY>`, `Meta AI <Month YYYY>`, `xAI Grok news <Month D YYYY>`,
   `Microsoft AI MAI Copilot <Month D YYYY>`, `Apple Intelligence Siri AI <Month D YYYY>`, `Amazon AWS AI announcement <Month D YYYY>`,
   `Nvidia AI chips news <Month D YYYY>`, `DeepSeek new model <Month YYYY>`, `Alibaba Qwen launch <Month D YYYY>`,
   `Mistral AI announcement <Month YYYY>`.
4. **Open source:** `open-weight model released Hugging Face <Month D YYYY>`, and the Hugging Face trending page
   (`https://huggingface.co/models?sort=trending`) plus Daily Papers (`https://huggingface.co/papers`).
5. **Research:** `notable AI research paper arXiv <Month D YYYY>`, `Nature study AI <Month D YYYY>`, plus the HF Daily Papers
   and any lab research blogs. Link the arXiv abs page (`arxiv.org/abs/...`), not the PDF.
6. **Funding & startups:** `AI startup raises funding round <Month D YYYY>`, `Series A/B/C AI <Month D YYYY>`, and
   `<company> valuation`. Press releases on GlobeNewswire/PRNewswire/BusinessWire are fine as sources.
7. **Policy & safety:** `AI regulation news <Month D YYYY>`, `EU AI Act news <Month D YYYY>`, `AI lawsuit ruling <Month D YYYY>`,
   `AI safety incident <Month D YYYY>`, the White House/Commerce/NIST/CAISI, the UK AISI, the UN, China CAC.
8. **Hardware & infra:** `TSMC Samsung SK Hynix HBM AI chip news <Month D YYYY>`, `AMD AI accelerator news`, `data center AI
   power <Month D YYYY>`, and Supermicro/Dell/CoreWeave/Oracle press releases.
9. **India:** `India AI news <Month D YYYY>`, and the sites Inc42, Entrackr, YourStory, MediaNama, Analytics India Magazine,
   ET Tech, India Today Tech and MeitY/IndiaAI Mission (Sarvam, Krutrim, BharatGen, Soket, CoRover, Gnani, etc.).
10. **Robotics / other:** `humanoid robot AI news <Month D YYYY>`, `AI video generation model launch <Month D YYYY>`.

Tip: a search result's "synthesis" text is only a lead. **Always open the article itself** and confirm the date and the facts.
Also watch for similar-sounding names, fake "official" channels, and stories that are just recaps of older news.

## 2. Verify every source URL

Put the candidate URLs in `research/$D/urls.txt`, then:

```bash
python3 scripts/check_urls.py research/$D/urls.txt research/$D/results.json   # status, <title>, published date
python3 scripts/article_text.py <url> ...                                    # quick text dump to confirm facts
```

Rules:
- The page has to exist, match the headline, and have a publish date inside the window.
  `check_urls.py` pulls the date from `datePublished` / `article:published_time` / `<time>` where possible.
- Sites like **Reuters (401), Meta.com (400), OpenAI/Perplexity/India Today (403), and GlobeNewswire (timeouts)**
  often block scripts. Try `WebFetch` on them. If WebFetch loads the content, the page is verified. If it
  doesn't, use another reputable outlet covering the same story (Reuters → CNBC/PYMNTS/The Verge; Meta blog → The Verge).
- Prefer the primary source (company blog, paper, court record, press release). Use a reputable outlet
  when the story is reporting, a leak, or analysis. Use one `source_url` per item.
- `published_at`: use the page's own timestamp and convert it to ISO with an offset (Z is fine). If only the
  date is known, use `YYYY-MM-DD`.

## 3. Find and verify videos (YouTube / X)

```bash
python3 scripts/yt_search.py "Gemini 3.8 Flash TTS" "Meta Connect 2026 keynote"   # newest-first YouTube search
python3 scripts/yt_verify.py <videoId> <videoId> ...                             # oEmbed: title, channel, channel URL
```

Rules:
- Only attach a video if it's **about this exact story** and comes from the **official company/lab channel**
  (e.g. @OpenAI, @anthropic-ai, @googledeepmind, @meta, @MetaDevelopers, @Microsoft.Copilot), a **major news
  outlet** (Reuters, Bloomberg, CNBC, ABC, CBC, C-SPAN, CNET, CNBC-TV18…), or a **reputable explainer**
  (Two Minute Papers, Sam Witteveen, Analytics Vidhya…).
- Check the `author_url` in the oEmbed result, because impostor channels exist (e.g. "GoogIe DeveIopers" with a capital I).
  Skip clickbait or AI-slop channels and anything uploaded before the news broke, unless it's the official video for the same announcement.
- If you're unsure the video covers the story (e.g. a talk show segment), open it with `WebFetch` on the watch
  URL. It returns the transcript/description.
- Store it as `https://www.youtube.com/watch?v=<id>` with `video_platform: "youtube"`. For X, use the post URL with `"x"`.
  If no verified video exists, use `null` for both fields.

## 4. Dedupe

- **Against earlier editions:** `add_edition.py` warns when a `source_url` was used before. Also skim the last 2–3
  editions' headlines (`editions/<date>.json`). If a story is continuing, include it only when something **new** happened,
  and link the new article.
- **Within the day:** merge coverage of the same event into one item and pick the best source. Related angles
  (e.g. "Medicare breach" vs "Transluce found more probes") can be separate items if each has distinct news.

## 5. Write the items

- **headline:** specific and factual, no clickbait, and ≤ ~110 chars where possible.
- **summary:** 1–2 plain-English sentences covering what happened and why it matters. Hedge anything reported
  but unconfirmed ("reportedly", "per The Information").
- **importance:**
  - **Major:** a frontier model or flagship product launch, a big policy/legal decision, a safety incident, a
    ≥$1B deal, or anything the whole AI world will be talking about.
  - **Notable:** meaningful launches, $50M+ rounds, strong open-weight releases, important papers, India headliners.
  - **Minor:** small tools, niche papers, small rounds, leaks, follow-ups.
- **category:** pick the single best fit from the 9 categories. Robotics usually goes under `Other` unless it's
  an open model (`Open source`).
- **tags:** 2–5 entities/topics (company names, model names, "India", "agents").
- Order the items within each importance tier by editorial priority, since the app keeps JSON order inside each tier.
- **highlights:** 3–5 bullets of ≤ ~140 chars each, covering the day's biggest threads. Include an India bullet when there's India news.

Keep a build script like `research/2026-09-25/build_2026-09-25.py`. It makes edits and re-runs easy.

## 6. Publish

```bash
python3 scripts/add_edition.py research/$D/$D.draft.json --check --check-urls   # fix every ERROR; review WARNs
python3 scripts/add_edition.py research/$D/$D.draft.json                        # writes editions/$D.json, latest.json, index.json
```

Then open `http://localhost:8765/` and check that the new date is selected, that ‹ goes to the previous day,
that the filters work, and that the Watch buttons open the right videos. Optionally run
`~/pwvenv/bin/python scripts/screenshots.py` and look at `screenshots/*.png`.

Repackage if needed: `python3 -c "import shutil; shutil.make_archive('/workspace/ai-news-daily', 'zip', '/workspace', 'ai-news-daily')"`.

## 7. Final checklist

- [ ] 25–50 items, no duplicates, every `source_url` opened and verified, dates inside the window
- [ ] Every `video_url` verified with oEmbed/page fetch and matching its story; the rest are `null`
- [ ] 3–5 highlights; at least 6 categories; India covered
- [ ] `add_edition.py` passes with no ERRORs
- [ ] `latest.json` equals the new day and `index.json` lists it first
- [ ] Nothing gets posted, emailed or deployed publicly without the owner asking
