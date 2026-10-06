"""post_tool.py — PostToolUse hook: silent tool-response measurement (Phase 1)."""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

from ccgate.config import load_config
from ccgate.state import acquire_lock, ccgate_home, read_session, write_session


def _default_session(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "started_at": datetime.now(UTC).isoformat(),
        "model": None,
        "tool_calls": [],
        "tool_profile": {},
        "ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0, "net_usd": 0.0},
        "notices_emitted": 0,
        "statusline_snapshot": None,
    }


def _update_profile(profile: dict, tool: str, tokens_est: int) -> None:
    if tool not in profile:
        profile[tool] = {"count": 0, "mean_tokens": 0, "max_tokens": 0, "total_tokens": 0}
    p = profile[tool]
    p["count"] += 1
    p["total_tokens"] += tokens_est
    p["mean_tokens"] = p["total_tokens"] // p["count"]
    p["max_tokens"] = max(p["max_tokens"], tokens_est)


def main() -> None:
    if os.environ.get("CCGATE_DISABLE") == "1":
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "Unknown")
    tool_response = payload.get("tool_response", "")
    model = payload.get("model")

    notice = ""
    try:
        config = load_config()
        unbounded = config.get("unboundedOutputTokens", 10_000)
        max_notices = config.get("maxNoticesPerSession", 4)

        response_chars = len(tool_response)
        tokens_est = response_chars // 4
        ts = datetime.now(UTC).isoformat()

        with acquire_lock(session_id):
            session = read_session(session_id) or _default_session(session_id)

            if model and not session.get("model"):
                session["model"] = model

            seq = len(session["tool_calls"]) + 1
            record = {
                "seq": seq,
                "tool": tool_name,
                "response_chars": response_chars,
                "tokens_est": tokens_est,
                "notice_bytes": 0,
                "ts": ts,
            }
            session["tool_calls"].append(record)
            _update_profile(session["tool_profile"], tool_name, tokens_est)

            # Read statusline snapshot if available
            snap_path = ccgate_home() / "sessions" / f"{session_id}-statusline.json"
            if snap_path.exists():
                try:
                    session["statusline_snapshot"] = json.loads(
                        snap_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    pass

            # AdditionalContext gate (I2 — only when unbounded output detected)
            if tokens_est > unbounded and session["notices_emitted"] < max_notices:
                notice = (
                    f"{tool_name} returned ~{tokens_est:,} tokens"
                    " — consider a tighter offset/limit."
                )
                session["notices_emitted"] += 1
                session["tool_calls"][-1]["notice_bytes"] = len(notice)

            write_session(session_id, session)
    except Exception:
        pass

    if notice:
        print(json.dumps({"hookSpecificOutput": {"additionalContext": notice}}))


if __name__ == "__main__":
    main()
