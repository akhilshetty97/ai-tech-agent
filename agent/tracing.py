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
        self.log("start", question=question)

    def log(self, kind: str, **fields) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")
