import json
import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).parent.parent
FIXTURE = WORKTREE / "tests" / "fixtures" / "baseline" / "first_turn.jsonl"


def _run(*args):
    """Run baseline as a subprocess; return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "ccgate.scripts.baseline", *args],
        capture_output=True,
        text=True,
        cwd=str(WORKTREE),
        env={**__import__("os").environ, "PYTHONPATH": str(WORKTREE / "src")},
    )
    return result.returncode, result.stdout, result.stderr


def test_passes_on_fixture():
    rc, out, _ = _run("--fixture", str(FIXTURE))
    assert rc == 0
    assert "PASS" in out
    assert "8,000" in out


def test_fails_above_cap(tmp_path):
    # Write a fixture with 13 000 tokens — above the default 12 000 cap
    fixture = tmp_path / "first_turn.jsonl"
    fixture.write_text(
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-21T00:00:00Z",
            "message": {
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 13000, "output_tokens": 10,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        }) + "\n",
        encoding="utf-8",
    )
    rc, out, _ = _run("--fixture", str(fixture))
    assert rc == 1
    assert "FAIL" in out


def test_custom_cap_flag():
    # Bundled fixture has 8 000; cap 7 000 → should fail
    rc, out, _ = _run("--fixture", str(FIXTURE), "--cap", "7000")
    assert rc == 1
    assert "FAIL" in out


def test_json_output():
    rc, out, _ = _run("--fixture", str(FIXTURE), "--json")
    assert rc == 0
    data = json.loads(out)
    assert data["tokens"] == 8000
    assert data["cap"] == 12000
    assert data["pass"] is True


def test_missing_fixture(tmp_path):
    rc, _, err = _run("--fixture", str(tmp_path / "nonexistent.jsonl"))
    assert rc == 1
    assert "not found" in err.lower() or "fixture" in err.lower()
