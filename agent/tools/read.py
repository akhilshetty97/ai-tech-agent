import threading

import httpx
import trafilatura
from pydantic import BaseModel, ConfigDict, Field

# trafilatura parses HTML with lxml (C code), which can crash the process when several threads
# parse at once. Downloads still run in parallel; only the parsing step goes one at a time.
_PARSE_LOCK = threading.Lock()


class ReadUrlArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # reject arguments the model invents

    url: str = Field(pattern=r"^https?://", description="Full URL of the page to read, starting with http:// or https://")


def read_url(url: str, max_chars: int = 8000) -> str:
    try:
        response = httpx.get(
            url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (research-agent)"},
        )
        response.raise_for_status()
    except Exception as e:
        return f"ERROR: could not fetch {url}: {e}"

    with _PARSE_LOCK:
        text = trafilatura.extract(response.text)
    if not text:
        return f"ERROR: no readable text at {url} (may need JavaScript). Try another source."

    return text[:max_chars]
