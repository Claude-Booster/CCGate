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

    def test_parse_todowrite_ignores_prose_mentions(self, tmp_path):
        """_parse_todowrite false-positive guard: prose/tool_result lines mentioning
        'TodoWrite' must not overwrite the confirmed todo list."""
        real_todos = [{"id": "1", "content": "real task", "status": "pending"}]
        # First entry: genuine tool_use with a real todo list
        real_entry = _assistant_entry([_tool_use_block("TodoWrite", {"todos": real_todos})])
        # Second entry: an assistant message that mentions "TodoWrite" in text content
        # (simulates the model describing the tool it just used — a common false-positive source)
        prose_entry = {
            "type": "assistant",
            "timestamp": "2026-09-23T10:01:00Z",
            "message": {
                "model": "claude-test",
                "content": [{"type": "text", "text": 'I used "TodoWrite" to update the task list.'}],
                "usage": {"input_tokens": 50, "output_tokens": 5},
            },
        }
        # Third entry: a tool_result echoing the name (another common false-positive source)
        tool_result_entry = {
            "type": "user",
            "timestamp": "2026-09-23T10:01:05Z",
            "message": {
                "content": [{"type": "tool_result", "content": "TodoWrite succeeded."}],
            },
        }
        transcript_entries = [real_entry, prose_entry, tool_result_entry]
        transcript_file = _write_fake_transcript(tmp_path, "c13", transcript_entries)
        _write_read_cache(tmp_path, "c13", [])
        env = _base_env(tmp_path)
        payload = {"session_id": "c13", "transcript_path": str(transcript_file)}
        r = _run_precompact(payload, env)
        assert r.returncode == 0
        content = (tmp_path / "sessions" / "c13.task.md").read_text(encoding="utf-8")
        # The real task must appear — prose/tool_result mentions must not have cleared it
        assert "real task" in content, "prose mention of TodoWrite must not discard confirmed todos"


def _run_session_start(payload: dict, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.session_start"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _write_task_md(tmp_path: Path, session_id: str, content: str) -> Path:
    p = tmp_path / "sessions" / f"{session_id}.task.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class TestSessionStartInjection:
    def test_compact_source_injects_task_md(self, tmp_path):
        """source: compact → stdout JSON with additionalContext."""
        _write_task_md(tmp_path, "s1", "active plan: refactor auth")
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s1", "source": "compact"}, env)
        assert r.returncode == 0
        assert r.stdout.strip()
        data = json.loads(r.stdout)
        hook_out = data["hookSpecificOutput"]
        assert hook_out["hookEventName"] == "SessionStart"
        assert "active plan: refactor auth" in hook_out["additionalContext"]

    def test_resume_source_injects_task_md(self, tmp_path):
        """source: resume → stdout JSON with additionalContext."""
        _write_task_md(tmp_path, "s2", "task: fix failing test")
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s2", "source": "resume"}, env)
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "fix failing test" in data["hookSpecificOutput"]["additionalContext"]

    def test_hook_event_name_literal(self, tmp_path):
        """hookEventName must be exactly 'SessionStart'."""
        _write_task_md(tmp_path, "s3", "step 4 of 6")
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s3", "source": "compact"}, env)
        assert r.returncode == 0
        assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_task_md_deleted_before_injection(self, tmp_path):
        """task.md deleted before print (unlink-first order)."""
        p = _write_task_md(tmp_path, "s4", "some state")
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s4", "source": "compact"}, env)
        assert r.returncode == 0
        assert r.stdout.strip()
        assert not p.exists(), "task.md must be deleted (unlink-first)"

    def test_resume_after_compact_does_not_reinject(self, tmp_path):
        """Second call (resume) after compact deleted task.md → no injection."""
        _write_task_md(tmp_path, "s5", "state")
        env = _base_env(tmp_path)
        r1 = _run_session_start({"session_id": "s5", "source": "compact"}, env)
        assert r1.returncode == 0
        assert r1.stdout.strip()
        r2 = _run_session_start({"session_id": "s5", "source": "resume"}, env)
        assert r2.returncode == 0
        assert r2.stdout.strip() == ""

    def test_startup_source_does_not_inject(self, tmp_path):
        """source: startup → no injection."""
        _write_task_md(tmp_path, "s6", "should not appear")
        env = _base_env(tmp_path, {
            "ENABLE_PROMPT_CACHING_1H": "1",
            "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
        })
        r = _run_session_start({"session_id": "s6", "source": "startup"}, env)
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_clear_source_does_not_inject(self, tmp_path):
        """source: clear → no injection."""
        _write_task_md(tmp_path, "s7", "should not appear")
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s7", "source": "clear"}, env)
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_missing_task_md_no_crash_no_output(self, tmp_path):
        """compact source but no task.md → exit 0, empty stdout."""
        (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s8", "source": "compact"}, env)
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_truncates_within_cap_at_line_boundary(self, tmp_path):
        """Injected length always <= taskStateMaxTokens*4 chars; ends with marker."""
        max_chars = 2500 * 4  # 10,000
        long_content = "\n".join(f"line {i}" for i in range(10000))
        assert len(long_content) > max_chars
        _write_task_md(tmp_path, "s9", long_content)
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s9", "source": "compact"}, env)
        assert r.returncode == 0
        injected = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert len(injected) <= max_chars, f"Exceeded cap: {len(injected)} > {max_chars}"
        assert injected.endswith("[truncated]")

    def test_exact_limit_not_truncated(self, tmp_path):
        """Content == max chars passes through without marker."""
        max_chars = 2500 * 4
        line = "y" * 79 + "\n"
        exact_content = (line * (max_chars // len(line)))[:max_chars]
        _write_task_md(tmp_path, "s10", exact_content)
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s10", "source": "compact"}, env)
        assert r.returncode == 0
        assert not json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"].endswith("[truncated]")

    def test_compact_recovery_disabled_no_injection(self, tmp_path):
        """compactRecovery=false → no injection."""
        _write_task_md(tmp_path, "s11", "should not appear")
        (tmp_path / "config.json").write_text(
            json.dumps({"compactRecovery": False}), encoding="utf-8"
        )
        env = _base_env(tmp_path)
        r = _run_session_start({"session_id": "s11", "source": "compact"}, env)
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_ledger_charged_and_compute_net_picks_it_up(self, tmp_path):
        """I3: compute_net picks up the charge — asserted by delta, not record existence."""
        from ccgate.ledger import compute_net
        content = "active plan: auth refactor\nstep 3 of 6"
        _write_task_md(tmp_path, "s12", content)
        _write_session_data(tmp_path, "s12")
        env = _base_env(tmp_path)

        session_path = tmp_path / "sessions" / "s12.json"
        net_before = compute_net(json.loads(session_path.read_text(encoding="utf-8")))

        r = _run_session_start({"session_id": "s12", "source": "compact"}, env)
        assert r.returncode == 0
        injected_content = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

        net_after = compute_net(json.loads(session_path.read_text(encoding="utf-8")))
        expected_delta = len(injected_content) // 4
        assert net_after["tokens_injected"] == net_before["tokens_injected"] + expected_delta, (
            f"compute_net delta wrong: before={net_before['tokens_injected']}, "
            f"after={net_after['tokens_injected']}, expected delta={expected_delta}"
        )
