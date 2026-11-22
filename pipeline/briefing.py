"""Daily briefing: top stories -> research the top few -> write the briefing -> send to Telegram.

    uv run python -m pipeline.briefing                    # build and print a preview; nothing is sent or marked
    uv run python -m pipeline.briefing --send             # build, send to Telegram, mark stories as covered
    uv run python -m pipeline.briefing --refresh --send   # run the discovery pipeline first (the daily job)
"""

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agent.llm import generate_structured
from agent.loop import run_agent
from pipeline import store, telegram
from pipeline.score import top_stories

CONFIG = Path(__file__).parent / "feeds.yaml"

WRITER_SYSTEM = """You write a short daily technology briefing for software engineers who build
AI agents and RAG systems. Be concrete and plain: say what happened, why an engineer should care,
and what they might do about it. No hype, no filler. Use only the information given to you;
if something is unclear from the material, leave it out rather than guess."""


class DigestItem(BaseModel):
    story_id: int = Field(description="The story_id given in the input, unchanged")
    headline: str = Field(description="A short, specific headline, under 12 words")
    what_happened: str = Field(description="1-2 sentences: what was released or announced, with concrete details")
    why_it_matters: str = Field(description="1 sentence: why an engineer building agents or RAG should care")
    takeaway: str = Field(description="1 sentence: something concrete the engineer could try, check or decide")


class Digest(BaseModel):
    intro: str = Field(description="One sentence summing up today's main theme")
    items: list[DigestItem]


def _story_block(conn, story: dict, report) -> str:
    content = conn.execute("SELECT content, summary FROM articles WHERE id = ?", (story["id"],)).fetchone()
    excerpt = (content["content"] or content["summary"] or "")[:1500]
    others = ", ".join(o["source"] for o in story["other_sources"]) or "none"
    block = (
        f"story_id: {story['story_id']}\n"
        f"title: {story['title']}\n"
        f"source: {story['source']} (also covered by: {others})\n"
        f"category: {story['category']}\n"
        f"excerpt: {excerpt}"
    )
    if report:
        findings = "\n".join(f"- {f.claim}" for f in report.findings)
        block += f"\n\nRESEARCH REPORT (investigated in depth):\nsummary: {report.summary}\nfindings:\n{findings}"
    return block


def build(conn, config: dict) -> dict | None:
    stories = top_stories(conn, config["ranking"], config["lookback_days"])
    if not stories:
        return None

    # The research agent digs into the stories with the highest impact
    n = config["briefing"]["investigate"]
    to_investigate = sorted(stories, key=lambda s: -s["impact"])[:n]
    research = {}
    for story in to_investigate:
        question = (f'What is "{story["title"]}", how does it work, '
                    "and how does it compare with existing options?")
        print(f"\nResearching story {story['story_id']}: {question}")
        research[story["story_id"]] = run_agent(question)

    print("\nWriting the briefing...")
    prompt = "Write today's briefing, one item per story below, in the same order.\n\n" + "\n\n---\n\n".join(
        _story_block(conn, s, research[s["story_id"]].report if s["story_id"] in research else None)
        for s in stories
    )
    digest = generate_structured(prompt, WRITER_SYSTEM, Digest)

    # Keep our ranking and our links: the writer only supplies the wording
    written = {item.story_id: item for item in digest.items}
    items = []
    for rank, story in enumerate(stories, 1):
        item = written.get(story["story_id"])
        if item is None:
            continue  # the writer skipped it; better to drop than invent
        result = research.get(story["story_id"])
        research_links = []
        if result:
            research_links = list(dict.fromkeys(u for f in result.report.findings for u in f.source_urls))[:3]
        items.append({
            "rank": rank,
            "story_id": story["story_id"],
            "category": story["category"],
            **item.model_dump(exclude={"story_id"}),
            "url": story["url"],
            "source": story["source"],
            "other_sources": [{"source": o["source"], "url": o["url"]} for o in story["other_sources"]],
            "investigated": result is not None,
            "research_links": research_links,
            "research_trace": result.trace_path if result else None,
        })
    return {"date": datetime.now(timezone.utc).date().isoformat(), "intro": digest.intro, "items": items}


def render_blocks(briefing: dict) -> list[str]:
    """Telegram HTML, one block per item, so messages can be split between items."""
    e = html.escape
    date = datetime.fromisoformat(briefing["date"]).strftime("%a %d %b %Y")
    blocks = [f"<b>🗞 AI Tech Briefing · {date}</b>\n<i>{e(briefing['intro'])}</i>"]
    for item in briefing["items"]:
        badge = "  🔍" if item["investigated"] else ""
        links = [f'<a href="{e(item["url"])}">{e(item["source"])}</a>']
        links += [f'<a href="{e(o["url"])}">{e(o["source"])}</a>' for o in item["other_sources"][:3]]
        block = (
            f"<b>{item['rank']}. {e(item['headline'])}</b>{badge}\n"
            f"{e(item['what_happened'])}\n"
            f"<b>Why it matters:</b> {e(item['why_it_matters'])}\n"
            f"<b>Takeaway:</b> {e(item['takeaway'])}\n"
            f"{' · '.join(links)}"
        )
        if item["research_links"]:
            research = " · ".join(f'<a href="{e(u)}">[{i}]</a>' for i, u in enumerate(item["research_links"], 1))
            block += f"\n🔍 Researched: {research}"
        blocks.append(block)
    blocks.append("<i>🔍 = investigated by the research agent</i>")
    return blocks


def as_plain_text(block: str) -> str:
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2 (\1)", block)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def save(conn, briefing: dict) -> int:
    now = datetime.now(timezone.utc).isoformat()
    digest_id = conn.execute(
        "INSERT INTO digests (digest_date, content_json, status, created_at) VALUES (?, ?, 'draft', ?)",
        (briefing["date"], json.dumps(briefing), now),
    ).lastrowid
    conn.executemany(
        "INSERT INTO digest_items (digest_id, story_id, rank, research_trace) VALUES (?, ?, ?, ?)",
        [(digest_id, i["story_id"], i["rank"], i["research_trace"]) for i in briefing["items"]],
    )
    conn.commit()
    return digest_id


def mark_delivered(conn, digest_id: int, briefing: dict) -> None:
    """Only after a successful send: these stories won't appear in future briefings."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE digests SET status = 'delivered', delivered_at = ? WHERE id = ?", (now, digest_id))
    conn.executemany(
        "UPDATE stories SET last_covered_at = ? WHERE id = ?",
        [(now, i["story_id"]) for i in briefing["items"]],
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="send to Telegram and mark the stories as covered")
    parser.add_argument("--refresh", action="store_true", help="run the discovery pipeline first")
    args = parser.parse_args()

    load_dotenv()
    config = yaml.safe_load(CONFIG.read_text())
    if args.refresh:
        from pipeline.run import ingest
        ingest(config, no_content=False)

    conn = store.connect()
    briefing = build(conn, config)
    if briefing is None:
        print("No new stories today.")
        if args.send:
            telegram.send(["🗞 AI Tech Briefing: no new stories worth your time today."])
        return

    digest_id = save(conn, briefing)
    blocks = render_blocks(briefing)
    print("\n" + "\n\n".join(as_plain_text(b) for b in blocks))

    if args.send:
        telegram.send(telegram.split(blocks))
        mark_delivered(conn, digest_id, briefing)
        print(f"\nSent to Telegram. Digest {digest_id} delivered; {len(briefing['items'])} stories marked as covered.")
    else:
        print(f"\nPreview only (digest {digest_id} saved as draft). Run with --send to deliver it.")


if __name__ == "__main__":
    main()
