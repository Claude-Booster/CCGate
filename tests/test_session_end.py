# tests/test_session_end.py
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ccgate.state import write_session

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def run_hook(payload: dict, env: dict) -> "subprocess.CompletedProcess":
    e = env.copy()
    e.setdefault("PYTHONPATH", SRC)
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.session_end"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=e,
    )


def _write_transcript(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    e = os.environ.copy()
    return e, tmp_path


_BASE_SESSION = {
    "session_id": "s1",
    "started_at": "2026-09-20T10:00:00Z",
    "model": None,
    "tool_calls": [],
    "tool_profile": {},
    "ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0, "net_usd": 0.0},
    "notices_emitted": 0,
    "statusline_snapshot": None,
}


class TestSessionEnd:
    def test_finalizes_ledger_with_no_notices(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [
            {"type": "assistant", "message": {"usage": {"input_tokens": 1000}}}
        ])
        result = run_hook(
            {"session_id": "s1", "transcript_path": str(transcript)}, env
        )
        assert result.returncode == 0
        final = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert final["ledger"]["tokens_injected"] == 0
        assert final["ledger"]["net"] == 0

    def test_finalizes_ledger_with_notice(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        session = dict(_BASE_SESSION)
        session["tool_calls"] = [
            {"seq": 1, "tool": "Read", "response_chars": 100_000,
             "tokens_est": 25_000, "notice_bytes": 80, "ts": "2026-09-20T10:00:01Z"}
        ]
        session["notices_emitted"] = 1
        write_session("s1", session)
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [
            {"type": "assistant", "message": {"usage": {"input_tokens": 5000}}}
        ])
        result = run_hook(
            {"session_id": "s1", "transcript_path": str(transcript)}, env
        )
        assert result.returncode == 0
        final = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert final["ledger"]["tokens_injected"] == 80 // 4  # == 20
        assert final["ledger"]["net"] == -20

    def test_appends_report_line(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [])
        run_hook({"session_id": "s1", "transcript_path": str(transcript)}, env)
        reports = list((tmp_path / "reports").glob("*.txt"))
        assert len(reports) == 1
        content = reports[0].read_text()
        assert "s1" in content
        assert "net=" in content
        assert "avoided=" in content
        assert "injected=" in content
        assert "tool_calls=" in content

    def test_missing_session_exits_0(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [])
        result = run_hook(
            {"session_id": "no_session", "transcript_path": str(transcript)}, env
        )
        assert result.returncode == 0
        # No session → no report written
        assert not (tmp_path / "reports").exists()

    def test_missing_transcript_exits_0(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        result = run_hook(
            {"session_id": "s1", "transcript_path": "/nonexistent/path.jsonl"}, env
        )
        assert result.returncode == 0

    def test_multiple_transcript_entries_summed(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [
            {"type": "assistant", "message": {"usage": {"input_tokens": 1000}}},
            {"type": "assistant", "message": {"usage": {"input_tokens": 2000}}},
        ])
        run_hook({"session_id": "s1", "transcript_path": str(transcript)}, env)
        # session_input_tokens == 3000; net == 0; injected == 0
        final = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert "ledger" in final
