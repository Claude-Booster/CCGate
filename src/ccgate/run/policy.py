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
