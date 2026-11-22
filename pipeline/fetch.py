"""Fetch new items from Hacker News and RSS feeds. No LLM here."""

import calendar
import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

HN_API = "https://hacker-news.firebaseio.com/v0"
HEADERS = {"User-Agent": "Mozilla/5.0 (tech-briefing-bot)"}


@dataclass
class Item:
    url: str
    title: str
    source: str
    published_at: datetime | None
    summary: str = ""            # RSS description, HTML stripped
    hn_points: int | None = None
    hn_comments: int | None = None


def _get_json(url: str):
    response = httpx.get(url, timeout=15, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def fetch_hn(limit: int) -> list[Item]:
    ids = _get_json(f"{HN_API}/topstories.json")[:limit]
    with ThreadPoolExecutor(max_workers=20) as pool:
        stories = list(pool.map(lambda i: _get_json(f"{HN_API}/item/{i}.json"), ids))

    items = []
    for s in stories:
        # Skip Ask HN / text posts (no url), and deleted or dead stories
        if not s or s.get("type") != "story" or not s.get("url") or s.get("dead") or s.get("deleted"):
            continue
        items.append(Item(
            url=s["url"],
            title=s.get("title", ""),
            source="Hacker News",
            published_at=datetime.fromtimestamp(s["time"], tz=timezone.utc),
            hn_points=s.get("score"),
            hn_comments=s.get("descendants"),
        ))
    return items


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _entry_date(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc) if parsed else None


def fetch_feed(name: str, url: str, since: datetime) -> list[Item]:
    response = httpx.get(url, timeout=15, follow_redirects=True, headers=HEADERS)
    response.raise_for_status()
    feed = feedparser.parse(response.content)

    items = []
    for entry in feed.entries:
        published = _entry_date(entry)
        if published and published < since:
            continue  # feeds often include years of old posts
        if not entry.get("link"):
            continue
        items.append(Item(
            url=entry.link,
            title=_strip_html(entry.get("title", "")),
            source=name,
            published_at=published,
            summary=_strip_html(entry.get("summary", ""))[:2000],
        ))
    return items


def fetch_all(feeds: list[dict], hn_limit: int, lookback_days: int) -> tuple[list[Item], dict[str, str]]:
    """Fetches every source in parallel. A failing source is reported, never fatal.

    Returns (items, errors) where errors maps source name -> error message.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    jobs = {"Hacker News": lambda: fetch_hn(hn_limit)}
    for f in feeds:
        jobs[f["name"]] = lambda f=f: fetch_feed(f["name"], f["url"], since)

    items, errors = [], {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        for name, future in futures.items():
            try:
                items.extend(future.result())
            except Exception as e:
                errors[name] = f"{type(e).__name__}: {e}"[:200]
    return items, errors
