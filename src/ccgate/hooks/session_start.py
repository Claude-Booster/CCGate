"""session_start.py — SessionStart hook: flush read cache on compact/resume (G14)."""
from __future__ import annotations

import json
import os
import sys

from ccgate.config import load_config
from ccgate.state import acquire_lock, ccgate_home


def _write_startup_findings(session_id: str, findings: list[dict]) -> None:
    """Atomically write startup findings to sessions/<id>-startup.json."""
    path = ccgate_home() / "sessions" / f"{session_id}-startup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"session_id": session_id, "findings": findings}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _check_pin_mismatch(config: dict) -> list[dict]:
    """A1: compare resolved env to intent. Returns list of pin_mismatch findings."""
    if not config.get("pinCacheTtl", True):
        return []
    findings = []
    if not os.environ.get("ENABLE_PROMPT_CACHING_1H"):
        findings.append({
            "type": "pin_mismatch",
            "key": "ENABLE_PROMPT_CACHING_1H",
            "message": "A1: ENABLE_PROMPT_CACHING_1H absent from resolved env — run 'ccgate shape --fix'",
        })
    if not os.environ.get("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"):
        findings.append({
            "type": "pin_mismatch",
            "key": "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
            "message": "A1: CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL absent from resolved env — run 'ccgate shape --fix'",
        })
    return findings


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

    # A1: pin_mismatch assertion on startup
    if source == "startup":
        try:
            config = load_config()
            findings = _check_pin_mismatch(config)
            if findings:
                _write_startup_findings(session_id, findings)
        except Exception:
            pass
        # TODO A3: tool_count_jump assertion (deferred — statusline payload
        # does not yet expose a tool count field)
        return

    # G14: flush read cache before/after compaction
    if source not in ("compact", "resume"):
        return

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
