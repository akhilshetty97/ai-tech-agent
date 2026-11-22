import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

RUNS_DIR = Path(__file__).parent.parent / "runs"


class Trace:
    """Appends one JSON line per event to runs/<run_id>.jsonl."""

    def __init__(self, question: str):
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        RUNS_DIR.mkdir(exist_ok=True)
        self.path = RUNS_DIR / f"{self.run_id}.jsonl"
        self.pages_path = RUNS_DIR / f"{self.run_id}.pages.jsonl"
        self.log("start", question=question)

    def save_page(self, url: str, text: str) -> None:
        """Keeps the full text the agent read, so the judge can check claims against it."""
        with self.pages_path.open("a") as f:
            f.write(json.dumps({"url": url, "text": text}) + "\n")

    def log(self, kind: str, **fields) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")
