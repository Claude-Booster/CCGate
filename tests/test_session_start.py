"""test_session_start.py — A1 pin_mismatch assertion in session_start hook."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def _run_hook(payload: dict, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.session_start"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _base_env(tmp_path: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    env["CCGATE_HOME"] = str(tmp_path)
    # Remove pinning env vars so the hook can detect their absence
    env.pop("ENABLE_PROMPT_CACHING_1H", None)
    env.pop("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", None)
    if extra:
        env.update(extra)
    return env


class TestPinMismatchAssertion:
    def test_startup_no_env_keys_writes_findings(self, tmp_path):
        """source: startup + env vars absent → pin_mismatch findings written."""
        env = _base_env(tmp_path)
        payload = {"session_id": "pm1", "source": "startup"}
        r = _run_hook(payload, env)
        assert r.returncode == 0
        # stdout must be empty (no context injection for A1)
        assert r.stdout.strip() == ""
        # startup file must exist and contain findings
        startup_file = tmp_path / "sessions" / "pm1-startup.json"
        assert startup_file.exists(), "startup findings file not written"
        data = json.loads(startup_file.read_text(encoding="utf-8"))
        assert "findings" in data
        types = [f["type"] for f in data["findings"]]
        assert "pin_mismatch" in types

    def test_startup_both_keys_present_no_findings(self, tmp_path):
        """source: startup + both env vars set → no pin_mismatch findings."""
        env = _base_env(tmp_path, {
            "ENABLE_PROMPT_CACHING_1H": "1",
            "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
        })
        payload = {"session_id": "pm2", "source": "startup"}
        r = _run_hook(payload, env)
        assert r.returncode == 0
        assert r.stdout.strip() == ""
        startup_file = tmp_path / "sessions" / "pm2-startup.json"
        if startup_file.exists():
            data = json.loads(startup_file.read_text(encoding="utf-8"))
            pin_findings = [f for f in data.get("findings", []) if f["type"] == "pin_mismatch"]
            assert len(pin_findings) == 0

    def test_compact_source_does_not_assert_pin(self, tmp_path):
        """source: compact → no pin_mismatch assertion (only read-cache flush)."""
        env = _base_env(tmp_path)
        rc_path = tmp_path / "sessions" / "pm3-read-cache.json"
        rc_path.parent.mkdir(parents=True, exist_ok=True)
        rc_path.write_text(
            json.dumps({"reads": {"file.py": {}}, "file_log": ["file.py"]}),
            encoding="utf-8",
        )
        payload = {"session_id": "pm3", "source": "compact"}
        r = _run_hook(payload, env)
        assert r.returncode == 0
        # Read cache must be flushed
        data = json.loads(rc_path.read_text(encoding="utf-8"))
        assert data["reads"] == {}
        # No startup findings file written for compact source
        startup_file = tmp_path / "sessions" / "pm3-startup.json"
        assert not startup_file.exists()

    def test_ccgate_disable_suppresses_all(self, tmp_path):
        """CCGATE_DISABLE=1 → exit immediately, nothing written."""
        env = _base_env(tmp_path, {"CCGATE_DISABLE": "1"})
        payload = {"session_id": "pm4", "source": "startup"}
        r = _run_hook(payload, env)
        assert r.returncode == 0
        assert r.stdout.strip() == ""
        startup_file = tmp_path / "sessions" / "pm4-startup.json"
        assert not startup_file.exists()

    def test_findings_file_atomic_write(self, tmp_path):
        """Startup findings are written atomically (tmp → replace)."""
        env = _base_env(tmp_path)
        payload = {"session_id": "pm5", "source": "startup"}
        r = _run_hook(payload, env)
        assert r.returncode == 0
        # No .tmp file should remain after successful write
        tmp_files = list((tmp_path / "sessions").glob("pm5*.tmp"))
        assert tmp_files == []
