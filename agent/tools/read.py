import httpx
import trafilatura
from pydantic import BaseModel, ConfigDict, Field


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

    text = trafilatura.extract(response.text)
    if not text:
        return f"ERROR: no readable text at {url} (may need JavaScript). Try another source."

    return text[:max_chars]
