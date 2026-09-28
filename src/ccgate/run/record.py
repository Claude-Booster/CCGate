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
