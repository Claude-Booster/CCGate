"""Tests for statusline._persist_snapshot."""
import json
import pytest
from pathlib import Path
from ccgate.scripts.statusline import _persist_snapshot


class TestPersistSnapshot:
    def test_writes_snapshot_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        payload = {"session_id": "s1", "model": {"id": "claude-sonnet-4-6"}}
        _persist_snapshot(payload)
        snap = tmp_path / "sessions" / "s1-statusline.json"
        assert snap.exists()
        data = json.loads(snap.read_text())
        assert data["session_id"] == "s1"

    def test_no_session_id_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({})
        assert not (tmp_path / "sessions").exists()

    def test_overwrites_previous_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({"session_id": "s1", "x": 1})
        _persist_snapshot({"session_id": "s1", "x": 2})
        snap = tmp_path / "sessions" / "s1-statusline.json"
        assert json.loads(snap.read_text())["x"] == 2

    def test_no_tmp_file_left_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({"session_id": "s1"})
        tmps = list((tmp_path / "sessions").glob("*.tmp"))
        assert tmps == []

    def test_session_id_used_as_filename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({"session_id": "mySession42"})
        assert (tmp_path / "sessions" / "mySession42-statusline.json").exists()
