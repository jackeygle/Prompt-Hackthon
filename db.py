"""SQLite storage: schema, API/LLM response cache, and small helpers."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, brief TEXT, spec_json TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS creators (
  id TEXT PRIMARY KEY, name TEXT, primary_platform TEXT, country TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS platform_accounts (
  id TEXT PRIMARY KEY, creator_id TEXT REFERENCES creators, platform TEXT, handle TEXT, url TEXT,
  followers INTEGER, source TEXT, source_reliability REAL, raw_json TEXT, fetched_at TEXT,
  UNIQUE(platform, handle));

CREATE TABLE IF NOT EXISTS content (
  id TEXT PRIMARY KEY, account_id TEXT, platform TEXT, title TEXT, description TEXT,
  published_at TEXT, duration_s INTEGER, views INTEGER, likes INTEGER, comments INTEGER,
  language TEXT, transcript TEXT, transcript_source TEXT, raw_json TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY, content_id TEXT, author_hash TEXT, text TEXT, likes INTEGER,
  is_creator_reply INTEGER, published_at TEXT, label TEXT, language TEXT);

CREATE TABLE IF NOT EXISTS features (
  campaign_id TEXT, creator_id TEXT, name TEXT, value REAL, method TEXT, n_samples INTEGER,
  PRIMARY KEY (campaign_id, creator_id, name));

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY, campaign_id TEXT, creator_id TEXT, feature TEXT, content_id TEXT,
  quote TEXT, verified INTEGER);

CREATE TABLE IF NOT EXISTS rankings (
  campaign_id TEXT, creator_id TEXT, rank INTEGER, topsis_score REAL, confidence REAL,
  passed_hard_filter INTEGER, filter_reason TEXT, breakdown_json TEXT,
  PRIMARY KEY (campaign_id, creator_id));

-- where each creator was found (YouTube search, Tavily web result, seed); web-only creators have no creator row
CREATE TABLE IF NOT EXISTS discoveries (
  id INTEGER PRIMARY KEY, campaign_id TEXT, creator_key TEXT, source TEXT, platform TEXT, handle TEXT,
  query TEXT, url TEXT, evidence TEXT, method TEXT);

CREATE TABLE IF NOT EXISTS visual_tags (
  campaign_id TEXT, creator_id TEXT, video_id TEXT, url TEXT, tags_json TEXT,
  PRIMARY KEY (campaign_id, creator_id, video_id));

CREATE TABLE IF NOT EXISTS run_stats (campaign_id TEXT PRIMARY KEY, stats_json TEXT);

CREATE TABLE IF NOT EXISTS api_cache (key TEXT PRIMARY KEY, response TEXT, created_at TEXT);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def use(path) -> None:
    """Switch the active database file (e.g. to the committed demo snapshot)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        config.DB_PATH = path


def execute(sql: str, params=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur


def executemany(sql: str, rows):
    with _lock:
        c = conn()
        c.executemany(sql, rows)
        c.commit()


def query(sql: str, params=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def cache_get(key: str):
    rows = query("SELECT response FROM api_cache WHERE key=?", (key,))
    return json.loads(rows[0]["response"]) if rows else None


def cache_put(key: str, value) -> None:
    execute("INSERT OR REPLACE INTO api_cache VALUES (?,?,?)", (key, json.dumps(value), now()))
