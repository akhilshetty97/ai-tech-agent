# AI Tech Agent

A daily technology briefing for engineers building AI agents and RAG systems, written by a
hand-built research agent. Every morning it reads Hacker News and 15 engineering blogs, groups
articles into stories, ranks them, researches the top two in depth, and sends a short briefing
to Telegram.

Architecture diagram: [`docs/architecture.excalidraw`](docs/architecture.excalidraw) (open at excalidraw.com).

## Setup

```bash
uv sync
```

`.env` in the project root:

```
GEMINI_API_KEY=...
TAVILY_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Commands

| Command | What it does |
| --- | --- |
| `uv run python -m agent.loop "question"` | Research a question; prints a cited report and the trace path |
| `uv run python -m evals.run_evals` | Run the eval set; prints a scorecard compared with the previous run |
| `uv run python -m pipeline.run` | Fetch, dedupe, group into stories, score; prints the top 10 stories |
| `uv run python -m pipeline.run --top-only` | Print the current top 10 without fetching |
| `uv run python -m pipeline.briefing` | Build a briefing preview (nothing sent) |
| `uv run python -m pipeline.briefing --refresh --send` | The daily job: fetch, build, send to Telegram |

## Layout

```
agent/      research agent: loop, tools (search, read, notes, finish), Gemini wrapper, tracing
evals/      question set, runner, Gemini Decision 1 judge
pipeline/   discovery pipeline (fetch, store, stories, score), briefing, Telegram
runs/       traces and eval results (not committed)
data/       SQLite database (not committed; carried between CI runs in the Actions cache)
.github/    daily GitHub Actions workflow
```

## Daily schedule

`.github/workflows/daily-briefing.yml` runs at 13:00 UTC and can also be started from the
Actions tab ("Run workflow"). It needs four repository secrets: `GEMINI_API_KEY`,
`TAVILY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. If a run fails, it sends a
Telegram alert with a link to the logs.
