import os

from pydantic import BaseModel, ConfigDict, Field
from tavily import TavilyClient


class SearchWebArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # reject arguments the model invents

    query: str = Field(min_length=2, description="One specific search query, e.g. 'MCP specification 2026'")
    max_results: int = Field(default=5, ge=1, le=10, description="Number of results to return (1-10)")


def search_web(query: str, max_results: int = 5) -> str:
    try:
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        results = client.search(query, max_results=max_results)["results"]
    except Exception as e:
        return f"ERROR: search failed: {e}"

    if not results:
        return "No results. Try a different query."

    return "\n\n".join(
        f"{r['title']}\n{r['url']}\n{r['content'][:300]}" for r in results
    )
