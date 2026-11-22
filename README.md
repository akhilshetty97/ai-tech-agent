# ai-tech-agent

<!-- architecture diagram -->

I wanted a way to keep up with AI agent and infra news without reading Hacker News and a dozen
blogs every morning, and I wanted an excuse to build an agent from scratch. This does both.

Every morning a GitHub Action pulls new posts from Hacker News and 15 engineering blogs, groups
duplicate coverage into stories, and ranks them. The research agent then digs into the top two,
and a short briefing lands in Telegram: what happened, why it matters, and something worth
trying, with links to every source.

The agent loop is written by hand against the Gemini API. There's no LangChain or LangGraph.

## How it works

### Research agent (`agent/`)

A plain loop: send the conversation and tool list to the model,
run whatever tools it asks for, append the results, repeat. It has four tools: `search_web`
(Tavily), `read_url`, `write_note` and `finish`. A run ends when the model calls `finish` with a
report, and the loop rejects the report if it cites a page the agent never read. Page text is
dropped from the conversation after one step to keep the context small; the agent's notes stay.
Every model call and tool call goes to a JSONL trace in `runs/`.

### Evals (`evals/`)

Eight questions with the facts a good answer should contain. The runner
records steps, pages read, failed tool calls, tokens and time for each one, and Gemini Decision 1
checks whether each answer covers the key facts and whether the cited pages back up each claim.

### Discovery pipeline (`pipeline/`)

Fetch, dedupe by canonical URL and content hash, store in
SQLite, group articles about the same news into one story, then score each article with
Gemini Decision 1 (category plus relevance, novelty and impact on a 1-5 rubric). The briefing
takes the top 10 stories and marks them as covered so they don't show up again the next day.

## Setup

Needs Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Create a `.env` in the project root:

```
GEMINI_API_KEY=...
TAVILY_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

To get the chat ID, message your bot once and open
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Usage

```bash
# ask the agent something
uv run python -m agent.loop "How does MCP differ from plain function calling?"

# run the eval set (compares against the previous run)
uv run python -m evals.run_evals
uv run python -m evals.run_evals --only rerankers

# fetch, group and score articles, then print the top 10 stories
uv run python -m pipeline.run
uv run python -m pipeline.run --top-only

# build a briefing; add --send to deliver it, --refresh to fetch first
uv run python -m pipeline.briefing
uv run python -m pipeline.briefing --refresh --send
```

Feeds, the lookback window and the ranking weights live in `pipeline/feeds.yaml`.

## Daily run

`.github/workflows/daily-briefing.yml` runs at 13:00 UTC, or on demand from the Actions tab. Add
the four values above as repository secrets. The SQLite database is carried between runs in the
Actions cache, traces are uploaded as an artifact, and a failed run sends a Telegram message
with a link to the logs.

## Layout

```
agent/      the research agent: loop, tools, Gemini wrapper, tracing
evals/      question set, runner, judge
pipeline/   fetch, store, stories, scoring, briefing, Telegram
runs/       traces and eval results (gitignored)
data/       SQLite database (gitignored)
```

## Known issues

- Faithfulness is around 0.84 on the eval set. Some claims come from the model's own knowledge
  rather than the page it cites, mostly on long pages that get cut at 8,000 characters.
- Vendor changelog posts ("X is now available on Y") still rank higher than they should.
- Gemini's free tier allows 15 requests a minute, so a full eval run hits rate limits and waits.
