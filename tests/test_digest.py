import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).parent.parent
SCHEMA_PATH = WORKTREE / "schema" / "ccgate.digest.schema.json"


def _run(*args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src")}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "ccgate.scripts.digest", *args],
        capture_output=True, text=True, cwd=str(WORKTREE), env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _make_rc(tmp_path, session_id, file_log, reads=None):
    """Write a minimal read-cache JSON file."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    rc = {"reads": reads or {}, "file_log": file_log, "seq": len(file_log)}
    (sessions_dir / f"{session_id}-read-cache.json").write_text(
        json.dumps(rc), encoding="utf-8"
    )


def test_export_produces_relative_paths(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_path = str(tmp_path / "project" / "src" / "main.py")
    _make_rc(tmp_path, "sess1", [abs_path, abs_path])

    out_file = tmp_path / "digest.json"
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err
    data = json.loads(out_file.read_text())
    paths = [e["path"] for e in data["path_stats"]]
    assert all(not os.path.isabs(p) for p in paths)


def test_export_rejects_absolute_paths(tmp_path, monkeypatch):
    # Absolute path that cannot be made relative — different Windows drive
    # Simulate by using a path that produces an absolute relpath
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    # Use a path outside cwd that would be relative but starts with ..
    outside_path = str(tmp_path / "other" / "secret.py")
    inside_path = str(tmp_path / "project" / "ok.py")
    _make_rc(tmp_path, "sess1", [outside_path, inside_path])

    out_file = tmp_path / "digest.json"
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    # The outside path has ".." in relpath and is skipped; inside path is exported
    exported_paths = [e["path"] for e in data["path_stats"]]
    assert not any(".." in p for p in exported_paths)


def test_export_rejects_dotdot(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_outside = str(tmp_path / "secret.py")  # one level above cwd
    _make_rc(tmp_path, "sess1", [abs_outside])

    out_file = tmp_path / "digest.json"
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    # Path with ".." must not appear in output
    assert all(".." not in e["path"] for e in data["path_stats"])


def test_export_caps_at_digest_max_paths(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    # Create 10 distinct paths; set cap to 5
    file_log = [str(tmp_path / "project" / f"f{i}.py") for i in range(10)]
    _make_rc(tmp_path, "sess1", file_log)

    out_file = tmp_path / "digest.json"
    env_extra = {"CCGATE_HOME": str(tmp_path), "CCGATE_DIGEST_MAX_PATHS": "5"}
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd, env_extra=env_extra,
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    assert len(data["path_stats"]) <= 5


def test_export_schema_valid(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_path = str(tmp_path / "project" / "main.py")
    _make_rc(tmp_path, "sess1", [abs_path, abs_path])

    out_file = tmp_path / "digest.json"
    rc, _, _ = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    # Manual validation of required fields
    for field in schema["required"]:
        assert field in data, f"Missing required field: {field}"
    for entry in data["path_stats"]:
        for field in ("path", "read_count", "cache_block_count"):
            assert field in entry
        assert isinstance(entry["read_count"], int)
        assert isinstance(entry["cache_block_count"], int)
        assert not os.path.isabs(entry["path"])


def test_import_merges_counts(tmp_path):
    # Pre-populate patterns.json with one path
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()
    existing = {"src/a.py": {"read_count": 3, "cache_block_count": 0}}
    (ccgate_home / "patterns.json").write_text(json.dumps(existing))

    # Create a digest that adds to a.py and introduces b.py
    digest = {
        "digest_id": "test-123",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [
            {"path": "src/a.py", "read_count": 2, "cache_block_count": 0},
            {"path": "src/b.py", "read_count": 5, "cache_block_count": 0},
        ],
    }
    digest_file = tmp_path / "digest.json"
    digest_file.write_text(json.dumps(digest))

    rc, out, err = _run(
        "import", str(digest_file),
        env_extra={"CCGATE_HOME": str(ccgate_home)},
    )
    assert rc == 0, err
    patterns = json.loads((ccgate_home / "patterns.json").read_text())
    assert patterns["src/a.py"]["read_count"] == 5   # 3 + 2
    assert patterns["src/b.py"]["read_count"] == 5


def test_import_rejects_absolute_path(tmp_path):
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()

    digest = {
        "digest_id": "bad-1",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [
            {"path": "/etc/passwd", "read_count": 1, "cache_block_count": 0},
        ],
    }
    digest_file = tmp_path / "bad_digest.json"
    digest_file.write_text(json.dumps(digest))

    rc, _, err = _run(
        "import", str(digest_file),
        env_extra={"CCGATE_HOME": str(ccgate_home)},
    )
    assert rc != 0
    # patterns.json must NOT have been created
    assert not (ccgate_home / "patterns.json").exists()


def test_import_rejects_dotdot(tmp_path):
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()

    digest = {
        "digest_id": "bad-2",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [
            {"path": "../secret/file.py", "read_count": 1, "cache_block_count": 0},
        ],
    }
    digest_file = tmp_path / "dotdot_digest.json"
    digest_file.write_text(json.dumps(digest))

    rc, _, err = _run(
        "import", str(digest_file),
        env_extra={"CCGATE_HOME": str(ccgate_home)},
    )
    assert rc != 0
    assert not (ccgate_home / "patterns.json").exists()


def test_import_atomic(tmp_path, monkeypatch):
    # If os.replace raises, patterns.json must remain unchanged
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()
    original = {"src/keep.py": {"read_count": 10, "cache_block_count": 0}}
    (ccgate_home / "patterns.json").write_text(json.dumps(original))

    digest = {
        "digest_id": "atomic-test",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [{"path": "src/new.py", "read_count": 1, "cache_block_count": 0}],
    }
    digest_file = tmp_path / "digest.json"
    digest_file.write_text(json.dumps(digest))

    # Patch os.replace inside digest module to raise before commit
    import ccgate.scripts.digest as digest_mod
    original_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(digest_mod.os, "replace", failing_replace)

    with pytest.raises(OSError):
        digest_mod._do_import(str(digest_file), str(ccgate_home))

    # patterns.json unchanged
    surviving = json.loads((ccgate_home / "patterns.json").read_text())
    assert surviving == original


def test_round_trip(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_path = str(tmp_path / "project" / "src" / "app.py")
    _make_rc(tmp_path, "sess1", [abs_path, abs_path, abs_path])

    # Export
    out1 = tmp_path / "digest1.json"
    rc, _, err = _run(
        "export", "--out", str(out1), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err

    # Import into fresh home
    ccgate_home2 = tmp_path / "home2"
    ccgate_home2.mkdir()
    rc, _, err = _run(
        "import", str(out1),
        env_extra={"CCGATE_HOME": str(ccgate_home2)},
    )
    assert rc == 0, err

    # Verify patterns were merged
    patterns = json.loads((ccgate_home2 / "patterns.json").read_text())
    # src/app.py appears 3 times in file_log → read_count == 3
    rel = os.path.relpath(abs_path, cwd)
    assert patterns[rel]["read_count"] == 3
