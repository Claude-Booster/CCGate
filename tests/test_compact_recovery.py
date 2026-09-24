# tests/test_compact_recovery.py
"""test_compact_recovery.py — A4 compact recovery."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def _run_precompact(payload: dict, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.pre_compact"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _base_env(tmp_path: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    env["CCGATE_HOME"] = str(tmp_path)
    if extra:
        env.update(extra)
    return env


def _write_read_cache(tmp_path: Path, session_id: str, file_log: list[str]) -> None:
    path = tmp_path / "sessions" / f"{session_id}-read-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"reads": {}, "file_log": file_log}),
        encoding="utf-8",
    )


def _write_session_data(tmp_path: Path, session_id: str, model: str = "claude-test") -> None:
    path = tmp_path / "sessions" / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "started_at": "2026-09-23T10:00:00+00:00",
        "model": model,
        "tool_calls": [],
        "tool_profile": {
            "Read": {"count": 5, "mean_tokens": 1000, "max_tokens": 4000, "total_tokens": 5000}
        },
        "ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0, "net_usd": 0.0},
        "notices_emitted": 0,
        "statusline_snapshot": None,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_fake_transcript(tmp_path: Path, session_id: str, entries: list[dict]) -> Path:
    """Write fake transcript in tmp_path. Tests pass the returned path in payload."""
    p = tmp_path / "sessions" / f"{session_id}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return p


def _tool_use_block(name: str, input_data: dict) -> dict:
    return {"type": "tool_use", "id": "tu1", "name": name, "input": input_data}


def _assistant_entry(blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-09-23T10:00:00Z",
        "message": {
            "model": "claude-test",
            "content": blocks,
            "usage": {"input_tokens": 100, "output_tokens": 10},
        },
    }


class TestPreCompactTaskState:
    def test_writes_task_md(self, tmp_path):
        """PreCompact writes sessions/<id>.task.md."""
        _write_read_cache(tmp_path, "c1", ["src/foo.py"])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c1"}, env)
        assert r.returncode == 0
        assert (tmp_path / "sessions" / "c1.task.md").exists()

    def test_g17_file_log_appears_in_task_md(self, tmp_path):
        """G17: file_log entries captured before G14 flush appear in task.md."""
        _write_read_cache(tmp_path, "c2", ["src/auth.py", "src/config.py"])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c2"}, env)
        assert r.returncode == 0
        content = (tmp_path / "sessions" / "c2.task.md").read_text(encoding="utf-8")
        assert "src/auth.py" in content
        assert "src/config.py" in content

    def test_g14_runs_after_a4_and_clears_file_log(self, tmp_path):
        """G14 must run after A4: read-cache cleared AND task.md has pre-flush state."""
        _write_read_cache(tmp_path, "c3", ["src/shape.py"])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c3"}, env)
        assert r.returncode == 0
        rc = json.loads((tmp_path / "sessions" / "c3-read-cache.json").read_text(encoding="utf-8"))
        assert rc["reads"] == {}
        assert rc["file_log"] == []
        content = (tmp_path / "sessions" / "c3.task.md").read_text(encoding="utf-8")
        assert "src/shape.py" in content

    def test_g22_stdout_is_empty(self, tmp_path):
        """G22: PreCompact stdout must be exactly empty."""
        _write_read_cache(tmp_path, "c4", [])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c4"}, env)
        assert r.returncode == 0
        assert r.stdout.strip() == "", f"G22 violated: {r.stdout!r}"

    def test_g22_no_output_with_real_state(self, tmp_path):
        """G22: non-empty state must still produce no stdout."""
        _write_read_cache(tmp_path, "c4b", ["src/x.py"])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c4b"}, env)
        assert r.returncode == 0
        assert r.stdout.strip() == "", f"G22 violated with state: {r.stdout!r}"

    def test_compact_recovery_disabled_skips_write(self, tmp_path):
        """compactRecovery=false → task.md not written."""
        (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config.json").write_text(
            json.dumps({"compactRecovery": False}), encoding="utf-8"
        )
        _write_read_cache(tmp_path, "c5", ["src/foo.py"])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c5"}, env)
        assert r.returncode == 0
        assert not (tmp_path / "sessions" / "c5.task.md").exists()

    def test_custom_instructions_appear_as_preamble(self, tmp_path):
        """Custom compactInstructions appears in task.md."""
        custom = "Context recovered after compaction."
        (tmp_path / "config.json").write_text(
            json.dumps({"compactInstructions": custom}), encoding="utf-8"
        )
        _write_read_cache(tmp_path, "c6", [])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c6"}, env)
        assert r.returncode == 0
        assert custom in (tmp_path / "sessions" / "c6.task.md").read_text(encoding="utf-8")

    def test_no_tmp_file_remains_after_write(self, tmp_path):
        """Atomic write leaves no .tmp artifact."""
        _write_read_cache(tmp_path, "c7", [])
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c7"}, env)
        assert r.returncode == 0
        assert list((tmp_path / "sessions").glob("c7*.tmp")) == []

    def test_edited_files_from_transcript_appear_in_task_md(self, tmp_path):
        """Edit/Write tool_use blocks in transcript appear under 'Recently edited files'.

        Passes transcript_path in payload so no glob of real home directory fires.
        """
        transcript_entries = [
            _assistant_entry([_tool_use_block("Edit", {"file_path": "src/hooks/pre_compact.py"})]),
            _assistant_entry([_tool_use_block("Write", {"file_path": "tests/test_pin.py"})]),
        ]
        transcript_file = _write_fake_transcript(tmp_path, "c8", transcript_entries)
        _write_read_cache(tmp_path, "c8", [])
        env = _base_env(tmp_path)
        payload = {"session_id": "c8", "transcript_path": str(transcript_file)}
        r = _run_precompact(payload, env)
        assert r.returncode == 0
        content = (tmp_path / "sessions" / "c8.task.md").read_text(encoding="utf-8")
        assert "src/hooks/pre_compact.py" in content
        assert "tests/test_pin.py" in content
        assert "Recently edited" in content

    def test_todos_from_transcript_appear_in_task_md(self, tmp_path):
        """TodoWrite entries appear under 'Active tasks' (pending/in_progress only)."""
        todos = [
            {"id": "1", "content": "fix truncation bug", "status": "in_progress", "priority": "high"},
            {"id": "2", "content": "add ledger test", "status": "pending", "priority": "medium"},
            {"id": "3", "content": "done task", "status": "completed", "priority": "low"},
        ]
        transcript_entries = [
            _assistant_entry([_tool_use_block("TodoWrite", {"todos": todos})]),
        ]
        transcript_file = _write_fake_transcript(tmp_path, "c9", transcript_entries)
        _write_read_cache(tmp_path, "c9", [])
        env = _base_env(tmp_path)
        payload = {"session_id": "c9", "transcript_path": str(transcript_file)}
        r = _run_precompact(payload, env)
        assert r.returncode == 0
        content = (tmp_path / "sessions" / "c9.task.md").read_text(encoding="utf-8")
        assert "fix truncation bug" in content
        assert "add ledger test" in content
        assert "done task" not in content, "completed todos must be filtered out"
        assert "Active tasks" in content

    def test_empty_todowrite_does_not_show_stale_todos(self, tmp_path):
        """An empty TodoWrite list is honoured; stale older list must not be shown."""
        old_todos = [{"id": "1", "content": "old task", "status": "pending"}]
        new_todos: list = []  # everything completed → list cleared
        transcript_entries = [
            # older entry (scanned later in reverse)
            _assistant_entry([_tool_use_block("TodoWrite", {"todos": old_todos})]),
            # newer entry (scanned first in reverse)
            _assistant_entry([_tool_use_block("TodoWrite", {"todos": new_todos})]),
        ]
        transcript_file = _write_fake_transcript(tmp_path, "c10", transcript_entries)
        _write_read_cache(tmp_path, "c10", [])
        env = _base_env(tmp_path)
        payload = {"session_id": "c10", "transcript_path": str(transcript_file)}
        r = _run_precompact(payload, env)
        assert r.returncode == 0
        content = (tmp_path / "sessions" / "c10.task.md").read_text(encoding="utf-8")
        assert "old task" not in content, "stale list must not be resurrected"

    def test_no_telemetry_in_task_md(self, tmp_path):
        """task.md must not contain model name, started_at, or tool token counts."""
        _write_read_cache(tmp_path, "c11", [])
        _write_session_data(tmp_path, "c11", model="claude-opus-4-5")
        env = _base_env(tmp_path)
        r = _run_precompact({"session_id": "c11"}, env)
        assert r.returncode == 0
        content = (tmp_path / "sessions" / "c11.task.md").read_text(encoding="utf-8")
        assert "claude-opus-4-5" not in content
        assert "tok/call" not in content
        assert "started_at" not in content

    def test_edited_files_separated_from_read_files(self, tmp_path):
        """Edited files appear under 'Recently edited'; read-only files not duplicated there."""
        transcript_entries = [
            _assistant_entry([_tool_use_block("Edit", {"file_path": "src/auth.py"})]),
        ]
        transcript_file = _write_fake_transcript(tmp_path, "c12", transcript_entries)
        # file_log has both the edited file and a read-only file
        _write_read_cache(tmp_path, "c12", ["src/auth.py", "src/config.py"])
        env = _base_env(tmp_path)
        payload = {"session_id": "c12", "transcript_path": str(transcript_file)}
        r = _run_precompact(payload, env)
        assert r.returncode == 0
        content = (tmp_path / "sessions" / "c12.task.md").read_text(encoding="utf-8")
        # src/auth.py must appear under edited, not duplicated under read-only
        assert content.count("src/auth.py") == 1
        assert "src/config.py" in content
