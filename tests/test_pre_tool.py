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
