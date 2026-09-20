"""state.py — atomic JSON I/O and per-session advisory file locking."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def ccgate_home() -> Path:
    env = os.environ.get("CCGATE_HOME")
    return Path(env) if env else Path.home() / ".ccgate"


def session_path(session_id: str) -> Path:
    return ccgate_home() / "sessions" / f"{session_id}.json"


def tools_path() -> Path:
    return ccgate_home() / "tools.json"


def _lock_path(session_id: str) -> Path:
    return ccgate_home() / "locks" / f"{session_id}.lock"


def read_session(session_id: str) -> dict:
    path = session_path(session_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_session(session_id: str, data: dict) -> None:
    path = session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_tools() -> dict:
    path = tools_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_tools(data: dict) -> None:
    path = tools_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def acquire_lock(session_id: str, timeout_ms: int = 200):
    """Advisory lock via O_CREAT|O_EXCL. Breaks stale locks (>30s). Raises TimeoutError."""
    lock_path = _lock_path(session_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_ms / 1000
    delay = 0.005
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 30:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"Could not acquire lock for session {session_id!r}")
            time.sleep(delay)
            delay = min(delay * 2, 0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
