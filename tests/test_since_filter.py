"""test_since_filter.py — find_transcripts since_dt filtering + --since duration parser."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ccgate.transcript import _first_transcript_timestamp, find_transcripts


def _write_transcript(path: Path, timestamp: str) -> None:
    """Write a minimal JSONL transcript with one assistant entry at the given ISO timestamp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {"model": "claude-test", "usage": {"input_tokens": 100}},
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


class TestFirstTranscriptTimestamp:
    def test_returns_timestamp_from_first_assistant_entry(self, tmp_path):
        f = tmp_path / "sess.jsonl"
        _write_transcript(f, "2026-09-23T10:00:00Z")
        assert _first_transcript_timestamp(f) == "2026-09-23T10:00:00Z"

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        assert _first_transcript_timestamp(f) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        assert _first_transcript_timestamp(tmp_path / "missing.jsonl") is None

    def test_skips_non_assistant_entries(self, tmp_path):
        f = tmp_path / "sess.jsonl"
        lines = [
            json.dumps({"type": "user", "timestamp": "2026-09-23T09:00:00Z", "message": {}}),
            json.dumps({"type": "assistant", "timestamp": "2026-09-23T10:00:00Z", "message": {"model": "m", "usage": {}}}),
        ]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert _first_transcript_timestamp(f) == "2026-09-23T10:00:00Z"


class TestFindTranscriptsSinceFilter:
    def _setup_sessions(self, base: Path) -> tuple[Path, Path, Path]:
        """Create three sessions: old (3 days ago), recent (6 hours ago), no-timestamp."""
        now = datetime.now(tz=UTC)
        old_ts = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_ts = (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")

        projects = base / ".claude" / "projects" / "test-project"
        projects.mkdir(parents=True)

        old = projects / "old-session.jsonl"
        recent = projects / "recent-session.jsonl"
        no_ts = projects / "no-timestamp-session.jsonl"

        _write_transcript(old, old_ts)
        _write_transcript(recent, recent_ts)
        # No-timestamp: only a user entry (no assistant entry)
        no_ts.write_text(
            json.dumps({"type": "user", "message": {}}) + "\n", encoding="utf-8"
        )
        return old, recent, no_ts

    def test_no_filter_returns_all(self, tmp_path):
        old, recent, no_ts = self._setup_sessions(tmp_path)
        with patch("ccgate.transcript.Path.home", return_value=tmp_path):
            paths = find_transcripts()
        assert set(paths) == {old, recent, no_ts}

    def test_since_1d_excludes_old_session(self, tmp_path):
        old, recent, no_ts = self._setup_sessions(tmp_path)
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)
        with patch("ccgate.transcript.Path.home", return_value=tmp_path):
            paths = find_transcripts(since_dt=cutoff)
        assert old not in paths
        assert recent in paths

    def test_since_1d_includes_no_timestamp_fail_open(self, tmp_path):
        """Files with no parseable timestamp are included (fail-open)."""
        old, recent, no_ts = self._setup_sessions(tmp_path)
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)
        with patch("ccgate.transcript.Path.home", return_value=tmp_path):
            paths = find_transcripts(since_dt=cutoff)
        assert no_ts in paths

    def test_since_future_excludes_timestamped_sessions(self, tmp_path):
        """A cutoff in the future excludes all sessions with a parseable timestamp.

        No-timestamp files are still included (fail-open), so result is not empty.
        """
        old, recent, no_ts = self._setup_sessions(tmp_path)
        cutoff = datetime.now(tz=UTC) + timedelta(hours=1)
        with patch("ccgate.transcript.Path.home", return_value=tmp_path):
            paths = find_transcripts(since_dt=cutoff)
        assert old not in paths
        assert recent not in paths
        assert no_ts in paths  # fail-open: unreadable timestamp → always included

    def test_since_does_not_use_mtime(self, tmp_path):
        """Verifies filtering is based on transcript content, not file mtime.

        Sets mtime of the old session to 'now' (simulating OneDrive sync),
        which must NOT cause it to appear in the --since 1d results.
        """
        import os
        import time
        old, recent, no_ts = self._setup_sessions(tmp_path)
        # Touch old session's mtime to now — sync tool behaviour
        now_ts = time.time()
        os.utime(old, (now_ts, now_ts))

        cutoff = datetime.now(tz=UTC) - timedelta(days=1)
        with patch("ccgate.transcript.Path.home", return_value=tmp_path):
            paths = find_transcripts(since_dt=cutoff)
        assert old not in paths  # content timestamp still 3 days ago


class TestSinceDurationParser:
    def _run_audit(self, tmp_path: Path, since_arg: str) -> subprocess.CompletedProcess:
        env_patch = {"CCGATE_HOME": str(tmp_path)}
        projects = tmp_path / ".claude" / "projects"
        projects.mkdir(parents=True)
        return subprocess.run(
            [sys.executable, "-m", "ccgate.dispatch", "audit", "--since", since_arg],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env={**__import__("os").environ, **env_patch},
            cwd=str(Path(__file__).parent.parent),
        )

    def test_valid_duration_1d(self, tmp_path):
        result = self._run_audit(tmp_path, "1d")
        # No transcripts → exits 0 with "no transcripts found" message
        assert result.returncode == 0

    def test_valid_duration_6h(self, tmp_path):
        result = self._run_audit(tmp_path, "6h")
        assert result.returncode == 0

    def test_valid_duration_30m(self, tmp_path):
        result = self._run_audit(tmp_path, "30m")
        assert result.returncode == 0

    def test_invalid_duration_exits_1(self, tmp_path):
        result = self._run_audit(tmp_path, "yesterday")
        assert result.returncode == 1
        assert "unrecognised" in result.stderr
