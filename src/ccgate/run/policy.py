"""policy.py — Track B B0 enforcement: .contextignore matching + PreToolUse deny callback."""
from __future__ import annotations

import fnmatch
from pathlib import Path


def load_contextignore(root: Path) -> list[str]:
    """Return non-blank, non-comment patterns from <root>/.contextignore, or [] if absent."""
    f = root / ".contextignore"
    if not f.exists():
        return []
    patterns: list[str] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def path_is_ignored(path: str, patterns: list[str]) -> bool:
    """True if path matches any pattern (glob on the full path or its basename). B0 semantics;
    full gitignore semantics are B1."""
    name = Path(path).name
    for pat in patterns:
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def make_read_deny_hook(patterns: list[str]):
    """Build an async PreToolUse callback that denies Read of a .contextignore'd path.

    Signature matches claude-agent-sdk hooks: (input_data, tool_use_id, context) -> dict.
    Deny dict shape is the SDK's hookSpecificOutput; {} means allow (spec §3).
    """
    async def _hook(input_data: dict, tool_use_id, context) -> dict:
        if input_data.get("tool_name") != "Read":
            return {}
        target = (input_data.get("tool_input") or {}).get("file_path", "")
        if target and path_is_ignored(target, patterns):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{target} is listed in .contextignore",
                }
            }
        return {}
    return _hook
