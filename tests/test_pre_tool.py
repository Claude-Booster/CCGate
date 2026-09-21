"""test_pre_tool.py — subprocess integration tests for pre_tool.py hook."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def _run(payload: dict, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.pre_tool"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _base_env(tmp_path: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    env["CCGATE_HOME"] = str(tmp_path)
    if extra:
        env.update(extra)
    return env


# ── contextignore ──────────────────────────────────────────────────────────


def test_contextignore_denies_matching_path(tmp_path):
    """Read on a *.json path denied when .contextignore has *.json and rule is enabled."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"
    assert "contextignore" in out["permissionDecisionReason"]
    assert "*.json" in out["permissionDecisionReason"]


def test_contextignore_allows_non_matching_path(tmp_path):
    """Read on a .py path allowed even when .contextignore has *.json."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/main.py"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_contextignore_disabled_allows_matching_path(tmp_path):
    """Rule off by default — matching path passes through."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path)  # no CCGATE_CONTEXTIGNORE_ENABLED
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_contextignore_applies_to_edit(tmp_path):
    """Rule applies to Edit tool as well."""
    (tmp_path / ".contextignore").write_text("secrets.txt\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Edit",
                "tool_input": {"file_path": "/home/user/secrets.txt"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_does_not_apply_to_grep(tmp_path):
    """Grep is not subject to contextignore (no file_path — pattern only)."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Grep",
                "tool_input": {"pattern": "foo", "path": "/project"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_contextignore_backslash_normalized(tmp_path):
    """Windows backslash in file_path is normalized before matching."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "C:\\project\\config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_crlf_stripped(tmp_path):
    """CRLF line endings in .contextignore are handled correctly."""
    (tmp_path / ".contextignore").write_bytes(b"*.log\r\n")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/var/app.log"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_comment_lines_skipped(tmp_path):
    """Lines starting with # are skipped in .contextignore."""
    (tmp_path / ".contextignore").write_text("# this is a comment\n*.log\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/var/app.log"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_absent_file_passes_through(tmp_path):
    """No .contextignore → allow everything even with rule enabled."""
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_hook_exit_0_on_empty_stdin(tmp_path):
    """Hook exits 0 even when stdin is empty (no payload)."""
    env = _base_env(tmp_path)
    r = subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.pre_tool"],
        input="",
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0


# ── read cache ────────────────────────────────────────────────────────────────


def test_read_cache_first_read_allowed(tmp_path):
    """First Read of a file is always allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload = {"session_id": "rc1", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_identical_repeat_denied(tmp_path):
    """Second Read with same (path, offset, limit) + unchanged mtime → denied."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload = {"session_id": "rc2", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read — populates cache
    r = _run(payload, env)  # second read — should deny
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"
    assert "read cache" in out["permissionDecisionReason"]
    assert "readCacheEnabled" in out["permissionDecisionReason"]


def test_read_cache_changed_mtime_allowed(tmp_path):
    """Re-read after file modification is allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload = {"session_id": "rc3", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read
    # Modify file (and ensure mtime changes — write new content)
    target.write_text("changed", encoding="utf-8")
    # Force mtime change on Windows (resolution may be coarse)
    import time as _time
    _time.sleep(0.01)
    target.write_text("changed2", encoding="utf-8")
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_different_offset_allowed(tmp_path):
    """Different offset on same file is a distinct cache key — allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello\nworld\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload1 = {"session_id": "rc4", "tool_name": "Read",
                 "tool_input": {"file_path": str(target), "offset": 0, "limit": 5}}
    payload2 = {"session_id": "rc4", "tool_name": "Read",
                 "tool_input": {"file_path": str(target), "offset": 5, "limit": 5}}
    _run(payload1, env)
    r = _run(payload2, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_staletime_eviction_allows(tmp_path):
    """After staleTimeMs, the cache entry is evicted and re-read is allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    # Set staleTimeMs to 0 via env var (immediate staleness)
    env = _base_env(tmp_path, {
        "CCGATE_READ_CACHE_ENABLED": "1",
        "CCGATE_STALE_TIME_MS": "0",
    })
    payload = {"session_id": "rc5", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read
    r = _run(payload, env)  # second read — staleTimeMs=0 → already stale → allow
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_stalefiles_eviction_allows(tmp_path):
    """After staleFiles distinct files are read, cache entry is stale — re-read allowed."""
    target = tmp_path / "main.py"
    target.write_text("hello", encoding="utf-8")
    # Set staleFiles to 0 via env var so ANY subsequent read makes entry stale
    env = _base_env(tmp_path, {
        "CCGATE_READ_CACHE_ENABLED": "1",
        "CCGATE_STALE_FILES": "0",
    })
    payload = {"session_id": "rc6", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read of target
    # Read a different file (adds to file_log after target's idx)
    other = tmp_path / "other.py"
    other.write_text("world", encoding="utf-8")
    _run({"session_id": "rc6", "tool_name": "Read",
          "tool_input": {"file_path": str(other)}}, env)
    # Now re-read target — staleFiles=0 → effective=0, distinct_after=1 > 0 → stale → allow
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_disabled_passes_through(tmp_path):
    """readCacheEnabled=false (default) → rule inactive, no deny."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path)  # no CCGATE_READ_CACHE_ENABLED
    payload = {"session_id": "rc7", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_missing_file_allowed(tmp_path):
    """OSError on os.stat (file missing) → treat as changed → allow."""
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    ghost = str(tmp_path / "does_not_exist.py")
    payload = {"session_id": "rc8", "tool_name": "Read",
                "tool_input": {"file_path": ghost}}
    _run(payload, env)  # first read (mtime=None recorded)
    r = _run(payload, env)  # second read — mtime still None → no change?
    # mtime=None on first, None on second → treat as "unchanged" → deny.
    # Wait: spec says "OSError → treat as mtime changed → allow". So each call
    # returns OSError → each call treats as changed → always allow.
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# ── bash rewriting ────────────────────────────────────────────────────────────


def test_bash_rewrite_pytest_gets_tail(tmp_path):
    """'pytest' command gets '2>&1 | tail -100' appended."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b1", "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/ -v"}}
    r = _run(payload, env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert "updatedInput" in out
    assert out["updatedInput"]["command"] == "pytest tests/ -v 2>&1 | tail -100"


def test_bash_rewrite_grep_gets_head(tmp_path):
    """'grep' command gets '| head -100' appended."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b2", "tool_name": "Bash",
                "tool_input": {"command": "grep -r foo ."}}
    r = _run(payload, env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["updatedInput"]["command"] == "grep -r foo . | head -100"


def test_bash_rewrite_no_match_passes_through(tmp_path):
    """Non-matching command passes through unchanged."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b3", "tool_name": "Bash",
                "tool_input": {"command": "ls -la"}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_bash_rewrite_disabled_passes_through(tmp_path):
    """Rule inactive by default — pytest command not rewritten."""
    env = _base_env(tmp_path)  # no CCGATE_BASH_REWRITE_ENABLED
    payload = {"session_id": "b4", "tool_name": "Bash",
                "tool_input": {"command": "pytest"}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_bash_rewrite_user_rule_applied(tmp_path):
    """User rule in bashRewriteRules config is applied after built-ins."""
    config_dir = tmp_path / ".ccgate"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"bashRewriteRules": [{"prefix": "mytest", "filter": "| tail -50"}]}),
        encoding="utf-8",
    )
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b5", "tool_name": "Bash",
                "tool_input": {"command": "mytest run"}}
    # subprocess cwd=tmp_path so project .ccgate/config.json is found
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["updatedInput"]["command"] == "mytest run | tail -50"


def test_bash_rewrite_metacharacter_prefix_rejected(tmp_path):
    """Prefix with '.' metacharacter is skipped; other rules still apply."""
    config_dir = tmp_path / ".ccgate"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"bashRewriteRules": [
            {"prefix": "bad.prefix", "filter": "| head -10"},  # rejected
            {"prefix": "goodcmd", "filter": "| tail -20"},     # applied
        ]}),
        encoding="utf-8",
    )
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b6", "tool_name": "Bash",
                "tool_input": {"command": "goodcmd run"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["updatedInput"]["command"] == "goodcmd run | tail -20"


def test_bash_rewrite_not_applied_to_read(tmp_path):
    """Bash rewrite rule only applies to Bash tool."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b7", "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/file.py", "command": "pytest"}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_bash_rewrite_leading_whitespace_stripped_for_match(tmp_path):
    """Leading whitespace before command is stripped before prefix matching."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b8", "tool_name": "Bash",
                "tool_input": {"command": "  pytest tests/"}}
    r = _run(payload, env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    # Original command preserved; filter appended to original (with leading spaces)
    assert out["updatedInput"]["command"] == "  pytest tests/ 2>&1 | tail -100"
