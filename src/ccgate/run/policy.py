"""policy.py — Track B enforcement: .contextignore matching + PreToolUse deny callbacks."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from ccgate.run.shellcmd import first_token, strip_runner_prefixes

_COST_REASON = ("{tok} is listed in .contextignore and is excluded because reading it is "
                "expensive and rarely useful; skip it and continue.")


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
    """True if path matches any pattern. Normalizes \\ -> / so Windows paths and forward-
    slash patterns agree. A trailing-slash pattern 'X/' matches iff X is a complete path
    segment (the dir itself and everything beneath, at any depth). Otherwise fnmatch on the
    full path or basename. Pure 'any match -> ignored' -- no negation (spec §3)."""
    norm = path.replace("\\", "/")
    name = norm.rsplit("/", 1)[-1]
    wrapped = "/" + norm + "/"
    for pat in patterns:
        p = pat.replace("\\", "/")
        if p.endswith("/"):
            seg = p[:-1]
            if seg and ("/" + seg + "/") in wrapped:
                return True
            continue
        if fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(name, p):
            return True
    return False


def first_matching_pattern(path: str, patterns: list[str]) -> str | None:
    """The first pattern that makes path ignored, or None. Uses path_is_ignored per pattern
    so it agrees exactly with the deny decision."""
    for pat in patterns:
        if path_is_ignored(path, [pat]):
            return pat
    return None


def make_read_deny_hook(patterns: list[str], recorder=None):
    """Build an async PreToolUse callback that denies Read of a .contextignore'd path.

    Signature matches claude-agent-sdk hooks: (input_data, tool_use_id, context) -> dict.
    Deny dict shape is the SDK's hookSpecificOutput; {} means allow (spec §3). When a recorder
    is given, records an F1 read event on deny (recorder=None keeps B0 call sites unchanged)."""
    async def _hook(input_data: dict, tool_use_id, context) -> dict:
        if input_data.get("tool_name") != "Read":
            return {}
        target = (input_data.get("tool_input") or {}).get("file_path", "")
        if target and path_is_ignored(target, patterns):
            if recorder is not None:
                recorder.append_event({
                    "type": "ccgate_event", "rule": "F1", "surface": "read",
                    "matched_pattern": first_matching_pattern(target, patterns),
                    "matched_token": target,
                })
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _COST_REASON.format(tok=target),
                }
            }
        return {}
    return _hook


class BashReadDeny:
    """Stateful PreToolUse Bash callback: deny common readers of a .contextignore'd path.
    Best-effort efficiency, not a control (spec §1). Owns matcher_errors (spec §7)."""

    def __init__(self, patterns, reader_prefixes, recorder):
        self.patterns = patterns
        self.readers = set(reader_prefixes)   # first-token equality, not startswith
        self.recorder = recorder
        self.denies = 0
        self.matcher_errors = 0

    async def __call__(self, input_data, tool_use_id, context) -> dict:
        try:
            if input_data.get("tool_name") != "Bash":
                return {}
            command = (input_data.get("tool_input") or {}).get("command", "")
            effective = strip_runner_prefixes(command)
            if first_token(effective) not in self.readers:
                return {}
            for tok in effective.split():
                tok = tok.strip("'\"")
                if path_is_ignored(tok, self.patterns):
                    self.denies += 1
                    self.recorder.append_event({
                        "type": "ccgate_event", "rule": "F1", "surface": "bash",
                        "command_prefix": command[:40],
                        "matched_pattern": first_matching_pattern(tok, self.patterns),
                        "matched_token": tok,
                    })
                    return {"hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": _COST_REASON.format(tok=tok),
                    }}
            return {}
        except Exception:
            self.matcher_errors += 1   # increment FIRST, then return (spec §7)
            return {}

    def summary(self) -> dict:
        return {"type": "ccgate_event", "rule": "F1", "surface": "bash", "summary": True,
                "denies": self.denies, "matcher_errors": self.matcher_errors}


def make_bash_read_deny_hook(patterns, reader_prefixes, recorder) -> BashReadDeny:
    return BashReadDeny(patterns, reader_prefixes, recorder)
