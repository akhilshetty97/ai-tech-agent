# AI Tech Intelligence Agent: What We're Building and How

Nov 17, 2026 · @Akhil

## What it is

We're building a daily technology briefing for software engineers, written by a research agent. Every morning it picks up the latest developments in AI agents and AI infrastructure, decides which ones matter, looks into the most important ones, and gives you a short briefing. You don't have to ask it anything. It exists because there's too much to keep up with.

It does three things for you:

1. **Briefs you every morning, without being asked.** It reads Hacker News and a set of engineering blogs, ranks what's new, investigates the top 2–3 items, and writes a short digest: what happened, why it matters, what to do about it.
2. **Answers your questions when you have them.** When a digest item catches your eye, or you need to compare tools, you can ask directly. For example: "Compare LangGraph, OpenAI Agents SDK and Pydantic AI on state management." You get a report where every claim links to a page the agent actually read.
3. **Shows its work.** Every run leaves a trace of each model call and tool call, with tokens, cost and time, plus an eval scorecard that says whether a change made it better or worse.

The briefing needs no question from you, but the agent still gets one: for each top item, the pipeline writes it ("What is this, how does it work, and how does it compare with what exists?"). The same agent loop does the work in both cases, so building the briefing still teaches you agents.

## How it fits together

It's one Python application with one research agent at the center. Everything else either calls the agent (the command line, the eval harness, the page, the MCP server, the daily pipeline) or is called by it (search, web pages, the database).

&#91;embedded content: architecture · arrows point from caller to what it calls\]

Arrows point from the caller to what it calls. The pipeline hands its top 2–3 items to the agent; everything above the agent is a different way to start a run. Until Milestone 5, runs and traces are saved as files instead of in PostgreSQL.

## The research agent

The agent is a loop you write yourself: call the model with the conversation and a list of tools, run whatever tools it asks for, add the results to the conversation, and repeat. It stops when the model returns a final report, or when a limit is hit.

&#91;embedded content: research agent loop · 2 decisions, 3 exits\]

The model decides *what* to do next; your code decides *whether it's allowed* and records everything.

**Tools**

| Tool | Input | Returns | From |
| --- | --- | --- | --- |
| `search_web` | query, max results | Title, URL and snippet for each result | M1 |
| `read_url` | URL | Main text of the page, cut to a length limit (later, only the passages relevant to the question) | M1 |
| `finish` | the report | Ends the run; the report is checked against the schema | M3 |
| `get_article_details` | article ID | A stored article with its scores | M5 |

**What the loop keeps track of**

- The message list sent to the model each step.
- Sources: every URL found, read and cited.
- Notes the agent writes as it goes, so long page text can be dropped from the context (M3).
- Step count, tokens used and cost so far.

**Limits**

- A maximum number of steps (start with 10).
- A token budget per run.
- A timeout on each tool call, with retries for network errors.

**Report format**

```python
class Finding(BaseModel):
    claim: str
    source_urls: list[str]   # must be URLs the agent actually read

class Report(BaseModel):
    question: str
    summary: str
    findings: list[Finding]
    open_questions: list[str]
    complete: bool           # False if a limit stopped the run
```

For comparison questions, a later version (M4) adds a planner and one subagent per item being compared. Each subagent runs this same loop with its own fresh context, and a final step merges their reports.

## Discovery pipeline and daily digest

The pipeline is a fixed sequence of steps, not an agent: the steps are always the same, so there's nothing for a model to decide about what to do next. LLM calls appear only where judgment about text is needed (scoring and writing). It runs once every morning.

1. **Fetch.** Pull new items from the Hacker News API and 10–15 RSS feeds.
2. **Normalize.** Convert each item to one article format: canonical URL, title, source, date, main text.
3. **Deduplicate.** Skip anything whose canonical URL or content hash is already stored.
4. **Store.** Save new articles in PostgreSQL.
5. **Score.** One structured LLM call per article returns category, relevance, novelty, impact (each 1–5) and a one-line reason. Articles outside the two categories are dropped.
6. **Select.** Rank by a weighted score and keep the top 10.
7. **Investigate.** Run the research agent on the 2–3 items with the highest impact, with a question like "What is this, how does it work, and how does it compare with existing options?"
8. **Write the digest.** One LLM call turns the top 10 and the research reports into the briefing. Each item has: what happened, why it matters, an engineering takeaway, and source links.
9. **Save.** Store the digest so the page can show it.

The agent can also read stored articles through its `get_article_details(article_id)` tool. That means a research question can start from something already in the database instead of a fresh web search.

## Ways to use it

There are three ways in, and all of them call the same agent code.

| Interface | What you can do | Arrives at |
| --- | --- | --- |
| Command line | `research "<question>"` prints the report and saves its trace; `evals run` prints the scorecard; `pipeline run` runs the daily pipeline by hand | M1, M2, M5 |
| Simple page (FastAPI) | Read today's digest, ask a research question, browse past reports and open their traces | M7 |
| MCP server | Use `search_web`, `read_url`, `get_article_details` and `run_research` from any MCP client | M7 |

The command line is where you'll spend most of your time. The page and the MCP server come last because they wrap the agent; they don't change how it works.

## A run, end to end

This is an illustrative run of `research "How does MCP differ from plain function calling?"`, showing what the model decides and what your code does at each step. The trace file records exactly these rows.

| Step | The model decides | Your code does |
| --- | --- | --- |
| 1 | Search for the MCP specification and an overview | Calls the search API, returns 5 results with titles, URLs, snippets |
| 2 | Read the spec's introduction and one explainer, in parallel | Fetches both pages, extracts the main text, trims it, records both URLs as read |
| 3 | Write key points to notes; search for how function calling works in one provider's docs | Saves the notes, drops the long page text from context, runs the search |
| 4 | Read the provider's tool-use docs | Fetch fails with a timeout; the error goes back to the model as the tool result |
| 5 | Try a second result instead | Fetches it, records it as read |
| 6 | It knows enough: call `finish(report)` | Validates the report against the schema, checks every cited URL was read, saves report and trace |

If the model had kept searching, the loop would stop at the step limit or the token budget and return whatever it had, marked incomplete. Every way the loop can end is deliberate.

## What it is not

These are left out on purpose, so time goes into the agent:

- **Not a news aggregator.** It shows a ranked top 10 plus investigated items, not a feed of everything.
- **Not a multi-user product.** No sign-in, no personalization, no saved items, no feedback buttons.
- **Not a framework demo.** The agent is written by hand first; LangGraph comes in Milestone 6 as a comparison, not a starting point.
- **Not a distributed system.** One Python process, one PostgreSQL database, Docker Compose on your machine. No queues, vector database or cloud deployment.
- **Not a fleet of agents.** One research agent, plus subagents only where Milestone 4 shows with numbers that they help.
