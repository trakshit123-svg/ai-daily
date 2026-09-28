"""Static configuration: sources, categories, trusted video channels, tunables (env-overridable)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ED_DIR = ROOT / "editions"
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("AI_DAILY_DB", DATA_DIR / "ai_daily.db"))
RUN_DIR = DATA_DIR / "runs"  # small per-run JSON report (committed; last few kept)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
FEED_UA = "Mozilla/5.0 (compatible; AIDailyBot/1.0; +https://trakshit123-svg.github.io/ai-daily/)"

CATEGORIES = ["Model releases", "Research & papers", "Tools & products", "Big Tech moves", "Open source",
              "Funding & startups", "Policy & safety", "Hardware & chips", "Other"]
IMPORTANCE = ["Major", "Notable", "Minor"]

def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default

WINDOW_HOURS = env_int("WINDOW_HOURS", 28)         # candidate window before the run
MIN_ITEMS = env_int("MIN_ITEMS", 12)               # below this we refuse to publish
TARGET_MIN, TARGET_MAX = 25, 50
MAX_CANDIDATES_FOR_LLM = env_int("MAX_CANDIDATES", 220)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
# Tried in order when the primary model is unavailable or out of free-tier quota.
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACK_MODELS",
    "gemini-3.7-flash,gemini-3.5-flash-lite,gemini-2.5-flash,gemini-3.1-flash-lite").split(",") if m.strip()]

# ---------------------------------------------------------------------------------------------
# Sources. kind: rss | gnews | hn | hf_papers | hf_models | reddit | techmeme
# weight: editorial prior (primary lab ≈ 3, major outlet ≈ 2, aggregator/community ≈ 1)
# ai_filter: True -> keep only entries that look AI-related (general tech feeds)
# ---------------------------------------------------------------------------------------------
def _s(id, name, kind, url, weight=1.5, ai_filter=False, cap=None, primary=False):
    return dict(id=id, name=name, kind=kind, url=url, weight=weight, ai_filter=ai_filter, cap=cap, primary=primary)

SOURCES = [
    # --- labs / companies (primary) ---
    _s("openai", "OpenAI", "rss", "https://openai.com/news/rss.xml", 3.2, primary=True),
    _s("deepmind", "Google DeepMind", "rss", "https://deepmind.google/blog/rss.xml", 3.0, primary=True),
    _s("google-ai", "Google", "rss", "https://blog.google/technology/ai/rss/", 2.8, primary=True),
    _s("google-blog", "Google", "rss", "https://blog.google/rss/", 2.2, ai_filter=True, primary=True),
    _s("google-research", "Google Research", "rss", "https://research.google/blog/rss/", 2.4, primary=True),
    _s("anthropic", "Anthropic", "rss", "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml", 3.2, primary=True),
    _s("anthropic-research", "Anthropic", "rss", "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_research.xml", 2.8, primary=True),
    _s("meta-news", "Meta", "rss", "https://about.fb.com/news/feed/", 2.4, ai_filter=True, primary=True),
    _s("meta-eng", "Engineering at Meta", "rss", "https://engineering.fb.com/feed/", 1.8, ai_filter=True, primary=True),
    _s("microsoft-blog", "Microsoft", "rss", "https://blogs.microsoft.com/feed/", 2.4, ai_filter=True, primary=True),
    _s("microsoft-source", "Microsoft Source", "rss", "https://news.microsoft.com/source/feed/", 2.2, ai_filter=True, primary=True),
    _s("microsoft-research", "Microsoft Research", "rss", "https://www.microsoft.com/en-us/research/feed/", 2.0, primary=True),
    _s("nvidia-blog", "NVIDIA Blog", "rss", "https://blogs.nvidia.com/feed/", 2.4, primary=True),
    _s("nvidia-news", "NVIDIA Newsroom", "rss", "https://nvidianews.nvidia.com/releases.xml", 2.6, primary=True),
    _s("huggingface", "Hugging Face", "rss", "https://huggingface.co/blog/feed.xml", 2.2, primary=True),
    _s("aws-ml", "AWS Machine Learning Blog", "rss", "https://aws.amazon.com/blogs/machine-learning/feed/", 1.4, primary=True, cap=4),
    _s("apple-ml", "Apple Machine Learning Research", "rss", "https://machinelearning.apple.com/rss.xml", 2.2, primary=True),
    _s("together", "Together AI", "rss", "https://www.together.ai/blog/rss.xml", 1.4, primary=True, cap=3),
    # --- AI / tech news outlets ---
    _s("techcrunch-ai", "TechCrunch", "rss", "https://techcrunch.com/category/artificial-intelligence/feed/", 2.2),
    _s("verge-ai", "The Verge", "rss", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 2.2),
    _s("mittr-ai", "MIT Technology Review", "rss", "https://www.technologyreview.com/topic/artificial-intelligence/feed", 2.1),
    _s("ars-ai", "Ars Technica", "rss", "https://arstechnica.com/ai/feed/", 2.0),
    _s("wired-ai", "WIRED", "rss", "https://www.wired.com/feed/tag/ai/latest/rss", 2.0),
    _s("decoder", "The Decoder", "rss", "https://the-decoder.com/feed/", 1.8),
    _s("guardian-ai", "The Guardian", "rss", "https://www.theguardian.com/technology/artificialintelligenceai/rss", 2.0),
    _s("nyt-ai", "The New York Times", "rss", "https://www.nytimes.com/svc/collections/v1/publish/https://www.nytimes.com/spotlight/artificial-intelligence/rss.xml", 2.1, ai_filter=True),
    _s("cnbc-tech", "CNBC", "rss", "https://www.cnbc.com/id/19854910/device/rss/rss.html", 2.0, ai_filter=True),
    _s("bloomberg-tech", "Bloomberg", "rss", "https://feeds.bloomberg.com/technology/news.rss", 2.1, ai_filter=True),
    _s("theinformation", "The Information", "rss", "https://www.theinformation.com/feed", 1.9, ai_filter=True),
    _s("semafor", "Semafor", "rss", "https://www.semafor.com/rss.xml", 1.6, ai_filter=True, cap=5),
    _s("engadget", "Engadget", "rss", "https://www.engadget.com/rss.xml", 1.6, ai_filter=True, cap=6),
    _s("siliconangle-ai", "SiliconANGLE", "rss", "https://siliconangle.com/category/ai/feed/", 1.5, cap=6),
    _s("zdnet-ai", "ZDNET", "rss", "https://www.zdnet.com/topic/artificial-intelligence/rss.xml", 1.5, cap=6),
    _s("ieee-ai", "IEEE Spectrum", "rss", "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", 1.6),
    _s("nature-ml", "Nature", "rss", "https://www.nature.com/subjects/machine-learning.rss", 1.8),
    _s("simonw", "Simon Willison", "rss", "https://simonwillison.net/atom/everything/", 1.3, ai_filter=True, cap=4),
    _s("techmeme", "Techmeme", "techmeme", "https://www.techmeme.com/feed.xml", 2.0, ai_filter=True),
    # --- India ---
    _s("et-tech", "The Economic Times", "rss", "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms", 1.7, ai_filter=True, cap=8),
    _s("inc42", "Inc42", "rss", "https://inc42.com/feed/", 1.7, ai_filter=True, cap=6),
    _s("yourstory", "YourStory", "rss", "https://yourstory.com/feed", 1.5, ai_filter=True, cap=6),
    _s("medianama", "MediaNama", "rss", "https://www.medianama.com/feed/", 1.6, ai_filter=True, cap=5),
    _s("livemint-ai", "Mint", "rss", "https://www.livemint.com/rss/AI", 1.7, cap=8),
    _s("thehindu-tech", "The Hindu", "rss", "https://www.thehindu.com/sci-tech/technology/feeder/default.rss", 1.5, ai_filter=True, cap=6),
    _s("indianexpress-tech", "The Indian Express", "rss", "https://indianexpress.com/section/technology/feed/", 1.5, ai_filter=True, cap=6),
    # --- research ---
    _s("hf-papers", "Hugging Face Daily Papers", "hf_papers", "https://huggingface.co/api/daily_papers?limit=100", 1.6, cap=12),
    _s("arxiv-ai", "arXiv", "rss", "https://rss.arxiv.org/rss/cs.AI", 0.4, cap=4),
    _s("arxiv-cl", "arXiv", "rss", "https://rss.arxiv.org/rss/cs.CL", 0.4, cap=4),
    _s("arxiv-lg", "arXiv", "rss", "https://rss.arxiv.org/rss/cs.LG", 0.4, cap=4),
    # --- community ---
    _s("hn", "Hacker News", "hn", "https://hn.algolia.com/api/v1/search", 1.6, ai_filter=True, cap=25),
    _s("hf-models", "Hugging Face (trending models)", "hf_models", "https://huggingface.co/api/models?sort=trendingScore&limit=60&full=false", 1.2, cap=8),
    _s("reddit-ml", "r/MachineLearning", "reddit", "https://www.reddit.com/r/MachineLearning/top/.rss?t=day", 0.9, cap=6),
    _s("reddit-localllama", "r/LocalLLaMA", "reddit", "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day", 0.9, cap=8),
]

# Google News RSS search queries (decoded to publisher URLs later). {win} is replaced by the time filter.
GNEWS_QUERIES = [
    ("gn-ai", "artificial intelligence", "US"),
    ("gn-openai", "OpenAI OR ChatGPT", "US"),
    ("gn-anthropic", "Anthropic OR \"Claude AI\"", "US"),
    ("gn-google", "\"Google Gemini\" OR DeepMind", "US"),
    ("gn-meta", "\"Meta AI\" OR \"Meta Superintelligence\" OR Llama model", "US"),
    ("gn-microsoft", "\"Microsoft AI\" OR Copilot AI", "US"),
    ("gn-xai", "xAI OR Grok", "US"),
    ("gn-apple-amazon", "\"Apple Intelligence\" OR \"Amazon AI\" OR AWS AI", "US"),
    ("gn-nvidia-chips", "Nvidia OR \"AI chip\" OR TSMC AI", "US"),
    ("gn-china", "DeepSeek OR Qwen OR Alibaba AI OR Moonshot Kimi", "US"),
    ("gn-mistral-eu", "\"Mistral AI\" OR \"EU AI Act\"", "US"),
    ("gn-funding", "AI startup raises funding", "US"),
    ("gn-policy", "AI regulation OR \"AI safety\" OR AI lawsuit", "US"),
    ("gn-opensource", "open-source AI model released", "US"),
    ("gn-research", "AI research breakthrough study", "US"),
    ("gn-robotics", "humanoid robot AI", "US"),
    ("gn-reuters", "site:reuters.com AI", "US"),
    ("gn-india", "AI India", "IN"),
    ("gn-india-startups", "IndiaAI OR Sarvam OR Krutrim OR \"Indian AI startup\"", "IN"),
]
GNEWS_PER_QUERY = 30
GNEWS_WEIGHT = 1.2

# Publisher reputation boost when a story arrives via an aggregator (Google News, HN, Techmeme ...)
REPUTABLE_DOMAINS = {
    "reuters.com": 2.3, "bloomberg.com": 2.2, "ft.com": 2.1, "wsj.com": 2.1, "nytimes.com": 2.1, "cnbc.com": 2.0,
    "theverge.com": 2.1, "techcrunch.com": 2.1, "arstechnica.com": 2.0, "wired.com": 2.0, "theinformation.com": 2.0,
    "technologyreview.com": 2.0, "venturebeat.com": 1.8, "axios.com": 1.9, "apnews.com": 2.0, "bbc.com": 2.0,
    "bbc.co.uk": 2.0, "theguardian.com": 2.0, "washingtonpost.com": 2.0, "economist.com": 2.0, "fortune.com": 1.8,
    "businessinsider.com": 1.7, "semafor.com": 1.8, "platformer.news": 1.8, "404media.co": 1.7, "nature.com": 2.0,
    "science.org": 2.0, "arxiv.org": 1.4, "github.com": 1.3, "huggingface.co": 1.8, "openai.com": 3.2,
    "anthropic.com": 3.2, "deepmind.google": 3.0, "blog.google": 2.8, "ai.meta.com": 3.0, "about.fb.com": 2.4,
    "microsoft.com": 2.4, "nvidia.com": 2.4, "mistral.ai": 2.8, "x.ai": 2.8, "apple.com": 2.4, "aboutamazon.com": 2.2,
    "economictimes.indiatimes.com": 1.7, "livemint.com": 1.7, "thehindu.com": 1.6, "indianexpress.com": 1.6,
    "inc42.com": 1.7, "yourstory.com": 1.5, "medianama.com": 1.6, "moneycontrol.com": 1.6, "business-standard.com": 1.6,
    "hindustantimes.com": 1.5, "timesofindia.indiatimes.com": 1.4, "ndtv.com": 1.4, "indiatoday.in": 1.4,
    "analyticsindiamag.com": 1.5, "pib.gov.in": 1.8, "the-decoder.com": 1.7, "siliconangle.com": 1.5, "zdnet.com": 1.5,
    "engadget.com": 1.6, "tomshardware.com": 1.6, "theregister.com": 1.7, "9to5google.com": 1.5, "9to5mac.com": 1.5,
    "macrumors.com": 1.4, "cnet.com": 1.5, "politico.com": 1.8, "politico.eu": 1.8, "euronews.com": 1.5,
    "scmp.com": 1.7, "nikkei.com": 1.8, "asia.nikkei.com": 1.8, "globenewswire.com": 1.2, "prnewswire.com": 1.2,
    "businesswire.com": 1.2, "marktechpost.com": 1.2, "simonwillison.net": 1.3, "quantamagazine.org": 1.8,
}
LOW_QUALITY_DOMAINS = {"msn.com", "yahoo.com", "finance.yahoo.com", "tradingview.com", "benzinga.com", "fool.com",
                       "zacks.com", "investing.com", "seekingalpha.com", "marketbeat.com", "nasdaq.com",
                       "youtube.com", "facebook.com", "instagram.com", "linkedin.com", "medium.com", "substack.com"}

# Channels whose videos may be attached (official labs/companies, major outlets, reputable explainers).
# (handle, channel_id, label)
TRUSTED_CHANNELS = [
    ("OpenAI", "UCXZCJLdBC09xxGZ6gcdrc6A", "OpenAI"),
    ("anthropic-ai", "UCrDwWp7EBBv4NwvScIpBDOA", "Anthropic"),
    ("Google", "UCK8sQmJBp8GCxrOtXWBpyEA", "Google"),
    ("GoogleDeepMind", "UCP7jMXSY2xbc3KCAE0MHQ-A", "Google DeepMind"),
    ("GoogleDevelopers", "UC_x5XG1OV2P6uZZ5FSM9Ttw", "Google for Developers"),
    ("GoogleCloudTech", "UCJS9pqu9BzkAMNTmzNMNhvg", "Google Cloud Tech"),
    ("GoogleIndia", "UCoVwq0vh-XD8RrEyDZ0KeJw", "Google India"),
    ("Meta", "UC04FyDIvYXNecpbG8gyOw4A", "Meta"),
    ("MetaDevelopers", "UCP_lo1MFyx5IXDeD9s_6nUw", "Meta Developers"),
    ("AIatMeta", "UC5qxlwEKM7-5YZudb24l0bg", "AI at Meta"),
    ("Microsoft", "UCFtEEv80fQVKkD4h1PF-Xqw", "Microsoft"),
    ("Microsoft365", "UCc3pNIRzIZ8ynI38GO6H01Q", "Microsoft Copilot"),
    ("NVIDIA", "UCHuiy8bXnmK5nisYHUd1J5g", "NVIDIA"),
    ("HuggingFace", "UCHlNU7kIZhRgSbhHvFoy72w", "Hugging Face"),
    ("MistralAI", "UCRaz_dquopKtb4ptswKcxTA", "Mistral AI"),
    ("Apple", "UCE_M8A5yxnLfW0KghEeajjw", "Apple"),
    ("amazonwebservices", "UCd6MoB9NC6uYN2grvUNT-Zg", "Amazon Web Services"),
    ("Perplexity-AI", "UCYqxnCFtaC4-iC_bwt2bRLg", "Perplexity"),
    ("AlibabaCloud", "UCipPA-ZHX6UYGH_Iyti1-Jw", "Alibaba Cloud"),
    ("DeepLearningAI", "UCcIXc5mJsHVYTZR1maL5l9w", "DeepLearning.AI"),
    ("TwoMinutePapers", "UCbfYPyITQ-7l4upoX8nvctg", "Two Minute Papers"),
    ("SamWitteveen", "UCSdphiIAFdD_16nNOpliTIw", "Sam Witteveen"),
    ("aiexplained-official", "UCNJ1Ymd5yFuUPtn21xtRbbw", "AI Explained"),
    ("AnalyticsVidhya", "UCH6gDteHtH4hg3o2343iObA", "Analytics Vidhya"),
    ("CNBC", "UCvJJ_dzjViJCoLf5uKUTwoA", "CNBC"),
    ("CNBCtelevision", "UCrp_UI8XtuYfpiqluWLD7Lw", "CNBC Television"),
    ("markets", "UCIALMKvObZNtJ6AmdCLP7Lg", "Bloomberg Television"),
    ("Reuters", "UChqUTb7kYRX8-EiaN3XFrSQ", "Reuters"),
    ("WSJ", "UCK7tptUDHh-RYDsdxO1-5QQ", "The Wall Street Journal"),
    ("TheVerge", "UCddiUEpeqJcYeBxX1IVBKvQ", "The Verge"),
    ("TechCrunch", "UCCjyq_K1Xwfg8Lndy7lKMpA", "TechCrunch"),
    ("CNET", "UCOmcA3f_RrH6b9NmcNa4tdg", "CNET"),
    ("CNBC-TV18", "UCmRbHAgG2k2vDUvb3xsEunQ", "CNBC-TV18"),
    ("IndiaToday", "UCYPvAwZP8pZhSMW8qs7cVCw", "India Today"),
    ("NDTVProfitIndia", "UC3uJIdRFTGgLWrUziaHbzrg", "NDTV Profit"),
    ("AssociatedPress", "UC52X5wxOL_s5yw0dQk7NtgA", "Associated Press"),
    ("cspan", "UCb--64Gl51jIEVE-GLDAVTg", "C-SPAN"),
    ("NBCNews", "UCeY0bbntWzzVIaj2z3QigXg", "NBC News"),
    ("CBSNews", "UC8p1vwvWtl6T73JiExfWs1g", "CBS News"),
    ("ABCNews", "UCBi2mrWuNuyYy4gbM6fU18Q", "ABC News"),
    ("CBCNews", "UCuFFtHWoLl5fauMMD5Ww2jA", "CBC News"),
]
# Official channels whose uploads feed (RSS) we scan every run for same-day videos.
CHANNEL_RSS_HANDLES = ["OpenAI", "anthropic-ai", "Google", "GoogleDeepMind", "GoogleDevelopers", "Meta", "AIatMeta",
                       "Microsoft", "NVIDIA", "HuggingFace", "MistralAI", "amazonwebservices", "Perplexity-AI",
                       "TwoMinutePapers", "CNBCtelevision", "markets", "Reuters", "TheVerge", "TechCrunch", "CNBC-TV18"]
