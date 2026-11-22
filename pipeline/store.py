"""Normalize items into articles and store them in SQLite, skipping duplicates."""

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

DB_PATH = Path(__file__).parent.parent / "data" / "pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,
    published_at  TEXT,
    summary       TEXT,
    content       TEXT,
    content_hash  TEXT,
    hn_points     INTEGER,
    hn_comments   INTEGER,
    fetched_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);

-- One row per piece of news. Several articles (an announcement, commentary, a follow-up) can share a story.
CREATE TABLE IF NOT EXISTS stories (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL,     -- title of the first article seen
    first_seen_at   TEXT NOT NULL,
    last_covered_at TEXT               -- set when a briefing includes it (Milestone 6)
);

-- One row per briefing. status: draft (built, not sent) -> delivered
CREATE TABLE IF NOT EXISTS digests (
    id           INTEGER PRIMARY KEY,
    digest_date  TEXT NOT NULL,
    content_json TEXT NOT NULL,
    status       TEXT NOT NULL,
    delivered_at TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digest_items (
    digest_id      INTEGER NOT NULL REFERENCES digests(id),
    story_id       INTEGER NOT NULL REFERENCES stories(id),
    rank           INTEGER NOT NULL,
    research_trace TEXT              -- trace path when the research agent investigated this story
);

CREATE TABLE IF NOT EXISTS article_scores (
    article_id          INTEGER PRIMARY KEY REFERENCES articles(id),
    category            TEXT NOT NULL,     -- agents | rag_infra | other
    category_confidence REAL NOT NULL,
    relevance           REAL NOT NULL,     -- expected score, 1-5
    novelty             REAL NOT NULL,
    impact              REAL NOT NULL,
    model               TEXT NOT NULL,
    scored_at           TEXT NOT NULL
);
"""

# Query parameters that track clicks but don't change the page
TRACKING_PARAMS = re.compile(r"^(utm_.*|ref|ref_src|source|fbclid|gclid|mc_cid|mc_eid|s|si)$", re.I)


def canonical_url(url: str) -> str:
    """Same page, same string: lowercase host, no www, no tracking params, no fragment, no trailing slash."""
    parts = urlparse(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)))
    path = parts.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", query, ""))


def content_hash(text: str) -> str | None:
    """Hash of the normalized text, so the same article at two URLs is caught. None if too short to trust."""
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()
    if len(normalized) < 500:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migration: databases created before story grouping lack articles.story_id
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(articles)")}
    if "story_id" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN story_id INTEGER REFERENCES stories(id)")
        conn.commit()
    return conn


def known_urls(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT canonical_url FROM articles")}


def hash_exists(conn: sqlite3.Connection, h: str | None) -> bool:
    return h is not None and conn.execute("SELECT 1 FROM articles WHERE content_hash = ?", (h,)).fetchone() is not None


def insert_article(conn: sqlite3.Connection, item, canonical: str, content: str, h: str | None) -> None:
    conn.execute(
        """INSERT INTO articles (canonical_url, url, title, source, published_at, summary, content,
                                 content_hash, hn_points, hn_comments, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            canonical, item.url, item.title, item.source,
            item.published_at.isoformat() if item.published_at else None,
            item.summary, content, h, item.hn_points, item.hn_comments,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
