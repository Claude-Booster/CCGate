import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

import pytest

WORKTREE = Path(__file__).parent.parent
FIXTURE = WORKTREE / "tests" / "fixtures" / "otlp_export.json"


def _run(*args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src")}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", *args],
        capture_output=True, text=True, cwd=str(WORKTREE), env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_payload(payload: dict) -> dict:
    """Call parse_otlp_payload directly for unit tests."""
    sys.path.insert(0, str(WORKTREE / "src"))
    from ccgate.scripts.otel_reader import parse_otlp_payload
    return parse_otlp_payload(payload)


def test_parse_standard_payload():
    payload = json.loads(FIXTURE.read_text())
    result = _parse_payload(payload)
    assert result["session_id"] == "fixture-session-001"
    assert result["tokens"]["input"] == 12345
    assert result["tokens"]["output"] == 678
    assert result["tokens"]["cache_read"] == 9800
    assert result["tokens"]["cache_creation"] == 500


def test_unknown_type_label():
    payload = json.loads(FIXTURE.read_text())
    result = _parse_payload(payload)
    # "experimental_new_type" → stored under "unknown", not dropped
    assert result["tokens"].get("unknown", 0) == 42


def test_missing_session_id():
    payload = {
        "resourceMetrics": [{
            "resource": {"attributes": []},
            "scopeMetrics": [{
                "metrics": [{
                    "name": "claude_code.token.usage",
                    "sum": {"dataPoints": [
                        {"attributes": [{"key": "type", "value": {"stringValue": "input"}}],
                         "asInt": "100"}
                    ]}
                }]
            }]
        }]
    }
    result = _parse_payload(payload)
    assert result["session_id"] == "unknown"
    assert result["tokens"]["input"] == 100


def test_asDouble_fallback():
    payload = {
        "resourceMetrics": [{
            "resource": {"attributes": [
                {"key": "session.id", "value": {"stringValue": "double-session"}}
            ]},
            "scopeMetrics": [{
                "metrics": [{
                    "name": "claude_code.token.usage",
                    "sum": {"dataPoints": [
                        {"attributes": [{"key": "type", "value": {"stringValue": "input"}}],
                         "asDouble": 5000.0}
                    ]}
                }]
            }]
        }]
    }
    result = _parse_payload(payload)
    assert result["tokens"]["input"] == 5000


def test_file_mode_writes_session(tmp_path):
    rc, out, err = _run(
        "read", "--file", str(FIXTURE),
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err
    otel_file = tmp_path / "sessions" / "fixture-session-001-otel.json"
    assert otel_file.exists(), f"Expected {otel_file} to exist"
    data = json.loads(otel_file.read_text())
    assert data["session_id"] == "fixture-session-001"
    assert data["source"] == "otlp_file"
    assert data["tokens"]["input"] == 12345
    assert data["tokens"]["cache_read"] == 9800


def test_atomic_write(tmp_path):
    # File is written atomically: check no partial file remains on success
    rc, _, err = _run(
        "read", "--file", str(FIXTURE),
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err
    # .tmp file must not remain after successful write
    tmp_files = list((tmp_path / "sessions").glob("*.tmp"))
    assert tmp_files == [], f"Stale .tmp files: {tmp_files}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    """Poll until the port accepts TCP connections or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Server did not bind on port {port} within {timeout}s")


def test_server_post_returns_200(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src"),
           "CCGATE_HOME": str(tmp_path), "CCGATE_OTEL_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", "serve", "--port", str(port)],
        env=env, cwd=str(WORKTREE),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)
        payload = FIXTURE.read_text().encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/metrics",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            assert resp.status == 200
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_server_bad_json_returns_400(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src"),
           "CCGATE_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", "serve", "--port", str(port)],
        env=env, cwd=str(WORKTREE),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/metrics",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=3)
            assert False, "Expected HTTP 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_server_wrong_path_returns_404(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src"),
           "CCGATE_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", "serve", "--port", str(port)],
        env=env, cwd=str(WORKTREE),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/wrong/path",
            method="GET",
        )
        try:
            urllib.request.urlopen(req, timeout=3)
            assert False, "Expected HTTP 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_session_id_path_traversal_sanitized(tmp_path):
    """Malicious session_id must not write outside the sessions directory."""
    from ccgate.scripts.otel_reader import _sanitize_session_id
    assert _sanitize_session_id("../../evil") == "unknown"
    assert _sanitize_session_id("../etc/passwd") == "unknown"
    assert _sanitize_session_id("valid-session-123") == "valid-session-123"
    assert _sanitize_session_id("") == "unknown"
    assert _sanitize_session_id("/etc/shadow") == "unknown"
