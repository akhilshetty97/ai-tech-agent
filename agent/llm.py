import os
import time
from dataclasses import dataclass

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

MODEL = "gemini-3.5-flash-lite"


@dataclass
class LLMResponse:
    content: types.Content
    text: str | None
    tool_calls: list[types.FunctionCall]
    input_tokens: int
    output_tokens: int


def make_tool(name: str, description: str, args_model: type[BaseModel]) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=name,
        description=description,
        parameters_json_schema=args_model.model_json_schema(),
    )


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, errors.ServerError):  # 500, 503 overloaded, 504 timeout
        return True
    if isinstance(e, errors.ClientError):  # 4xx: only rate limits are worth retrying
        return e.code == 429
    return True  # network errors: dropped connection, timeout


def call_llm(messages: list[types.Content], tools: list[types.FunctionDeclaration], system: str) -> LLMResponse:
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=60_000),  # ms; without this a stuck call hangs forever
    )
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=tools)],
    )

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=MODEL, contents=messages, config=config)
            break
        except Exception as e:
            if attempt == 2 or not _is_retryable(e):
                raise
            print(f"  LLM call failed ({e}), retrying...")
            time.sleep(2 ** attempt * 2)  # 2s, then 4s

    usage = response.usage_metadata
    content = response.candidates[0].content
    text = "".join(p.text for p in content.parts or [] if p.text) or None
    return LLMResponse(
        content=content,
        text=text,
        tool_calls=response.function_calls or [],
        input_tokens=usage.prompt_token_count or 0,
        output_tokens=usage.candidates_token_count or 0,
    )
