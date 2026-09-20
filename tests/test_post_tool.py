import json
import os
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def run_hook(payload: dict, env: dict) -> "subprocess.CompletedProcess":
    import subprocess
    e = env.copy()
    e.setdefault("PYTHONPATH", SRC)
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.post_tool"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=e,
    )


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    e = os.environ.copy()
    return e, tmp_path


class TestPostTool:
    def test_records_tool_call(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x" * 100}
        result = run_hook(payload, env)
        assert result.returncode == 0
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert len(session["tool_calls"]) == 1
        tc = session["tool_calls"][0]
        assert tc["tool"] == "Read"
        assert tc["response_chars"] == 100
        assert tc["tokens_est"] == 25  # 100 // 4

    def test_increments_seq(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x"}
        run_hook(payload, env)
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert session["tool_calls"][0]["seq"] == 1
        assert session["tool_calls"][1]["seq"] == 2

    def test_updates_tool_profile(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x" * 100}
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert "Read" in session["tool_profile"]
        p = session["tool_profile"]["Read"]
        assert p["count"] == 1
        assert p["total_tokens"] == 25
        assert p["mean_tokens"] == 25
        assert p["max_tokens"] == 25

    def test_no_notice_for_small_response(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x" * 100}
        result = run_hook(payload, env)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_emits_notice_for_large_response(self, hook_env):
        env, tmp_path = hook_env
        # tokens_est = 40_004 // 4 = 10_001 > 10_000 default threshold
        big = "x" * 40_004
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": big}
        result = run_hook(payload, env)
        assert result.returncode == 0
        out = json.loads(result.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "Read" in ctx
        assert "~10,001" in ctx
        assert "offset/limit" in ctx

    def test_notice_increments_notices_emitted(self, hook_env):
        env, tmp_path = hook_env
        big = "x" * 40_004
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": big}
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert session["notices_emitted"] == 1
        assert session["tool_calls"][0]["notice_bytes"] > 0

    def test_notice_capped_at_max_per_session(self, hook_env):
        env, tmp_path = hook_env
        big = "x" * 40_004
        # Emit 4 notices (default maxNoticesPerSession=4)
        for _ in range(4):
            run_hook({"session_id": "s1", "tool_name": "Read", "tool_response": big}, env)
        # 5th call must emit no notice
        result = run_hook(
            {"session_id": "s1", "tool_name": "Read", "tool_response": big}, env
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_reads_statusline_snapshot(self, hook_env):
        env, tmp_path = hook_env
        snap_dir = tmp_path / "sessions"
        snap_dir.mkdir(parents=True)
        snap = snap_dir / "s1-statusline.json"
        snap.write_text(json.dumps({"session_id": "s1", "hit_ratio": 0.9}))
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x"}
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert session["statusline_snapshot"] is not None
        assert session["statusline_snapshot"]["hit_ratio"] == 0.9

    def test_exit_zero_on_empty_stdin(self, hook_env):
        env, tmp_path = hook_env
        import subprocess
        e = env.copy()
        e.setdefault("PYTHONPATH", SRC)
        result = subprocess.run(
            [PYTHON, "-m", "ccgate.hooks.post_tool"],
            input="",
            capture_output=True,
            text=True,
            env=e,
        )
        assert result.returncode == 0
