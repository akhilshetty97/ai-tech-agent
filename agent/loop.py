import sys
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from google.genai import types
from pydantic import BaseModel, ValidationError

from agent.llm import call_llm, make_tool
from agent.schemas import FinishArgs, Report
from agent.tools.read import ReadUrlArgs, read_url
from agent.tools.search import SearchWebArgs, search_web
from agent.tracing import Trace

SYSTEM = """You are a research agent. Investigate the user's question.
Use search_web to find sources, then read_url to read the most promising ones.
Search snippets are not enough: only cite pages you have read with read_url.
When you know enough, call finish with your report. Do not answer in plain text."""

# What the model sees: name, description, argument schema
TOOLS = [
    make_tool("search_web", "Search the web. Returns titles, URLs and snippets.", SearchWebArgs),
    make_tool("read_url", "Read the main text of a web page. Use it on promising search results before citing them.", ReadUrlArgs),
    make_tool("finish", "Submit your final report. Every finding must cite URLs you have read with read_url.", FinishArgs),
]

# What your code runs when the model asks for a tool by name, and how its arguments are checked.
# finish is not here: the loop handles it, because it ends the run.
RUNNERS = {"search_web": search_web, "read_url": read_url}
ARGS = {"search_web": SearchWebArgs, "read_url": ReadUrlArgs}

NUDGE = "Do not answer in plain text. Call finish with your report, citing only pages you have read."


def _validate(args_model: type[BaseModel], call: types.FunctionCall) -> BaseModel | str:
    """Returns the validated arguments, or an ERROR string the model can act on."""
    try:
        return args_model(**(call.args or {}))
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return f"ERROR: invalid arguments for {call.name}: {problems}"


def run_tool(call: types.FunctionCall) -> str:
    if call.name not in RUNNERS:
        return f"ERROR: unknown tool {call.name}. Available tools: {', '.join(RUNNERS)}, finish"

    args = _validate(ARGS[call.name], call)
    if isinstance(args, str):
        return args

    try:
        return RUNNERS[call.name](**args.model_dump())
    except Exception as e:
        return f"ERROR: {call.name} failed: {e}"


def _normalize(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def check_citations(args: FinishArgs, read_urls: list[str]) -> list[str]:
    """Returns the cited URLs that were never read (empty list means the report is OK)."""
    read = {_normalize(u) for u in read_urls}
    cited = [u for f in args.findings for u in f.source_urls]
    return sorted({u for u in cited if _normalize(u) not in read})


@dataclass
class RunResult:
    report: Report
    status: str            # "done" or "step_limit"
    read_urls: list[str]   # pages the agent actually read
    trace_path: str


def run_agent(question: str, max_steps: int = 10) -> RunResult:
    trace = Trace(question)
    read_urls: list[str] = []  # source tracking: only successful reads count
    totals = {"input_tokens": 0, "output_tokens": 0}

    # The conversation starts with the user's question
    messages = [types.Content(role="user", parts=[types.Part(text=question)])]

    for step in range(1, max_steps + 1):
        start = time.perf_counter()
        response = call_llm(messages, TOOLS, SYSTEM)
        totals["input_tokens"] += response.input_tokens
        totals["output_tokens"] += response.output_tokens
        trace.log(
            "llm",
            step=step,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=round((time.perf_counter() - start) * 1000),
            tool_calls=[c.name for c in response.tool_calls],
        )
        messages.append(response.content)  # remember what the model said

        # Plain text is no longer a valid way to end: nudge the model to call finish
        if not response.tool_calls:
            print(f"step {step}: answered in text, nudging it to call finish")
            trace.log("nudge", step=step, text_preview=(response.text or "")[:300])
            messages.append(types.Content(role="user", parts=[types.Part(text=NUDGE)]))
            continue

        # Run the other tools first, so pages read in this step count; handle finish last
        calls = sorted(response.tool_calls, key=lambda c: c.name == "finish")

        results = []
        for call in calls:
            print(f"step {step}: {call.name}({_short(call.args)})")

            if call.name == "finish":
                output = _handle_finish(call, read_urls, trace, step)
                if isinstance(output, FinishArgs):  # accepted: the run is over
                    report = Report(question=question, complete=True, **output.model_dump())
                    return _end(trace, report, "done", step, read_urls, totals)
            else:
                start = time.perf_counter()
                output = run_tool(call)
                error = output.startswith("ERROR")
                if not error and call.name == "read_url":
                    read_urls.append(call.args["url"])
                trace.log(
                    "tool",
                    step=step,
                    tool=call.name,
                    args=call.args,
                    error=error,
                    output_chars=len(output),
                    output_preview=output[:300],
                    latency_ms=round((time.perf_counter() - start) * 1000),
                )

            if output.startswith("ERROR"):
                print(f"  ↳ {output}")
            results.append(
                types.Part.from_function_response(name=call.name, response={"result": output})
            )

        # Send all results back together, so the model sees them on the next step
        messages.append(types.Content(role="user", parts=results))

    # Every way out is deliberate: a limit produces a report that says it is incomplete
    report = Report(
        question=question,
        summary="Stopped at the step limit before the agent submitted a report.",
        findings=[],
        open_questions=[question],
        complete=False,
    )
    return _end(trace, report, "step_limit", max_steps, read_urls, totals)


def _handle_finish(call, read_urls, trace, step) -> FinishArgs | str:
    """Accepts the report (returns FinishArgs) or rejects it (returns an ERROR string)."""
    args = _validate(FinishArgs, call)
    if isinstance(args, str):
        trace.log("finish_rejected", step=step, reason="invalid_args", detail=args)
        return args

    unread = check_citations(args, read_urls)
    if unread:
        trace.log("finish_rejected", step=step, reason="unread_citations", unread=unread)
        return (
            f"ERROR: report rejected. These URLs were cited but never read: {unread}. "
            "Read them with read_url, or remove them from your findings, then call finish again."
        )
    return args


def _end(trace, report, status, steps, read_urls, totals) -> RunResult:
    trace.log("end", status=status, steps=steps, read_urls=read_urls, report=report.model_dump(), **totals)
    return RunResult(report=report, status=status, read_urls=read_urls, trace_path=str(trace.path))


def _short(args: dict | None) -> str:
    text = str(args or {})
    return text if len(text) <= 120 else text[:120] + "..."


def print_report(report: Report) -> None:
    print(f"\n{report.summary}\n")
    for i, f in enumerate(report.findings, 1):
        print(f"{i}. {f.claim}")
        for url in f.source_urls:
            print(f"     {url}")
    if report.open_questions:
        print("\nOpen questions:")
        for q in report.open_questions:
            print(f"  - {q}")
    if not report.complete:
        print("\n(incomplete: a limit stopped the run)")


if __name__ == "__main__":
    load_dotenv()
    question = " ".join(sys.argv[1:]) or "What is the new Gemini Decisions model?"
    result = run_agent(question)
    print_report(result.report)
    print(f"\nTrace: {result.trace_path}")
