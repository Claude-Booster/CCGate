"""b0_ci_probe.py — CI-only: prove the PreToolUse deny fires even for a Read in allowed_tools.

Red state: --no-enforce (hook absent, Read allowed) → the fixture content IS read (SENTINEL appears).
Green state: enforced (hook present, Read allowed, same config) → the read is DENIED (SENTINEL absent).
Proves the hook overrides allowed_tools, not just that some deny exists.

Runs `ccgate run` with cwd = the fixture dir, so load_contextignore reads the fixture's own
.contextignore (which lists target_secret.txt). The target is a committed fixture — never a real path.
"""
import subprocess
import sys
from pathlib import Path

SENTINEL = "SENTINEL_B0_READ_OK"
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "run"


def _run(no_enforce: bool) -> str:
    args = [sys.executable, "-m", "ccgate.dispatch", "run", "--task", "task.txt"]
    if no_enforce:
        args.append("--no-enforce")
    r = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       cwd=str(FIXTURE_DIR))
    return r.stdout + r.stderr


def main() -> int:
    baseline = _run(no_enforce=True)
    treatment = _run(no_enforce=False)
    ok_red = SENTINEL in baseline          # without the hook, the read happened
    ok_green = SENTINEL not in treatment   # with the hook, it did not
    print(f"PROBE_RED_READ_HAPPENED={ok_red}")
    print(f"PROBE_GREEN_READ_DENIED={ok_green}")
    return 0 if (ok_red and ok_green) else 1


if __name__ == "__main__":
    sys.exit(main())
