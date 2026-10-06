"""session_end.py — SessionEnd hook: finalize session ledger and write report."""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ccgate.ledger import compute_net
from ccgate.state import acquire_lock, ccgate_home, read_session, write_session
from ccgate.transcript import read_transcript


def _session_input_tokens(transcript_path: str) -> int:
    path = Path(transcript_path)
    if not path.exists():
        return 0
    try:
        return sum(r.usage.input_tokens for r in read_transcript(path))
    except Exception:
        return 0


def main() -> None:
    if os.environ.get("CCGATE_DISABLE") == "1":
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    transcript_path = payload.get("transcript_path", "")

    try:
        input_tokens = _session_input_tokens(transcript_path)

        ledger: dict = {}
        session: dict = {}
        with acquire_lock(session_id):
            session = read_session(session_id)
            if not session:
                return
            ledger = compute_net(session, input_tokens)
            session["ledger"] = ledger
            write_session(session_id, session)

        now = datetime.now(UTC)
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        today = now.strftime("%Y-%m-%d")
        report_dir = ccgate_home() / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / f"{today}-{session_id[:8]}.txt"
        net = ledger.get("net", 0)
        avoided = ledger.get("tokens_avoided", 0)
        injected = ledger.get("tokens_injected", 0)
        call_count = len(session.get("tool_calls", []))
        line = (
            f"{ts} {session_id[:8]} net={net:+d} "
            f"avoided={avoided} injected={injected} tool_calls={call_count}\n"
        )
        with open(report_file, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


if __name__ == "__main__":
    main()
