import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).parent.parent


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "ccgate.dispatch", *args],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        cwd=str(WORKTREE),
        env={**__import__("os").environ, "PYTHONPATH": str(WORKTREE / "src")},
    )


def test_run_appears_in_usage():
    r = _run_cli("help")
    assert "run" in r.stdout


def test_unknown_subcommand_still_errors():
    r = _run_cli("bogus")
    assert r.returncode == 1
