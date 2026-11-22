"""Discovery pipeline: fetch -> normalize -> dedupe -> store -> group into stories -> score -> top N.

    uv run python -m pipeline.run
    uv run python -m pipeline.run --no-content     # skip fetching page text (fast)
    uv run python -m pipeline.run --top-only       # just print the current top N
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agent.tools.read import read_url
from pipeline import store
from pipeline.fetch import fetch_all
from pipeline.score import score_unscored, top_stories
from pipeline.stories import group_new_articles

CONFIG = Path(__file__).parent / "feeds.yaml"


def page_text(item) -> str:
    """Main text of the page; falls back to the RSS summary when the page can't be read."""
    text = read_url(item.url, max_chars=20_000)
    return item.summary if text.startswith("ERROR") else text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-content", action="store_true", help="don't fetch page text for new articles")
    parser.add_argument("--top-only", action="store_true", help="skip fetching and scoring; print the top N")
    args = parser.parse_args()

    load_dotenv()
    config = yaml.safe_load(CONFIG.read_text())
    if not args.top_only:
        ingest(config, args.no_content)

    conn = store.connect()
    print_top(top_stories(conn, config["ranking"], config["lookback_days"]))


def ingest(config: dict, no_content: bool) -> None:
    start = time.perf_counter()

    # 1. Fetch
    items, errors = fetch_all(config["feeds"], config["hn_top_stories"], config["lookback_days"])

    # 2-3. Normalize URLs and drop anything already stored, or repeated within this run
    conn = store.connect()
    seen = store.known_urls(conn)
    new = []
    for item in items:
        canonical = store.canonical_url(item.url)
        if canonical in seen:
            continue
        seen.add(canonical)
        new.append((item, canonical))
    url_dupes = len(items) - len(new)

    # Page text only for new articles, in parallel
    if no_content:
        texts = [item.summary for item, _ in new]
    else:
        with ThreadPoolExecutor(max_workers=10) as pool:
            texts = list(pool.map(lambda pair: page_text(pair[0]), new))

    # 4. Store, skipping the same text published at a different URL
    inserted, hash_dupes, by_source = 0, 0, {}
    for (item, canonical), text in zip(new, texts):
        h = store.content_hash(text)
        if store.hash_exists(conn, h):
            hash_dupes += 1
            continue
        store.insert_article(conn, item, canonical, text, h)
        inserted += 1
        by_source[item.source] = by_source.get(item.source, 0) + 1
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    print(f"Fetched {len(items)} items from {len(config['feeds']) + 1 - len(errors)} sources "
          f"in {time.perf_counter() - start:.0f}s")
    print(f"  new: {inserted}   duplicate URL: {url_dupes}   duplicate content: {hash_dupes}   total stored: {total}")
    for source, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"    {n:4}  {source}")
    for source, error in errors.items():
        print(f"  FAILED {source}: {error}")

    # 5. Group articles about the same news into stories
    start = time.perf_counter()
    g = group_new_articles(conn)
    print(f"Grouped into stories in {time.perf_counter() - start:.0f}s: "
          f"{g['new_stories']} new stories, {g['joined']} articles joined an existing story "
          f"({g['llm_calls']} Decision 1 checks)")

    # 6. Score everything not scored yet (normally just today's new articles)
    start = time.perf_counter()
    scored, failed = score_unscored(conn)
    print(f"Scored {scored} articles in {time.perf_counter() - start:.0f}s" + (f" ({failed} failed)" if failed else ""))


def print_top(stories) -> None:
    print(f"\nTop {len(stories)} stories   (score = best article's weighted relevance / impact / novelty, each 1-5)\n")
    for i, r in enumerate(stories, 1):
        hn = f"  HN {r['hn_points']}" if r["hn_points"] else ""
        print(f"{i:2}. {r['total']:.2f}  [{r['category']}]  {r['title'][:80]}")
        print(f"          rel {r['relevance']:.1f}  imp {r['impact']:.1f}  nov {r['novelty']:.1f}  ·  {r['source']}{hn}")
        print(f"          {r['url']}")
        for o in r["other_sources"]:
            print(f"          + {o['source']}: {o['title'][:70]}")


if __name__ == "__main__":
    main()
