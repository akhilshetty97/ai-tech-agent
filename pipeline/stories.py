"""Group articles about the same news into stories.

For each article without a story:
  1. Find candidate stories cheaply: recent stories whose titles share 2+ distinctive words.
  2. If there are candidates, ask Gemini Decision 1 (one call, one yes/no per candidate):
     "Is this article about the same news as <candidate>?"
  3. Join the most likely story if P(yes) >= 0.5; otherwise start a new story.
"""

import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from google import genai

from agent.llm import _is_retryable, _retry_delay

MODEL = "gemini-decision-1"
WINDOW_DAYS = 7          # only stories first seen this recently can gain new articles
MIN_SHARED_WORDS = 2
THRESHOLD = 0.5
MAX_ATTEMPTS = 4

# Words that appear in many unrelated titles, so sharing them says nothing
STOPWORDS = set("""
a an and are as at be by for from has have how in into is it its of on or our that the this to was what
when why will with you your we new now introducing announcing announces launch launches launched release
releases released welcome available update updates ainews using use build building guide vs via ai
""".split())


def title_words(title: str) -> set[str]:
    """Distinctive words: lowercase, keeps tokens like 'gpt-6.2' and 'qwen4-max' whole."""
    words = re.findall(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]", title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def _same_news_probabilities(article: sqlite3.Row, candidates: list[sqlite3.Row]) -> dict[int, float]:
    """One Decisions call: P(same news) for each candidate story."""
    text = (article["content"] or article["summary"] or "")[:1500]
    questions = {
        f"story_{c['story_id']}": {
            "type": "noul",
            "instructions": (
                "Is this article about the same news event as the following article? "
                "Count it as the same if it reports, comments on, or follows up on the same release, "
                "announcement or event, even from a different angle. "
                f"Other article: {c['title']}. {(c['snippet'] or '')[:400]}"
            ),
            "criteria": {
                "true_description": "Yes, both cover the same release, announcement or event.",
                "false_description": "No, they are about different news, even if the topic is related.",
            },
        }
        for c in candidates
    }
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"api_version": "v1beta"})
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.decisions.create(
                model=MODEL, input=f"Title: {article['title']}\n\n{text}", questions=questions
            )
            return {int(qid.split("_")[1]): a.noul for qid, a in response.answers.items()}
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1 or not _is_retryable(e):
                raise
            time.sleep(_retry_delay(e, attempt))


def group_new_articles(conn: sqlite3.Connection) -> dict:
    """Assigns every article without a story to an existing or new story. Returns counts."""
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    unassigned = conn.execute(
        "SELECT * FROM articles WHERE story_id IS NULL ORDER BY COALESCE(published_at, fetched_at)"
    ).fetchall()

    # Recent stories, represented by their first article (title + a snippet for the yes/no question)
    stories = [
        dict(row) | {"words": title_words(row["title"])}
        for row in conn.execute(
            """SELECT s.id AS story_id, s.title,
                      (SELECT substr(COALESCE(a.content, a.summary), 1, 400) FROM articles a
                       WHERE a.story_id = s.id ORDER BY a.id LIMIT 1) AS snippet
               FROM stories s WHERE s.first_seen_at >= ?""",
            (since,),
        )
    ]

    counts = {"new_stories": 0, "joined": 0, "llm_calls": 0}
    for article in unassigned:
        words = title_words(article["title"])
        candidates = [s for s in stories if len(words & s["words"]) >= MIN_SHARED_WORDS]

        story_id = None
        if candidates:
            probs = _same_news_probabilities(article, candidates[:20])
            counts["llm_calls"] += 1
            best_id, best_p = max(probs.items(), key=lambda kv: kv[1])
            if best_p >= THRESHOLD:
                story_id = best_id

        if story_id is None:
            first_seen = article["published_at"] or article["fetched_at"]
            story_id = conn.execute(
                "INSERT INTO stories (title, first_seen_at) VALUES (?, ?)", (article["title"], first_seen)
            ).lastrowid
            snippet = (article["content"] or article["summary"] or "")[:400]
            stories.append({"story_id": story_id, "title": article["title"], "snippet": snippet, "words": words})
            counts["new_stories"] += 1
        else:
            counts["joined"] += 1

        conn.execute("UPDATE articles SET story_id = ? WHERE id = ?", (story_id, article["id"]))
    conn.commit()
    return counts
