Here's a full mock run of `run_agent`, modeled on the real run from earlier. For each step it shows what's called, what comes back, and how `messages` grows.

**Question:** `"What is the latest MCP spec version?"`

---

## Setup (before the loop)

```python
messages = [types.Content(role="user", parts=[types.Part(text=question)])]
```

`messages` now holds one entry:
```
[0] user:  "What is the latest MCP spec version?"
```

---

## Step 1: the model searches

**① Call the model:**
```python
response = call_llm(messages, TOOLS, SYSTEM)
```
What goes to Gemini: the 1 message, plus the 2 tool definitions, plus the system prompt.

**② `response` that comes back (an `LLMResponse`):**
```python
LLMResponse(
    content=Content(role="model", parts=[
        Part(function_call=FunctionCall(name="search_web",
                                        args={"query": "MCP specification latest version"}))
    ]),
    text=None,                                  # no text, only a tool request
    tool_calls=[FunctionCall(name="search_web", args={"query": "MCP specification latest version"})],
    input_tokens=180, output_tokens=22,
)
```

**③ Remember it:** `messages.append(response.content)`
```
[0] user:   "What is the latest MCP spec version?"
[1] model:  function_call search_web(query="MCP specification latest version")
```

**④ Check whether it's done:** `response.tool_calls` isn't empty, so it isn't. Continue.

**⑤ Run each tool call:**
```python
for call in response.tool_calls:          # 1 call this step
    print("step 1: search_web({'query': 'MCP specification latest version'})")
    output = run_tool(call)
```
Inside `run_tool`:
```python
runner = RUNNERS["search_web"]                               # → your search_web function
return runner(**{"query": "MCP specification latest version"})
# same as: search_web(query="MCP specification latest version")
```
`output`, the string your function returned:
```
MCP 2026-07-28 Specification Released
https://blog.modelcontextprotocol.io/posts/2026-07-28
The largest revision of the protocol since launch...

Specification - Model Context Protocol
https://modelcontextprotocol.io/specification
...
```

**⑥ Wrap it as a function response:**
```python
results = [Part.from_function_response(name="search_web", response={"result": output})]
```

**⑦ Send the results back:** `messages.append(Content(role="user", parts=results))`
```
[0] user:   "What is the latest MCP spec version?"
[1] model:  function_call search_web(...)
[2] user:   function_response search_web → "MCP 2026-07-28 Specification Released ..."
```

---

## Step 2: the model reads a page

**① `call_llm` with all 3 messages.** The model now sees its own request *and* the search results.

**② `response`:**
```python
tool_calls=[FunctionCall(name="read_url",
                         args={"url": "https://blog.modelcontextprotocol.io/posts/2026-07-28"})]
text=None
input_tokens=520, output_tokens=30          # input grew: the search results are now in the context
```

**③–⑦ Same steps as before:**
```
print → "step 2: read_url({'url': 'https://blog.modelcontextprotocol.io/posts/2026-07-28'})"
run_tool → read_url(url="https://...") → "The 2026-07-28 revision moves MCP from a stateful ..." (up to 8000 chars)
```
`messages`:
```
[0] user:   question
[1] model:  function_call search_web(...)
[2] user:   function_response search_web → results
[3] model:  function_call read_url(url=".../2026-07-28")
[4] user:   function_response read_url → "The 2026-07-28 revision moves MCP ..." (long page text)
```

---

## Step 3: the model answers

**① `call_llm` with all 5 messages.**

**② `response`:**
```python
LLMResponse(
    content=Content(role="model", parts=[Part(text="The latest MCP spec is 2026-07-28. It ...")]),
    text="The latest MCP spec is 2026-07-28. It ...",
    tool_calls=[],                               # empty: the model is done
    input_tokens=2600, output_tokens=310,        # the page text made the input much bigger
)
```

**③** It gets appended as `messages[5]`.

**④** `if not response.tool_calls:` is **True**, so the function runs `return response.text`.

**Final output:**
```
step 1: search_web({'query': 'MCP specification latest version'})
step 2: read_url({'url': 'https://blog.modelcontextprotocol.io/posts/2026-07-28'})
The latest MCP spec is 2026-07-28. It ...
```

---

## Variations on the same flow

**The model asks for 2 tools in one step:**
```python
tool_calls=[FunctionCall("read_url", {"url": A}), FunctionCall("read_url", {"url": B})]
```
The inner `for` loop runs both. `results` holds 2 parts, and both go back in **one** message:
```
[n]   model:  function_call read_url(A), function_call read_url(B)
[n+1] user:   function_response read_url → A text, function_response read_url → B text
```

**A tool fails:**
```python
output = read_url("https://slow-site.com")  # → "ERROR: could not fetch ...: timed out"
```
The error string goes back as an ordinary result. At the next step the model reads it and usually tries another URL. Nothing crashes.

**The model makes up a tool name:**
```python
run_tool(FunctionCall("fetch_pdf", {...}))  # → "ERROR: unknown tool fetch_pdf"
```

**The model never stops:** after step 10 the `for` loop ends and the function returns `"Stopped: step limit reached before the agent finished."`

---

## The pattern

| Step | Model output      | Loop action               | `messages` length | Input tokens |
| ---- | ----------------- | ------------------------- | ----------------- | ------------ |
| 1    | `search_web` call | run search, append result | 1 → 3             | 180          |
| 2    | `read_url` call   | run read, append result   | 3 → 5             | 520          |
| 3    | text answer       | **return**                | 5 → 6             | 2,600        |

**Input tokens grow at every step** because the whole conversation is sent again each time, and page text is large. In a 10-step run with several pages read, that growth is what drives cost, and it's what Milestone 3 (notes, trimming) addresses. The trace you build next will record these numbers for real instead of this mock.