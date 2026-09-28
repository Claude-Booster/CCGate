"""test_miss_audit_g4g5.py — G4/G5 gate tests against synthesized fixtures."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")
FIXTURES = Path(__file__).parent / "fixtures"
CLEAN = FIXTURES / "g4g5_clean.jsonl"
DIRTY = FIXTURES / "g4g5_dirty.jsonl"


def _run(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    return subprocess.run(
        [PYTHON, "-m", "ccgate.scripts.miss_audit", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def test_clean_assert_exits_0():
    """g4g5_clean.jsonl: hit_ratio=1.0, zero D1 misses — --assert exits 0."""
    r = _run("--assert", str(CLEAN))
    assert r.returncode == 0, r.stderr


def test_dirty_assert_exits_1():
    """g4g5_dirty.jsonl: hit_ratio=0.1, 9 D1.model_switch — --assert exits 1."""
    r = _run("--assert", str(DIRTY))
    assert r.returncode == 1


def test_dirty_reports_g4():
    """G4 failure message appears in stderr."""
    r = _run("--assert", str(DIRTY))
    assert "G4" in r.stderr, f"stderr: {r.stderr!r}"


def test_dirty_reports_g5():
    """G5 failure message appears in stderr."""
    r = _run("--assert", str(DIRTY))
    assert "G5" in r.stderr, f"stderr: {r.stderr!r}"


def test_json_output_includes_hit_ratio():
    """--json output has summary.hit_ratio field."""
    import json
    r = _run("--json", str(CLEAN))
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "hit_ratio" in data["summary"]
    assert data["summary"]["hit_ratio"] == pytest.approx(1.0)
