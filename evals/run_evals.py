"""Run the eval question set and print a scorecard.

    uv run python -m evals.run_evals                 # all verified questions
    uv run python -m evals.run_evals --only rerankers
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

from agent.llm import MODEL
from agent.loop import run_agent
from evals.judge import judge_completeness, judge_faithfulness, load_pages

EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR.parent / "runs" / "evals"


def load_questions(only: str | None = None) -> list[dict]:
    questions = yaml.safe_load((EVALS_DIR / "questions.yaml").read_text())
    questions = [q for q in questions if q.get("verified")]
    if only:
        questions = [q for q in questions if q["id"] == only]
    return questions


def read_trace(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def _source_matches(url: str, expected: str) -> bool:
    """'github.com/a2aproject' matches https://github.com/a2aproject/A2A; 'pydantic.dev' matches ai.pydantic.dev."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    exp_host, _, exp_path = expected.lower().partition("/")
    host_ok = host == exp_host or host.endswith("." + exp_host)
    return host_ok and parsed.path.lower().startswith("/" + exp_path if exp_path else "/")


def score(question: dict, result, events: list[dict], seconds: float) -> dict:
    """Automatic metrics from the report and the trace. No LLM involved."""
    tools = [e for e in events if e["kind"] == "tool"]
    searches = [e["args"].get("query", "").strip().lower() for e in tools if e["tool"] == "search_web"]
    end = next(e for e in events if e["kind"] == "end")
    expected = question.get("expected_sources", [])

    return {
        "id": question["id"],
        "complete": result.report.complete,
        "steps": end["steps"],
        "searches": len(searches),
        "repeated_searches": len(searches) - len(set(searches)),
        "pages_read": len(result.read_urls),
        "failed_tool_calls": sum(1 for e in tools if e["error"]),
        "finish_rejected": sum(1 for e in events if e["kind"] == "finish_rejected"),
        "nudges": sum(1 for e in events if e["kind"] == "nudge"),
        "findings": len(result.report.findings),
        "expected_source_read": any(_source_matches(u, s) for u in result.read_urls for s in expected),
        "input_tokens": end["input_tokens"],
        "output_tokens": end["output_tokens"],
        "seconds": round(seconds, 1),
        "trace": result.trace_path,
    }


def judge(question: dict, result) -> dict:
    """LLM-judge metrics. A judge failure is recorded, not fatal."""
    try:
        completeness = judge_completeness(result.report, question["key_facts"])
        faithfulness = judge_faithfulness(result.report, load_pages(result.trace_path))
    except Exception as e:
        return {"judge_error": f"{type(e).__name__}: {e}"}
    return {
        "facts": f"{completeness['covered']}/{completeness['total']}",
        "completeness": completeness["score"],
        "faith": f"{faithfulness['supported']}/{faithfulness['total']}",
        "faithfulness": faithfulness["score"],
        "judge_details": {"facts": completeness["facts"], "claims": faithfulness["claims"]},
    }


def _avg(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    n = len(ok) or 1
    return {
        "avg_completeness": _avg([r.get("completeness") for r in ok]),
        "avg_faithfulness": _avg([r.get("faithfulness") for r in ok]),
        "questions": len(rows),
        "crashed": len(rows) - len(ok),
        "complete_rate": sum(r["complete"] for r in ok) / n,
        "expected_source_rate": sum(r["expected_source_read"] for r in ok) / n,
        "avg_steps": sum(r["steps"] for r in ok) / n,
        "avg_pages_read": sum(r["pages_read"] for r in ok) / n,
        "total_failed_tool_calls": sum(r["failed_tool_calls"] for r in ok),
        "total_repeated_searches": sum(r["repeated_searches"] for r in ok),
        "total_finish_rejected": sum(r["finish_rejected"] for r in ok),
        "total_nudges": sum(r["nudges"] for r in ok),
        "avg_input_tokens": sum(r["input_tokens"] for r in ok) / n,
        "avg_seconds": sum(r["seconds"] for r in ok) / n,
    }


def print_scorecard(rows: list[dict], summary: dict, previous: dict | None) -> None:
    cols = [
        ("id", 26), ("complete", 8), ("steps", 5), ("searches", 8), ("repeat", 6),
        ("read", 4), ("failed", 6), ("rejected", 8), ("nudges", 6), ("src", 3),
        ("in_tok", 7), ("secs", 5), ("facts", 5), ("faith", 5),
    ]
    keys = {
        "repeat": "repeated_searches", "read": "pages_read", "failed": "failed_tool_calls",
        "rejected": "finish_rejected", "src": "expected_source_read", "in_tok": "input_tokens",
        "secs": "seconds",
    }
    print("\n" + "  ".join(name.ljust(w) for name, w in cols))
    print("  ".join("-" * w for _, w in cols))
    for r in rows:
        if "error" in r:
            print(f"{r['id'].ljust(26)}  CRASHED: {r['error'][:80]}")
            continue
        cells = []
        for name, w in cols:
            v = r.get(keys.get(name, name), "-")
            v = ("yes" if v else "no") if isinstance(v, bool) else v
            cells.append(str(v).ljust(w))
        print("  ".join(cells))

    for r in rows:
        if "judge_error" in r:
            print(f"judge failed on {r['id']}: {r['judge_error'][:150]}")

    print("\nSummary" + ("  (change vs previous run)" if previous else ""))
    for key, value in summary.items():
        line = f"  {key:24} {value:.2f}" if isinstance(value, float) else f"  {key:24} {value}"
        if previous and previous.get(key) is not None and value is not None:
            delta = value - previous[key]
            if delta:
                line += f"   ({delta:+.2f})"
        print(line)


def latest_previous_summary() -> dict | None:
    files = sorted(RESULTS_DIR.glob("*.json"))
    return json.loads(files[-1].read_text())["summary"] if files else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run a single question by id")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--no-judge", action="store_true", help="skip the Decision 1 judge")
    parser.add_argument("--label", default="", help="what this run tests, e.g. 'prompt-v2'")
    args = parser.parse_args()

    load_dotenv()
    questions = load_questions(args.only)
    if not questions:
        raise SystemExit("No verified questions to run.")

    previous = None if args.only else latest_previous_summary()
    rows = []
    for i, q in enumerate(questions, 1):
        print(f"\n[{i}/{len(questions)}] {q['id']}: {q['question']}")
        start = time.perf_counter()
        try:
            result = run_agent(q["question"], max_steps=args.max_steps)
            row = score(q, result, read_trace(result.trace_path), time.perf_counter() - start)
            if not args.no_judge:
                row.update(judge(q, result))
            rows.append(row)
        except Exception as e:  # one crash shouldn't stop the whole eval run
            rows.append({"id": q["id"], "error": f"{type(e).__name__}: {e}"})

    summary = summarize(rows)
    print_scorecard(rows, summary, previous)

    if not args.only:  # only full runs become the baseline for the next comparison
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps({"label": args.label, "model": MODEL, "summary": summary, "rows": rows}, indent=2))
        print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
