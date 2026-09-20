import json
import os
import time
import pytest
from pathlib import Path
from ccgate.state import (
    acquire_lock,
    ccgate_home,
    read_session,
    read_tools,
    session_path,
    tools_path,
    write_session,
    write_tools,
)


class TestCcgateHome:
    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        assert ccgate_home() == tmp_path

    def test_default_is_home_ccgate(self, monkeypatch):
        monkeypatch.delenv("CCGATE_HOME", raising=False)
        result = ccgate_home()
        assert result == Path.home() / ".ccgate"


class TestAtomicWrite:
    def test_write_session_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        write_session("s1", {"session_id": "s1"})
        assert session_path("s1").exists()
        data = json.loads(session_path("s1").read_text())
        assert data["session_id"] == "s1"

    def test_read_session_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        assert read_session("missing") == {}

    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        data = {"session_id": "s2", "notices_emitted": 3}
        write_session("s2", data)
        assert read_session("s2") == data

    def test_write_tools_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        profile = {"Read": {"count": 5, "mean_tokens": 900}}
        write_tools(profile)
        assert read_tools() == profile

    def test_no_tmp_file_left_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        write_session("s3", {"x": 1})
        tmp_files = list((tmp_path / "sessions").glob("*.tmp"))
        assert tmp_files == []

    def test_write_is_atomic_overwrites_previous(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        write_session("s4", {"v": 1})
        write_session("s4", {"v": 2})
        assert read_session("s4") == {"v": 2}


class TestAdvisoryLock:
    def test_acquire_and_release(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_file = tmp_path / "locks" / "s1.lock"
        with acquire_lock("s1"):
            assert lock_file.exists()
        assert not lock_file.exists()

    def test_double_acquire_times_out(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_path = tmp_path / "locks" / "s1.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("9999")  # simulate held lock
        with pytest.raises(TimeoutError):
            with acquire_lock("s1", timeout_ms=50):
                pass

    def test_stale_lock_is_broken(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_path = tmp_path / "locks" / "s1.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("9999")
        old_time = time.time() - 31  # 31 seconds ago → stale
        os.utime(lock_path, (old_time, old_time))
        with acquire_lock("s1", timeout_ms=100):  # must not raise
            pass
        assert not lock_path.exists()

    def test_lock_released_on_exception(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_path = tmp_path / "locks" / "s1.lock"
        try:
            with acquire_lock("s1"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert not lock_path.exists()
