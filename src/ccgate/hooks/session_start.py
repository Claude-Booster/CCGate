"""session_start.py — SessionStart hook: flush read cache on compact/resume (G14)."""
from __future__ import annotations

import json
import os
import sys

from ccgate.state import acquire_lock, ccgate_home


def main() -> None:
    if os.environ.get("CCGATE_DISABLE") == "1":
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    source = payload.get("source", "")
    if source not in ("compact", "resume"):
        return

    session_id = payload.get("session_id", "unknown")

    try:
        rc_path = ccgate_home() / "sessions" / f"{session_id}-read-cache.json"
        if not rc_path.exists():
            return
        with acquire_lock(f"{session_id}-read-cache"):
            try:
                data = json.loads(rc_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            data["reads"] = {}
            data["file_log"] = []
            data["flushed_at"] = f"session_start:{source}"
            tmp = rc_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(rc_path))
    except Exception:
        pass


if __name__ == "__main__":
    main()
