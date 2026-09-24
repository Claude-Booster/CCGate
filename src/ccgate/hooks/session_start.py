"""session_start.py — SessionStart hook: flush read cache (G14), inject task state (A4),
charge ledger (I3)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from ccgate.config import load_config
from ccgate.state import acquire_lock, ccgate_home, read_session, write_session

_MARKER = "\n...[truncated]"  # 14 chars; budget = max_chars - len(_MARKER)


def _write_startup_findings(session_id: str, findings: list[dict]) -> None:
    path = ccgate_home() / "sessions" / f"{session_id}-startup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"session_id": session_id, "findings": findings}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _check_pin_mismatch(config: dict) -> list[dict]:
    if not config.get("pinCacheTtl", True):
        return []
    findings = []
    if not os.environ.get("ENABLE_PROMPT_CACHING_1H"):
        findings.append({
            "type": "pin_mismatch",
            "key": "ENABLE_PROMPT_CACHING_1H",
            "message": "A1: ENABLE_PROMPT_CACHING_1H absent — run 'ccgate shape --fix'",
        })
    if not os.environ.get("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"):
        findings.append({
            "type": "pin_mismatch",
            "key": "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
            "message": "A1: CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL absent — run 'ccgate shape --fix'",
        })
    return findings


def _truncate_at_line(content: str, max_chars: int) -> str:
    """Truncate at last newline within (max_chars - len(_MARKER)); result <= max_chars."""
    if max_chars <= len(_MARKER):
        return ""
    if len(content) <= max_chars:
        return content
    budget = max_chars - len(_MARKER)
    truncated = content[:budget]
    last_nl = truncated.rfind("\n")
    if last_nl > 0:
        truncated = truncated[:last_nl]
    return truncated + _MARKER


def _charge_ledger(session_id: str, notice_bytes: int) -> None:
    """Append compact_recovery pseudo-record so compute_net picks up the charge (I3)."""
    try:
        with acquire_lock(session_id):
            session = read_session(session_id)
            if not session:
                return
            calls = session.get("tool_calls") or []
            calls.append({
                "seq": len(calls) + 1,
                "tool": "compact_recovery",
                "response_chars": 0,
                "tokens_est": 0,
                "notice_bytes": notice_bytes,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            session["tool_calls"] = calls
            write_session(session_id, session)
    except Exception:
        pass


def _inject_task_state(session_id: str, config: dict) -> None:
    """A4: unlink task.md first (fail toward under-injection), then inject, then charge."""
    task_path = ccgate_home() / "sessions" / f"{session_id}.task.md"
    if not task_path.exists():
        return
    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError:
        return

    max_chars = config.get("taskStateMaxTokens", 2500) * 4
    content = _truncate_at_line(content, max_chars)
    if not content:
        return

    # Unlink first: OSError here → return (no injection, no duplication)
    try:
        task_path.unlink(missing_ok=True)
    except OSError:
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": content,
        }
    }))

    _charge_ledger(session_id, len(content))


def main() -> None:
    if os.environ.get("CCGATE_DISABLE") == "1":
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    source = payload.get("source", "")
    session_id = payload.get("session_id", "unknown")

    if source == "startup":
        try:
            config = load_config()
            findings = _check_pin_mismatch(config)
            if findings:
                _write_startup_findings(session_id, findings)
        except Exception:
            pass
        return

    if source not in ("compact", "resume"):
        return

    # G14: flush read cache
    try:
        rc_path = ccgate_home() / "sessions" / f"{session_id}-read-cache.json"
        if rc_path.exists():
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

    # A4: inject task state
    try:
        config = load_config()
        if config.get("compactRecovery", True):
            _inject_task_state(session_id, config)
    except Exception:
        pass


if __name__ == "__main__":
    main()
