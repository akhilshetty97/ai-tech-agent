"""LLM judge using Gemini Decision 1.

Two checks, both yes/no questions that Decision 1 answers with a calibrated probability:
  - completeness: does the report state each key fact?        (input: the report)
  - faithfulness: does a cited page support each claim?       (input: the page text)
"""

import json
import os
import time
from pathlib import Path

from google import genai

from agent.llm import _is_retryable, _retry_delay
from agent.schemas import Report

JUDGE_MODEL = "gemini-decision-1"
THRESHOLD = 0.5  # P(true) at or above this counts as "yes"
MAX_ATTEMPTS = 4


def _ask(input_text: str, questions: dict[str, str]) -> dict[str, float]:
    """One Decisions call: several yes/no questions over the same input. Returns P(yes) per question."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"api_version": "v1beta"})
    payload = {
        qid: {
            "type": "noul",
            "instructions": text,
            "criteria": {
                "true_description": "Yes. The text states this, in any wording.",
                "false_description": "No. The text does not state this, or states something different.",
            },
        }
        for qid, text in questions.items()
    }
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.decisions.create(model=JUDGE_MODEL, input=input_text, questions=payload)
            return {qid: answer.noul for qid, answer in response.answers.items()}
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1 or not _is_retryable(e):
                raise
            time.sleep(_retry_delay(e, attempt))


def report_as_text(report: Report) -> str:
    lines = [f"Question: {report.question}", f"Summary: {report.summary}", "Findings:"]
    lines += [f"- {f.claim}" for f in report.findings]
    return "\n".join(lines)  # open questions are left out: an unanswered question is not a stated fact


def judge_completeness(report: Report, key_facts: list[str]) -> dict:
    questions = {
        f"fact_{i}": f"Does this research report state the following fact? Fact: {fact}"
        for i, fact in enumerate(key_facts)
    }
    probs = _ask(report_as_text(report), questions)
    facts = [{"fact": fact, "p": round(probs[f"fact_{i}"], 3)} for i, fact in enumerate(key_facts)]
    covered = sum(f["p"] >= THRESHOLD for f in facts)
    return {"covered": covered, "total": len(facts), "score": covered / len(facts), "facts": facts}


def judge_faithfulness(report: Report, pages: dict[str, str]) -> dict:
    # Group claims by the page they cite, so each page is sent once with all its claims
    claims_by_page: dict[str, list[int]] = {}
    for i, finding in enumerate(report.findings):
        for url in finding.source_urls:
            if url in pages:
                claims_by_page.setdefault(url, []).append(i)

    # A claim is supported if at least one of its cited pages supports it
    best = {i: 0.0 for i in range(len(report.findings))}
    for url, claim_ids in claims_by_page.items():
        questions = {
            f"claim_{i}": f"Does this web page support the following claim? Claim: {report.findings[i].claim}"
            for i in claim_ids
        }
        for qid, p in _ask(pages[url], questions).items():
            i = int(qid.split("_")[1])
            best[i] = max(best[i], p)

    claims = [{"claim": f.claim, "p": round(best[i], 3)} for i, f in enumerate(report.findings)]
    supported = sum(c["p"] >= THRESHOLD for c in claims)
    total = len(claims)
    return {"supported": supported, "total": total, "score": supported / total if total else None, "claims": claims}


def load_pages(trace_path: str) -> dict[str, str]:
    path = Path(trace_path).with_suffix(".pages.jsonl")
    if not path.exists():
        return {}
    pages = {}
    for line in path.read_text().splitlines():
        page = json.loads(line)
        pages[page["url"]] = page["text"]
    return pages
