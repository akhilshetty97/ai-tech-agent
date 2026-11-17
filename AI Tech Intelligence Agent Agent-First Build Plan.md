# AI Tech Intelligence Agent: Agent-First Build Plan

Nov 17, 2026 · @Akhil

## Goal and principles

The goal is to learn how to build agents. The product is the vehicle: a daily briefing on AI agents and AI infrastructure that arrives every morning without you asking, because there's too much to keep up with. The research agent behind the briefing comes first; ingestion, the digest and delivery arrive only once there is an agent worth feeding.

- **Agent first, plumbing second.** Backend work you already know comes after the agent loop is solid.
- **Raw SDK before a framework.** Write the loop by hand, then move to LangGraph and compare.
- **Observable from day one.** Every LLM call and tool call is traced with tokens, cost and latency.
- **Evals before features.** From Milestone 2 on, every change is rerun against the same question set.
- **Workflow vs. agent.** Use a fixed pipeline where the steps are known; use an agent only where the next step depends on what it finds.
- **Each milestone works on its own.** You can stop after any milestone and have something useful.

| Area | Original plan | Updated plan |
| --- | --- | --- |
| Main use case | Dashboard plus on-demand research | A daily briefing that arrives without being asked; your own questions come second |
| First milestone | Ingestion from RSS, Hacker News, GitHub | Research agent on the command line |
| Tracing and cost tracking | Milestone 7 | Milestone 1 |
| Evaluation | Milestone 6 | Milestone 2, rerun at every milestone after |
| Categories | 6 | 2 |
| Dashboard | Next.js app with 4 areas | One simple page, plus the briefing by email or Slack |
| Personalization, Saved page | In the MVP | Cut |
| Deployment | Docker on AWS, Milestone 7 | One small always-on machine, Milestone 8 |
| New agent topics | None | Context management, subagents, LangGraph comparison, MCP server, trajectory evals |

## Scope

The MVP is a research agent you can question, fed by a small discovery pipeline that produces a daily digest. It covers two categories at first, chosen so you can judge the output yourself.

**In scope**

- Daily briefing, unprompted: every morning it ranks what's new, investigates the top 2–3 items, and sends you a short digest by email or Slack.
- Research agent: searches, reads, decides when it knows enough, and writes a sourced report. It answers the pipeline's questions every day and yours whenever you ask.
- Categories: **AI agents and frameworks** and **RAG and AI infrastructure**. Others are added later through config.
- Sources: web search and page reading for the agent first; Hacker News and 10–15 RSS feeds for discovery from Milestone 5.
- No repeats: a story already covered in a recent briefing doesn't come back.
- One simple page plus an MCP server, so you can use the agent from the browser and from an MCP client.
- One small always-on machine, so the briefing runs while your laptop is asleep.

**Cut or deferred**

- Personalization, user preferences, feedback buttons
- Saved and bookmarked items
- Users, sign-in, multiple accounts
- GitHub and arXiv ingestion (add after Milestone 8 if you want them)
- Embeddings, Qdrant, Kafka, Kubernetes
- A full Next.js dashboard
- A multi-service cloud setup (one machine running Docker Compose is enough)

## Tech stack

The stack stays in Python and uses tools you already know, so all new learning goes into the agent. Each piece arrives only at the milestone that needs it.

| Component | Choice | From |
| --- | --- | --- |
| Language | Python, managed with uv | M0 |
| LLM | One provider's official SDK (Anthropic, OpenAI or Gemini) behind a thin wrapper of your own | M0 |
| Schemas | Pydantic for tool arguments and the report format | M1 |
| Web search | Tavily, or another search API | M1 |
| Page reading | httpx plus trafilatura to pull the main text out of a page | M1 |
| Tracing | Your own JSONL trace per run; optionally Langfuse later | M1 |
| Storage | JSON files until M5, then PostgreSQL in Docker Compose | M5 |
| Scheduling | APScheduler or plain cron | M6 |
| Delivery | Email through a transactional email service, or a Slack incoming webhook | M6 |
| Agent framework | LangGraph, as a port of the hand-written agent | M6 |
| MCP | The official MCP Python SDK | M7 |
| API and page | FastAPI with one simple page | M7 |
| Hosting | One small cloud VM running Docker Compose | M8 |

Switching LLM providers later is a good exercise, but pick one for now. Writing your own wrapper teaches you what the message formats have in common.

## Milestones

There are nine milestones in four phases, about 22–28 days of focused work in total. Plumbing starts only at Milestone 5, and the first briefing reaches you at Milestone 6. The estimates are rough; the "done when" line is what counts.

&#91;embedded content: roadmap · 4 phases, 9 milestones, 3 gates\]

Each diamond is a gate: don't start the next phase until its condition is met.

### Milestone 0 — One tool call, by hand (0.5–1 day)

**Goal:** see exactly what tool calling looks like at the API level.

- Make one LLM call that offers a single trivial tool (a calculator or a clock).
- Run the tool yourself and send the result back.
- Print the raw request and response at every step.

**You learn:** message roles, tool schemas, how tool-call IDs link a call to its result.

**Done when:** you can explain every field in one round trip.

### Milestone 1 — Research agent v1, on the command line (3–4 days)

**Goal:** an agent that investigates a question and returns a sourced report.

- Tools: `search_web(query)` and `read_url(url)`.
- The loop: call the model, run the tools it asks for, append results, repeat until it answers.
- Limits: a maximum number of steps and a token budget.
- Tool errors go back to the model as tool results, so it can recover instead of crashing.
- Source tracking: which URLs were found, which were read, which were cited.
- Final report as a Pydantic model: summary, findings with citations, open questions.
- A JSONL trace per run: every model call and tool call with tokens, cost and latency.

**You learn:** the agent loop, stopping conditions, structured output, why traces matter.

**Done when:** `research "How does MCP differ from plain function calling?"` returns a cited report, and you can follow its reasoning step by step in the trace.

### Milestone 2 — Evaluation harness (2 days)

**Goal:** a way to tell whether a change made the agent better or worse.

- Write 10–15 research questions you can judge, each with notes: key facts a good answer must include, sources it should find.
- Automatic checks: every finding has a citation; every cited URL was actually read.
- An LLM judge for completeness and for whether the cited sources support the claims.
- Trajectory metrics: steps taken, repeated searches, failed tool calls.
- Cost and latency per question.

**You learn:** evaluating answers and evaluating the path taken to them.

**Done when:** one command runs the whole set and prints a scorecard you can compare between runs.

### Milestone 3 — Make the agent hold up (3–4 days)

**Goal:** handle long pages, slow tools and hard questions without breaking.

- Context management: cut long pages down, or have a cheaper model pull out only the passages relevant to the question.
- A notes scratchpad the agent writes to, so old page text can be dropped from the context.
- Parallel tool calls, with timeouts and retries on network errors.
- An explicit `finish(report)` tool, compared against simply letting the model stop talking.
- Prompt caching for the parts of the conversation that repeat on every step.

**You learn:** context is the main constraint in real agents.

**Done when:** a three-framework comparison runs without overflowing the context, and eval scores hold or improve while cost per report goes down.

### Milestone 4 — Orchestrator and subagents (2–3 days)

**Goal:** find out when splitting work across agents actually helps.

- A planner breaks a comparison into one sub-question per item.
- One subagent per item, each with its own fresh context.
- A synthesizer combines their findings using the same criteria for every item.
- Run the eval set on both the single agent and this version.

**You learn:** fan-out costs more and is not always better; you will have numbers to show when it is.

**Done when:** you have a table of quality, cost and latency for both versions, and know which one to use for which kind of question.

### Milestone 5 — Discovery pipeline (3–4 days)

**Goal:** find developments worth investigating. This is a workflow, not an agent.

- Ingest from the Hacker News API and 10–15 RSS feeds.
- Normalize to one article format; deduplicate by canonical URL and content hash.
- Group articles into stories, so the same news from Hacker News and a blog counts once.
- Mark stories already covered in a recent briefing, so they don't come back unless something new happened.
- Store in PostgreSQL; move research runs and traces there too.
- Score each story with one structured LLM call: category, relevance, novelty, impact, and a one-line reason.
- Give the agent a third tool, `get_article_details(article_id)`.

**You learn:** where a fixed pipeline with LLM steps beats an agent.

**Done when:** repeated runs create no duplicates, and the top 10 looks right to you on three days in a row.

### Milestone 6 — Daily digest, then LangGraph (3–4 days)

**Goal:** a scheduled daily briefing, and an understanding of what a framework adds.

- Write a research question for each top item with one LLM call, and check it's specific enough to investigate.
- Run the research agent on the 2–3 items with the highest impact.
- Write the briefing: what happened, why it matters, an engineering takeaway, and source links for each item. Save it.
- Send it by email or Slack every morning, with a link to the page for more.
- Set a daily cost cap; if it's reached, the briefing goes out with fewer investigated items.
- Port the research agent to LangGraph: explicit state, nodes, conditional edges, checkpointing.
- Add a human-approval pause before an expensive deep-research run you start yourself.
- Run the eval set on the LangGraph version.

**You learn:** what LangGraph gives you, and what it costs, compared with your own loop.

**Done when:** the briefing lands in your inbox or Slack every morning without you starting it, it doesn't repeat yesterday's stories, and the LangGraph agent scores at least as well as the hand-written one.

### Milestone 7 — MCP server and a simple page (2–3 days)

**Goal:** use the agent from the outside.

- Expose `search_web`, `read_url`, `get_article_details` and `run_research` as an MCP server.
- Connect it from an MCP client you already use and try it there.
- One page: today's digest, a box to ask a research question, past reports with links to their traces.

**You learn:** MCP from the server side.

**Done when:** you can start a research run both from the page and from an MCP client.

### Milestone 8 — Run it unattended (2 days)

**Goal:** the briefing keeps arriving when nobody is watching.

- Move Docker Compose to one small always-on cloud VM, so the schedule doesn't depend on your laptop being awake.
- Retry a failed source or tool call, and skip a source that keeps failing instead of failing the whole run.
- Alert yourself (email or Slack) when no briefing was produced, or when the daily cost cap was hit.
- Back up the database.

**You learn:** what it takes to trust an agent that runs without you.

**Done when:** the briefing has arrived on time for a week straight, and a deliberately broken source produces an alert instead of silence.

## Data model

Until Milestone 5, runs and traces are JSON files on disk. At Milestone 5 they move into PostgreSQL alongside the articles. The tables are built around research runs and their traces, because those are what you'll study most.

| Table | Key fields | Added in |
| --- | --- | --- |
| `research_runs` | id, question, variant (single, fan-out, LangGraph), status, report\_json, tokens\_in, tokens\_out, cost\_usd, latency\_ms, created\_at | M5 (files before) |
| `run_steps` | run\_id, step\_no, kind (llm or tool), tool\_name, input, output\_summary, tokens, latency\_ms, error | M5 (files before) |
| `research_sources` | run\_id, url, title, was\_read, was\_cited | M5 (files before) |
| `eval_results` | eval\_run\_id, question\_id, run\_id, scores\_json, created\_at | M5 (files before) |
| `articles` | id, canonical\_url, title, source, published\_at, content, content\_hash, story\_id, fetched\_at | M5 |
| `stories` | id, title, first\_seen\_at, last\_covered\_at (one row per story, so the same news from two sources counts once) | M5 |
| `article_scores` | article\_id, category, relevance, novelty, impact, reason, model, scored\_at | M5 |
| `digests` | id, digest\_date, content\_json, status, cost\_usd, delivered\_at, created\_at | M6 |
| `digest_items` | digest\_id, story\_id, rank, research\_run\_id | M6 |

The original plan's `user_preferences` and `saved_articles` tables are gone, since those features were cut.

## Evaluation

Evals start at Milestone 2. From then on, every milestone reports its scores against the same question set. Each change should come with numbers, not just an impression that it's better.

| Metric | How it's measured | From |
| --- | --- | --- |
| Citation coverage | Share of findings that carry a source (automatic) | M2 |
| Citation faithfulness | LLM judge reads the cited passage: does it support the claim? | M2 |
| Completeness | LLM judge checks the answer against your notes on key facts | M2 |
| Read-before-cite | Every cited URL was actually fetched (automatic) | M2 |
| Wasted steps | Repeated searches, failed tool calls, total steps | M2 |
| Cost per report | USD from token counts in the trace | M2 |
| Latency per report | Seconds from the trace | M2 |
| **Ranking precision** | Share of the daily top 10 you'd actually read; hand-label 30–50 articles. **The metric that decides whether the briefing is worth opening.** | M5 |
| Duplicate rate | The same story stored or shown twice, within a day or across days | M5 |
| Question quality | LLM judge: is the generated question specific enough to investigate? | M6 |
| Daily reliability | Days the briefing arrived on time, and its cost against the daily cap | M8 |

Three habits keep the evals trustworthy:

- **Check the judge.** Grade 10 outputs by hand and see how often the LLM judge agrees with you before trusting it.
- **Don't edit questions.** Add new ones instead, so scores stay comparable over time.
- **Read failing traces.** A low score tells you something went wrong; the trace tells you what.

## Project structure and first steps

The layout starts with only the agent and its evals; the other folders appear at the milestones that need them.

```
ai-tech-intelligence/
├── agent/
│   ├── llm.py          # thin wrapper around one provider's SDK
│   ├── loop.py         # the agent loop
│   ├── tools/          # search_web, read_url, later get_article_details
│   ├── schemas.py      # Pydantic models: tool arguments, report
│   └── tracing.py      # one JSONL trace per run
├── evals/
│   ├── questions.yaml  # questions + notes on what a good answer contains
│   └── run_evals.py
├── pipeline/           # M5+: ingestion, scoring, digest
├── mcp_server/         # M7
├── api/                # M7: FastAPI + one page
├── runs/               # traces and reports (gitignored)
├── tests/
├── docker-compose.yml  # PostgreSQL, from M5
└── pyproject.toml
```

**First steps**

- [ ] Pick one LLM provider and get an API key.
- [ ] Get a search API key (Tavily or similar).
- [ ] Milestone 0: one tool-calling round trip, printing the raw messages.
- [ ] Write 5 research questions whose answers you already know; they seed the eval set.
- [ ] Milestone 1: the loop with `search_web` and `read_url`, capped at 10 steps, with a trace file per run.
