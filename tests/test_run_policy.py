import asyncio
from pathlib import Path
from ccgate.run.policy import load_contextignore, path_is_ignored, make_read_deny_hook


def _call(hook, tool_name, tool_input):
    return asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input}, "tuid", None))


def test_denies_ignored_read():
    hook = make_read_deny_hook(["secrets/*.txt"])
    out = _call(hook, "Read", {"file_path": "secrets/a.txt"})
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "secrets/a.txt" in hso["permissionDecisionReason"]


def test_allows_non_ignored_read():
    hook = make_read_deny_hook(["secrets/*.txt"])
    assert _call(hook, "Read", {"file_path": "src/main.py"}) == {}


def test_ignores_non_read_tools():
    hook = make_read_deny_hook(["secrets/*.txt"])
    assert _call(hook, "Bash", {"command": "cat secrets/a.txt"}) == {}


def test_absent_contextignore_returns_empty(tmp_path):
    assert load_contextignore(tmp_path) == []


def test_blank_and_comment_lines_ignored(tmp_path):
    (tmp_path / ".contextignore").write_text("# comment\n\nsecrets/*.txt\n", encoding="utf-8")
    assert load_contextignore(tmp_path) == ["secrets/*.txt"]


def test_glob_matches_full_path_and_basename(tmp_path):
    pats = ["secrets/*.txt", "*.key"]
    assert path_is_ignored("secrets/a.txt", pats) is True
    assert path_is_ignored("/repo/private.key", pats) is True     # basename match
    assert path_is_ignored("src/main.py", pats) is False


def test_empty_patterns_never_match():
    assert path_is_ignored("anything.txt", []) is False
