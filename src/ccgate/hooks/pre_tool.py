"""pre_tool.py — PreToolUse hook: contextignore, read-cache, bash rewriting (Phase 2)."""
from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ccgate.config import load_config
from ccgate.state import acquire_lock, ccgate_home


# ── read-cache helpers (used by Task 3) ───────────────────────────────────────

def _rc_path(session_id: str) -> Path:
    return ccgate_home() / "sessions" / f"{session_id}-read-cache.json"


def _read_rc(session_id: str) -> dict:
    path = _rc_path(session_id)
    if not path.exists():
        return {"reads": {}, "file_log": [], "seq": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"reads": {}, "file_log": [], "seq": 0}


def _write_rc(session_id: str, data: dict) -> None:
    path = _rc_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


# ── rule 1: contextignore ─────────────────────────────────────────────────────

def _load_contextignore_patterns() -> list[str]:
    patterns: list[str] = []
    for candidate in [
        Path.cwd() / ".contextignore",
        Path.home() / ".claude" / ".contextignore",
    ]:
        if not candidate.exists():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.replace("\r", "").strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        except OSError:
            pass
    return patterns


def _check_contextignore(
    tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    if not config.get("contextignoreEnabled", False):
        return None
    if tool_name not in ("Read", "Edit", "Write"):
        return None
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    name = Path(file_path).name
    for pattern in _load_contextignore_patterns():
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"blocked by .contextignore pattern {pattern!r}"
                    " — use Grep for targeted search."
                    " To override: remove the pattern or set"
                    " contextignoreEnabled: false in .claude/settings.json."
                ),
            }
    return None


# ── rule 2: read-cache (implemented in Task 3) ───────────────────────────────

def _window_size(session_id: str) -> int:
    snap = ccgate_home() / "sessions" / f"{session_id}-statusline.json"
    if not snap.exists():
        return 200_000
    try:
        data = json.loads(snap.read_text(encoding="utf-8"))
        return int(data.get("window_tokens", 200_000)) or 200_000
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 200_000


def _is_stale(entry: dict, file_log: list, config: dict, session_id: str) -> bool:
    try:
        ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
        elapsed_ms = (datetime.now(timezone.utc) - ts).total_seconds() * 1000
    except (KeyError, ValueError):
        return True  # unparseable ts → treat as stale
    if elapsed_ms > config.get("staleTimeMs", 600_000):
        return True
    stale_files = config.get("staleFiles", 8)
    actual_window = _window_size(session_id)
    effective = round(stale_files * actual_window / 200_000)
    idx = entry.get("idx", 0)
    distinct_after = len(set(file_log[idx + 1:]))
    return distinct_after > effective


def _check_read_cache(
    session_id: str, tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    if not config.get("readCacheEnabled", False):
        return None
    if tool_name != "Read":
        return None
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    key = f"{file_path}:{offset}:{limit}"

    try:
        current_mtime: float | None = os.stat(file_path).st_mtime
    except OSError:
        current_mtime = None

    with acquire_lock(f"{session_id}-read-cache"):
        rc = _read_rc(session_id)
        reads: dict = rc.setdefault("reads", {})
        file_log: list = rc.setdefault("file_log", [])

        if key not in reads:
            idx = len(file_log)
            reads[key] = {
                "mtime": current_mtime,
                "ts": datetime.now(timezone.utc).isoformat(),
                "idx": idx,
            }
            file_log.append(file_path)
            rc["seq"] = rc.get("seq", 0) + 1
            _write_rc(session_id, rc)
            return None

        entry = reads[key]
        stored_mtime = entry.get("mtime")
        if current_mtime is None or stored_mtime is None or current_mtime != stored_mtime:
            idx = len(file_log)
            reads[key] = {
                "mtime": current_mtime,
                "ts": datetime.now(timezone.utc).isoformat(),
                "idx": idx,
            }
            file_log.append(file_path)
            rc["seq"] = rc.get("seq", 0) + 1
            _write_rc(session_id, rc)
            return None

        if _is_stale(entry, file_log, config, session_id):
            idx = len(file_log)
            reads[key] = {
                "mtime": current_mtime,
                "ts": datetime.now(timezone.utc).isoformat(),
                "idx": idx,
            }
            file_log.append(file_path)
            rc["seq"] = rc.get("seq", 0) + 1
            _write_rc(session_id, rc)
            return None

        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"read cache: {file_path!r} (lines {offset}–{limit}, unchanged)"
                " was already read this session — use the earlier result."
                " To override: set readCacheEnabled: false."
            ),
        }


# ── rule 3: bash rewriting (stub — implemented in Task 4) ─────────────────────

def _apply_bash_rewrite(
    tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    return None  # implemented in Task 4


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    result: Optional[dict] = None
    try:
        config = load_config()
        result = _check_contextignore(tool_name, tool_input, config)
        if result is None:
            result = _check_read_cache(session_id, tool_name, tool_input, config)
        if result is None:
            result = _apply_bash_rewrite(tool_name, tool_input, config)
    except Exception:
        pass

    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
