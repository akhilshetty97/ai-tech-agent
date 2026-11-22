"""Score articles with Gemini Decision 1, and pick the top N.

One Decisions call per article asks four questions over the same text:
category (choice), and relevance / novelty / impact (rubric scores, 1-5).
"""

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from google import genai

from agent.llm import _is_retryable, _retry_delay

MODEL = "gemini-decision-1"
MAX_INPUT_CHARS = 6_000  # the opening of an article is enough to judge it
MAX_ATTEMPTS = 4

READER = "a software engineer who builds AI agents and RAG systems"

QUESTIONS = {
    "category": {
        "type": "choice",
        "instructions": "Which category does this article belong to?",
        "criteria": {
            "agents": "AI agents and agent frameworks: agent loops, tool use, MCP and agent protocols, "
                      "multi-agent systems, coding agents, agent SDKs (LangGraph, OpenAI Agents SDK, etc.), agent evaluation.",
            "rag_infra": "RAG and AI infrastructure: retrieval, vector databases, embeddings, rerankers, model APIs and "
                         "new models, LLM serving and inference, caching, LLMOps and observability, compute for AI.",
            "other": "Anything else, including AI news without an engineering angle (business, funding, policy) "
                     "and topics unrelated to AI.",
        },
    },
    "relevance": {
        "type": "score",
        "instructions": f"How useful is this article to {READER}?",
        "criteria": [
            "Not about AI engineering at all.",
            "Loosely related to AI; of little use to an engineer building AI systems.",
            "About AI, but mostly business, policy or general news, with little engineering substance.",
            "Useful: concrete tools, techniques or lessons for building agents or RAG systems.",
            "Directly actionable: new capabilities, APIs, patterns or benchmarks the engineer would apply.",
        ],
    },
    "novelty": {
        "type": "score",
        "instructions": "How new is what this article reports?",
        "criteria": [
            "Rehash of old news, a listicle, or promotional content.",
            "Tutorial or explainer of well-known techniques.",
            "Opinion or analysis that adds some new perspective.",
            "A new product feature, release or result of moderate significance.",
            "A major new release, model, technique or finding.",
        ],
    },
    "impact": {
        "type": "score",
        "instructions": f"How much could this change what {READER} builds or chooses?",
        "criteria": [
            "No effect on how engineers build AI systems.",
            "Minor: affects a niche use case.",
            "Moderate: worth knowing for some teams.",
            "Significant: many engineers building agents or RAG would try it or change a decision.",
            "Major: changes the default choice of tools, models or architecture for many teams.",
        ],
    },
}


def _article_input(row: sqlite3.Row) -> str:
    text = row["content"] or row["summary"] or ""
    return f"Title: {row['title']}\nSource: {row['source']}\nURL: {row['url']}\n\n{text[:MAX_INPUT_CHARS]}"


def score_article(row: sqlite3.Row) -> dict:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"api_version": "v1beta"})
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.decisions.create(model=MODEL, input=_article_input(row), questions=QUESTIONS)
            break
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1 or not _is_retryable(e):
                raise
            time.sleep(_retry_delay(e, attempt))

    a = response.answers
    return {
        "article_id": row["id"],
        "category": a["category"].choice,
        "category_confidence": a["category"].confidence,
        # rubric levels are 0-based (0-4); shift to the 1-5 scale used everywhere else
        "relevance": a["relevance"].score + 1,
        "novelty": a["novelty"].score + 1,
        "impact": a["impact"].score + 1,
    }


def score_unscored(conn: sqlite3.Connection) -> tuple[int, int]:
    """Scores every article without a score yet. Returns (scored, failed)."""
    rows = conn.execute(
        "SELECT a.* FROM articles a LEFT JOIN article_scores s ON s.article_id = a.id WHERE s.article_id IS NULL"
    ).fetchall()

    def safe_score(row):
        try:
            return score_article(row)
        except Exception as e:
            print(f"  scoring failed for article {row['id']} ({row['title'][:50]}): {type(e).__name__}")
            return None

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(safe_score, rows))

    now = datetime.now(timezone.utc).isoformat()
    scored = [r for r in results if r]
    conn.executemany(
        """INSERT INTO article_scores (article_id, category, category_confidence, relevance, novelty, impact, model, scored_at)
           VALUES (:article_id, :category, :category_confidence, :relevance, :novelty, :impact, :model, :scored_at)""",
        [{**r, "model": MODEL, "scored_at": now} for r in scored],
    )
    conn.commit()
    return len(scored), len(rows) - len(scored)


def top_stories(conn: sqlite3.Connection, ranking: dict, lookback_days: int) -> list[dict]:
    """On-topic stories from the lookback window, ranked by their best article's weighted score.

    Each story appears once. Its other articles come along as extra sources.
    """
    w = ranking["weights"]
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        """WITH scored AS (
               SELECT a.id, a.story_id, a.title, a.url, a.source, a.hn_points, s.category,
                      s.relevance, s.novelty, s.impact,
                      :wr * s.relevance + :wi * s.impact + :wn * s.novelty AS total
               FROM articles a JOIN article_scores s ON s.article_id = a.id
               WHERE s.category != 'other'
                 AND s.category_confidence >= :min_conf
                 AND COALESCE(a.published_at, a.fetched_at) >= :since
                 AND a.story_id IS NOT NULL
           ),
           ranked AS (
               SELECT *, ROW_NUMBER() OVER (PARTITION BY story_id ORDER BY total DESC) AS rn FROM scored
           )
           SELECT r.* FROM ranked r JOIN stories st ON st.id = r.story_id
           WHERE r.rn = 1 AND st.last_covered_at IS NULL   -- not already in a briefing
           ORDER BY r.total DESC
           LIMIT :n""",
        {
            "wr": w["relevance"], "wi": w["impact"], "wn": w["novelty"],
            "min_conf": ranking["min_category_confidence"], "since": since, "n": ranking["top_n"],
        },
    ).fetchall()

    stories = []
    for row in rows:
        others = conn.execute(
            "SELECT title, url, source, hn_points FROM articles WHERE story_id = ? AND id != ? ORDER BY id",
            (row["story_id"], row["id"]),
        ).fetchall()
        stories.append(dict(row) | {"other_sources": [dict(o) for o in others]})
    return stories
