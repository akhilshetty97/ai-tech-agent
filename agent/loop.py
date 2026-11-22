import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from dotenv import load_dotenv
from google.genai import types
from pydantic import BaseModel, ValidationError

from agent.llm import call_llm, make_tool
from agent.schemas import FinishArgs, Report
from agent.tools.notes import WriteNoteArgs, write_note
from agent.tools.read import ReadUrlArgs, read_url
from agent.tools.search import SearchWebArgs, search_web
from agent.tracing import Trace

SYSTEM = """You are a research agent. Investigate the user's question thoroughly.

How to work:
1. Break the question into its parts. In a comparison, each item being compared is a separate part.
2. Use search_web to find sources for each part. Prefer official documentation and primary sources over blogs.
3. Read at least 2-3 relevant pages with read_url before finishing. Search snippets are not enough:
   only cite pages you have read.
4. After reading pages, call write_note once per page, with only facts that page states.
   Don't add facts from memory or from other pages: each note must match its source_url. Page text is removed
   from the conversation after one step to save space; your notes stay. Facts you don't note are lost.
5. You can call several tools in one step: read 2-3 pages at once, or note several pages at once.
6. Before calling finish, check that every part of the question is covered by your notes.
   If a part is missing, research it first.

Report: call finish with a short summary and specific findings. One fact per finding, with concrete
details (names, mechanisms, numbers), each citing pages you read. Do not answer in plain text."""

# What the model sees: name, description, argument schema
TOOLS = [
    make_tool("search_web", "Search the web. Returns titles, URLs and snippets.", SearchWebArgs),
    make_tool("read_url", "Read the main text of a web page. Use it on promising search results before citing them.", ReadUrlArgs),
    make_tool("write_note", "Save key facts stated on ONE page you read (one call per page). Notes stay in the conversation; page text does not.", WriteNoteArgs),
    make_tool("finish", "Submit your final report. Every finding must cite URLs you have read with read_url.", FinishArgs),
]

# What your code runs when the model asks for a tool by name, and how its arguments are checked.
# finish is not here: the loop handles it, because it ends the run.
RUNNERS = {"search_web": search_web, "read_url": read_url, "write_note": write_note}
ARGS = {"search_web": SearchWebArgs, "read_url": ReadUrlArgs, "write_note": WriteNoteArgs}

PAGE_REMOVED = "[Page text removed to save context. Use your notes on this page.]"

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


def run_agent(question: str, max_steps: int = 15) -> RunResult:
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
        tool_calls = [c for c in response.tool_calls if c.name != "finish"]
        finish_calls = [c for c in response.tool_calls if c.name == "finish"]

        # Tools in one step don't depend on each other, so run them in parallel
        for call in tool_calls:
            print(f"step {step}: {call.name}({_short(call.args)})")
        with ThreadPoolExecutor(max_workers=5) as pool:
            timed = list(pool.map(lambda c: _timed_run(c, read_urls), tool_calls))

        results = []
        for call, (output, latency_ms) in zip(tool_calls, timed):
            error = output.startswith("ERROR")
            if error:
                print(f"  ↳ {output}")
            elif call.name == "read_url":
                read_urls.append(call.args["url"])
                trace.save_page(call.args["url"], output)
            trace.log(
                "tool",
                step=step,
                tool=call.name,
                args=call.args,
                error=error,
                output_chars=len(output),
                output_preview=output[:300],
                latency_ms=latency_ms,
            )
            results.append(types.Part.from_function_response(name=call.name, response={"result": output}))

        for call in finish_calls:
            print(f"step {step}: finish({_short(call.args)})")
            output = _handle_finish(call, read_urls, trace, step)
            if isinstance(output, FinishArgs):  # accepted: the run is over
                report = Report(question=question, complete=True, **output.model_dump())
                return _end(trace, report, "done", step, read_urls, totals)
            print(f"  ↳ {output}")
            results.append(types.Part.from_function_response(name=call.name, response={"result": output}))

        # Page text from earlier steps has had its chance to be noted: drop it before adding new results
        removed = _remove_old_pages(messages)
        if removed:
            trace.log("compact", step=step, pages_removed=removed)

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


def _timed_run(call: types.FunctionCall, read_urls: list[str]) -> tuple[str, int]:
    start = time.perf_counter()
    note_url = (call.args or {}).get("source_url", "") if call.name == "write_note" else None
    if note_url is not None and _normalize(note_url) not in {_normalize(u) for u in read_urls}:
        output = f"ERROR: {note_url} has not been read. Only note pages you have read with read_url."
    else:
        output = run_tool(call)
    return output, round((time.perf_counter() - start) * 1000)


def _remove_old_pages(messages: list[types.Content]) -> int:
    """Replaces read_url results already in the conversation with a short stub.

    Called before the newest results are appended, so the model always sees a page for
    exactly one step: the step where it can write notes on it.
    """
    removed = 0
    for message in messages:
        if message.role != "user":
            continue
        for i, part in enumerate(message.parts or []):
            fr = part.function_response
            if fr and fr.name == "read_url" and fr.response.get("result") not in (None, PAGE_REMOVED):
                if not str(fr.response["result"]).startswith("ERROR"):
                    message.parts[i] = types.Part.from_function_response(
                        name="read_url", response={"result": PAGE_REMOVED}
                    )
                    removed += 1
    return removed


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
