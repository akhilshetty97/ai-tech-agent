import sys

from dotenv import load_dotenv
from google.genai import types
from pydantic import ValidationError

from agent.llm import call_llm, make_tool
from agent.tools.read import ReadUrlArgs, read_url
from agent.tools.search import SearchWebArgs, search_web

SYSTEM = """You are a research agent. Investigate the user's question.
Use search_web to find sources and read_url to read them before relying on them.
When you know enough, answer with a summary and cite the URLs you read."""

# What the model sees: name, description, argument schema
TOOLS = [
    make_tool("search_web", "Search the web. Returns titles, URLs and snippets.", SearchWebArgs),
    make_tool("read_url", "Read the main text of a web page. Use it on promising search results before citing them.", ReadUrlArgs),
]

# What your code runs when the model asks for a tool by name, and how its arguments are checked
RUNNERS = {"search_web": search_web, "read_url": read_url}
ARGS = {"search_web": SearchWebArgs, "read_url": ReadUrlArgs}


def run_tool(call: types.FunctionCall) -> str:
    if call.name not in RUNNERS:
        return f"ERROR: unknown tool {call.name}. Available tools: {', '.join(RUNNERS)}"

    # Validate first: bad arguments come back as a clear message the model can fix
    try:
        args = ARGS[call.name](**(call.args or {}))
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return f"ERROR: invalid arguments for {call.name}: {problems}"

    try:
        return RUNNERS[call.name](**args.model_dump())
    except Exception as e:
        return f"ERROR: {call.name} failed: {e}"


def run_agent(question: str, max_steps: int = 10) -> str:
    # The conversation starts with the user's question
    messages = [types.Content(role="user", parts=[types.Part(text=question)])]

    for step in range(1, max_steps + 1):
        response = call_llm(messages, TOOLS, SYSTEM)
        messages.append(response.content)  # remember what the model said

        if not response.tool_calls:  # no tool requests: the model is done
            return response.text or ""

        # Run every tool the model asked for, and collect one result per call
        results = []
        for call in response.tool_calls:
            print(f"step {step}: {call.name}({call.args})")
            output = run_tool(call)
            if output.startswith("ERROR"):
                print(f"  ↳ {output}")
            results.append(
                types.Part.from_function_response(name=call.name, response={"result": output})
            )

        # Send all results back together, so the model sees them on the next step
        messages.append(types.Content(role="user", parts=results))

    return "Stopped: step limit reached before the agent finished."


if __name__ == "__main__":
    load_dotenv()
    question = " ".join(sys.argv[1:]) or "What is the new Gemini Decisions model?"
    print(run_agent(question))
