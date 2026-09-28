"""record.py — Track B run records in Track A's JSONL shape (spec §5)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ccgate.state import ccgate_home
from ccgate.transcript import encode_cwd


def runs_dir() -> Path:
    return ccgate_home() / "runs"


def make_run_id(cwd: str) -> str:
    """Repo-encoding, unique run id: <encoded-cwd>-<utc-stamp>-<short-uuid>."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{encode_cwd(cwd)}-{stamp}-{uuid.uuid4().hex[:8]}"


def assistant_entry(model: str, usage: dict, timestamp: str | None = None) -> dict:
    """One transcript-shaped assistant entry; usage passed through verbatim (spec §5)."""
    ts = timestamp or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {"type": "assistant", "timestamp": ts, "message": {"model": model, "usage": usage}}


class RunRecorder:
    """Append-per-turn writer: survives a crash with partial-but-valid data (spec §5)."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        d = runs_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{run_id}.jsonl"

    def _append(self, obj: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")

    def append_assistant(self, model: str, usage: dict) -> None:
        self._append(assistant_entry(model, usage))

    def finish(self) -> None:
        self._append({"type": "ccgate_run_end", "status": "complete", "run_id": self.run_id})


def is_complete(path: Path) -> bool:
    """True iff the record ends with a ccgate_run_end marker (else crashed/incomplete)."""
    if not path.exists():
        return False
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return False
    try:
        return json.loads(lines[-1]).get("type") == "ccgate_run_end"
    except json.JSONDecodeError:
        return False
