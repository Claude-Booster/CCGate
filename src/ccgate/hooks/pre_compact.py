"""pre_compact.py — PreCompact hook: write task state (A4) then flush read cache (G14).

G22: NO stdout output from this file. additionalContext/systemMessage from PreCompact
are discarded by Claude Code. Re-injection happens at SessionStart only.
Order: A4 reads file_log BEFORE G14 clears it.
"""
from __future__ import annotations

import json
import os
import sys
from collections import deque
from pathlib import Path

from ccgate.state import acquire_lock, ccgate_home


def _parse_todowrite(raw: str) -> list | None:
    """Parse a raw JSONL line; return the todos list if it contains a TodoWrite tool_use.

    Returns a list (possibly []) on a confirmed hit, None on any failure or if no
    tool_use block with name=='TodoWrite' is present. An empty list is a valid hit —
    it means all tasks were cleared.
    """
    try:
        entry = json.loads(raw)
        content = (entry.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            return None
        for block in content:
            if (isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "TodoWrite"):
                return block.get("input", {}).get("todos") or []
        return None
    except Exception:
        return None


def _extract_from_transcript(transcript_path: str | None) -> dict:
    """Scan transcript for edited file paths and latest TodoWrite state.

    Single read pass — two operations interleaved:
    (a) Every stripped line is appended to raw_window (deque[str](maxlen=500)) unparsed.
        json.loads is deferred to the parse pass below — only ≤500 lines get parsed.
    (b) Lines containing '"TodoWrite"' as a substring are passed to _parse_todowrite().
        Only confirmed hits (tool_use block present) overwrite todos, so prose mentions
        and tool_results echoing the name are filtered out. Last confirmed hit wins.

    Parse pass: reverse over raw_window (≤500 entries), extract Edit/Write file paths.

    If transcript_path is absent, returns {} — no glob, no Path.home() reference.
    """
    if not transcript_path:
        return {}
    try:
        path = Path(transcript_path)
        if not path.exists():
            return {}

        raw_window: deque[str] = deque(maxlen=500)  # raw strings; json.loads deferred
        todos: list[dict] = []

        with path.open(encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                raw_window.append(s)
                if '"TodoWrite"' in s:
                    cand = _parse_todowrite(s)
                    if cand is not None:
                        todos = cand  # last confirmed hit wins; [] is valid (tasks cleared)

        # Parse the last ≤500 entries for edited files (newest-first)
        edited_files: list[str] = []
        seen: set[str] = set()
        for raw in reversed(raw_window):
            if len(edited_files) >= 10:
                break
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content") or []
            if not isinstance(content, list):
                continue
            for block in reversed(content):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") in ("Edit", "Write"):
                    inp = block.get("input") or {}
                    fp = inp.get("file_path") or inp.get("path") or ""
                    if fp and fp not in seen:
                        seen.add(fp)
                        edited_files.append(fp)
                        if len(edited_files) >= 10:
                            break

        return {
            "edited_files": list(reversed(edited_files)),  # chronological
            "todos": todos,
        }
    except Exception:
        return {}


def _compose_task_md(session_id: str, config: dict, transcript_path: str | None) -> str:
    """Compose task.md from live session state.

    Content (in order):
    1. compactInstructions framing preamble
    2. Recently edited files (from transcript — highest recovery value)
    3. Other files read this session (from file_log, excluding edited set)
    4. Active tasks (from latest TodoWrite, pending/in_progress only)

    Excludes all ccgate telemetry (model, started_at, tool_profile).
    """
    lines: list[str] = []

    preamble = config.get("compactInstructions", "").strip()
    if preamble:
        lines.append(preamble)
        lines.append("")

    ctx = _extract_from_transcript(transcript_path)

    edited_files = ctx.get("edited_files") or []
    if edited_files:
        lines.append("## Recently edited files")
        for p in edited_files:
            lines.append(f"- {p}")
        lines.append("")

    rc_path = ccgate_home() / "sessions" / f"{session_id}-read-cache.json"
    file_log: list[str] = []
    if rc_path.exists():
        try:
            rc = json.loads(rc_path.read_text(encoding="utf-8"))
            file_log = rc.get("file_log") or []
        except (json.JSONDecodeError, OSError):
            pass
    edited_set = set(edited_files)
    read_only = [f for f in file_log if f not in edited_set]
    if read_only:
        lines.append("## Other files read this session")
        for p in read_only:
            lines.append(f"- {p}")
        lines.append("")

    todos = ctx.get("todos") or []
    active_todos = [t for t in todos if t.get("status") not in ("completed",)]
    if active_todos:
        lines.append("## Active tasks")
        status_marker = {"in_progress": "[~]"}
        for todo in active_todos:
            marker = status_marker.get(todo.get("status", ""), "[ ]")
            lines.append(f"- {marker} {todo.get('content', '')}")
        lines.append("")

    return "\n".join(lines)


def _write_task_state(session_id: str, config: dict, transcript_path: str | None) -> None:
    """Write sessions/<id>.task.md (A4). Must be called BEFORE G14 flush."""
    content = _compose_task_md(session_id, config, transcript_path)
    path = ccgate_home() / "sessions" / f"{session_id}.task.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


def main() -> None:
    if os.environ.get("CCGATE_DISABLE") == "1":
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    transcript_path = payload.get("transcript_path")

    # A4: write task state FIRST — reads file_log before G14 clears it
    try:
        from ccgate.config import load_config
        config = load_config()
        if config.get("compactRecovery", True):
            _write_task_state(session_id, config, transcript_path)
    except Exception:
        pass

    # G14: flush read cache AFTER A4 has captured state
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
                data["flushed_at"] = "pre_compact"
                tmp = rc_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(str(tmp), str(rc_path))
    except Exception:
        pass


if __name__ == "__main__":
    main()
