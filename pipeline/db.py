"""SQLite store (data/ai_daily.db, committed to the repo): sources, articles, videos, editions, edition_items."""
import datetime as dt, json, sqlite3
from .config import DB_PATH, ED_DIR
from .textutil import url_hash, normalize_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, url TEXT, weight REAL,
  last_ok INTEGER, last_error TEXT, last_count INTEGER, last_checked_at TEXT, last_ok_at TEXT,
  ok_runs INTEGER DEFAULT 0, fail_runs INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url_hash TEXT UNIQUE NOT NULL, url TEXT NOT NULL, title TEXT, source_id TEXT, source_name TEXT,
  published_at TEXT, snippet TEXT, http_status INTEGER, verified TEXT,
  first_seen_at TEXT, last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_seen ON articles(last_seen_at);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT UNIQUE NOT NULL, url TEXT, platform TEXT, title TEXT, channel_name TEXT, channel_url TEXT,
  published_at TEXT, verified_at TEXT
);
CREATE TABLE IF NOT EXISTS editions (
  date TEXT PRIMARY KEY, generated_at TEXT, mode TEXT, model TEXT, item_count INTEGER, video_count INTEGER,
  candidate_count INTEGER, sources_ok INTEGER, sources_total INTEGER, window_start TEXT, window_end TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS edition_items (
  edition_date TEXT NOT NULL REFERENCES editions(date) ON DELETE CASCADE,
  position INTEGER NOT NULL, item_id TEXT NOT NULL, article_id INTEGER REFERENCES articles(id),
  headline TEXT, summary TEXT, category TEXT, importance TEXT, source_name TEXT, source_url TEXT,
  published_at TEXT, video_id INTEGER REFERENCES videos(id), tags TEXT,
  PRIMARY KEY (edition_date, item_id)
);
"""


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _iso(d):
    if d is None:
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


class DB:
    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- sources ----
    def record_sources(self, statuses):
        t = now_iso()
        for s in statuses:
            self.conn.execute("""INSERT INTO sources(id,name,kind,url,weight,last_ok,last_error,last_count,last_checked_at,last_ok_at,ok_runs,fail_runs)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, url=excluded.url, weight=excluded.weight,
                  last_ok=excluded.last_ok, last_error=excluded.last_error, last_count=excluded.last_count,
                  last_checked_at=excluded.last_checked_at, last_ok_at=COALESCE(excluded.last_ok_at, sources.last_ok_at),
                  ok_runs=sources.ok_runs+excluded.ok_runs, fail_runs=sources.fail_runs+excluded.fail_runs""",
                (s["id"], s["name"], s["kind"], s["url"], s["weight"], int(s["ok"]), s.get("error"), s["count"], t,
                 t if s["ok"] else None, int(s["ok"]), int(not s["ok"])))
        self.conn.commit()

    # ---- articles ----
    def upsert_article(self, url, title=None, source_id=None, source_name=None, published_at=None, snippet=None,
                       http_status=None, verified=None):
        h, t = url_hash(url), now_iso()
        self.conn.execute("""INSERT INTO articles(url_hash,url,title,source_id,source_name,published_at,snippet,http_status,verified,first_seen_at,last_seen_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url_hash) DO UPDATE SET last_seen_at=excluded.last_seen_at,
              title=COALESCE(excluded.title, articles.title), snippet=COALESCE(excluded.snippet, articles.snippet),
              http_status=COALESCE(excluded.http_status, articles.http_status), verified=COALESCE(excluded.verified, articles.verified)""",
            (h, url, title, source_id, source_name, _iso(published_at), (snippet or None) and snippet[:400], http_status, verified, t, t))
        return self.conn.execute("SELECT id FROM articles WHERE url_hash=?", (h,)).fetchone()[0]

    def upsert_video(self, video_id, url, title, channel_name, channel_url, published_at=None, platform="youtube"):
        self.conn.execute("""INSERT INTO videos(video_id,url,platform,title,channel_name,channel_url,published_at,verified_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET verified_at=excluded.verified_at, title=excluded.title""",
            (video_id, url, platform, title, channel_name, channel_url, _iso(published_at), now_iso()))
        return self.conn.execute("SELECT id FROM videos WHERE video_id=?", (video_id,)).fetchone()[0]

    # ---- history for dedupe ----
    def previous_hashes(self, exclude_date, days=30):
        rows = self.conn.execute("""SELECT a.url_hash FROM edition_items ei JOIN articles a ON a.id=ei.article_id
                                    WHERE ei.edition_date<>? AND ei.edition_date>=date(?, ?)""",
                                 (exclude_date, exclude_date, f"-{days} day")).fetchall()
        return {r[0] for r in rows}

    def previous_headlines(self, exclude_date, days=4):
        rows = self.conn.execute("""SELECT ei.headline, a.title FROM edition_items ei LEFT JOIN articles a ON a.id=ei.article_id
                                    WHERE ei.edition_date<>? AND ei.edition_date>=date(?, ?) AND ei.edition_date<?""",
                                 (exclude_date, exclude_date, f"-{days} day", exclude_date)).fetchall()
        out = []
        for h, t in rows:
            out.append(h)
            if t:
                out.append(t)
        return out

    def used_video_ids(self, exclude_date):
        rows = self.conn.execute("""SELECT v.video_id FROM edition_items ei JOIN videos v ON v.id=ei.video_id
                                    WHERE ei.edition_date<>?""", (exclude_date,)).fetchall()
        return {r[0] for r in rows}

    # ---- editions ----
    def save_edition(self, ed, meta, article_ids, video_ids):
        d = ed["date"]
        self.conn.execute("DELETE FROM edition_items WHERE edition_date=?", (d,))
        self.conn.execute("""INSERT OR REPLACE INTO editions(date,generated_at,mode,model,item_count,video_count,candidate_count,
                             sources_ok,sources_total,window_start,window_end,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (d, ed["generated_at"], meta.get("mode"), meta.get("model"), len(ed["items"]),
                           sum(1 for i in ed["items"] if i.get("video_url")), meta.get("candidates"), meta.get("sources_ok"),
                           meta.get("sources_total"), meta.get("window_start"), meta.get("window_end"), meta.get("notes")))
        for pos, it in enumerate(ed["items"]):
            self.conn.execute("""INSERT INTO edition_items(edition_date,position,item_id,article_id,headline,summary,category,importance,
                                 source_name,source_url,published_at,video_id,tags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (d, pos, it["id"], article_ids.get(it["id"]), it["headline"], it["summary"], it["category"],
                               it["importance"], it["source_name"], it["source_url"], it["published_at"],
                               video_ids.get(it["id"]), json.dumps(it.get("tags", []))))
        self.conn.commit()

    def import_edition_file(self, path):
        """Load an existing editions/<date>.json into the DB (used to seed history)."""
        ed = json.loads(path.read_text())
        aids, vids = {}, {}
        for it in ed["items"]:
            aids[it["id"]] = self.upsert_article(it["source_url"], it["headline"], None, it["source_name"], it["published_at"],
                                                 it.get("summary"), None, "edition")
            if it.get("video_url"):
                vid = it["video_url"].split("v=")[-1][:11] if "v=" in it["video_url"] else it["video_url"]
                vids[it["id"]] = self.upsert_video(vid, it["video_url"], None, None, None, None, it.get("video_platform") or "youtube")
        gen = ed.get("generator") or {}
        self.save_edition(ed, dict(mode=gen.get("mode", "manual"), model=gen.get("model")), aids, vids)

    def prune(self, keep_days=10):
        """Drop candidate-only articles older than keep_days (edition articles are kept forever)."""
        self.conn.execute("""DELETE FROM articles WHERE last_seen_at < datetime('now', ?) AND id NOT IN
                             (SELECT article_id FROM edition_items WHERE article_id IS NOT NULL)""", (f"-{keep_days} day",))
        self.conn.commit()

    def close(self, vacuum=True):
        self.conn.commit()
        if vacuum:
            self.conn.execute("VACUUM")
        self.conn.close()


def seed_from_files(db):
    have = {r[0] for r in db.conn.execute("SELECT date FROM editions")}
    for f in sorted(ED_DIR.glob("????-??-??.json")):
        if f.stem not in have:
            db.import_edition_file(f)
