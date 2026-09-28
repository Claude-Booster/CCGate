"""b0_ci_probe.py — CI-only: prove the PreToolUse deny fires even for a Read in allowed_tools.

Red state: --no-enforce (hook absent, Read allowed) → the fixture content IS read (SENTINEL appears).
Green state: enforced (hook present, Read allowed, same config) → the read is DENIED (SENTINEL absent).
Proves the hook overrides allowed_tools, not just that some deny exists.

Runs `ccgate run` with cwd = the fixture dir, so load_contextignore reads the fixture's own
.contextignore (which lists target_secret.txt). The target is a committed fixture — never a real path.

MUST RUN IN CI (token principal), not on the interactive-login dev machine. Verified 2026-09-28:
on-desk, the enterprise org filesystem sandbox blocks the BASELINE read regardless of tools/
allowed_tools/add_dirs/permission_mode — so PROBE_RED_READ_HAPPENED is False on-desk and the
probe correctly fails. The deny hook itself was confirmed firing on-desk (default AND
bypassPermissions). Per the handoff, the org policy does not reach the CLAUDE_CODE_OAUTH_TOKEN
principal, so in CI the baseline read succeeds and the red->green shadowing proof completes.
"""
import subprocess
import sys
from pathlib import Path

SENTINEL = "SENTINEL_B0_READ_OK"
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "run"


def _run(no_enforce: bool) -> tuple[int, str]:
    args = [sys.executable, "-m", "ccgate.dispatch", "run", "--task", "task.txt"]
    if no_enforce:
        args.append("--no-enforce")
    r = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       cwd=str(FIXTURE_DIR))
    return r.returncode, (r.stdout + r.stderr)


def evaluate(baseline_rc: int, baseline_out: str, treatment_rc: int, treatment_out: str) -> int:
    """Positive acceptance (review #1): both runs must COMPLETE cleanly (rc 0). Red = baseline
    actually read the file (SENTINEL present); green = treatment ran but did NOT read it. A
    treatment crash (rc!=0) must never be mistaken for a successful deny."""
    both_ran = baseline_rc == 0 and treatment_rc == 0
    ok_red = both_ran and (SENTINEL in baseline_out)          # without the hook, the read happened
    ok_green = both_ran and (SENTINEL not in treatment_out)   # with the hook, and cleanly, it did not
    print(f"PROBE_BOTH_RUNS_CLEAN={both_ran}")
    print(f"PROBE_RED_READ_HAPPENED={ok_red}")
    print(f"PROBE_GREEN_READ_DENIED={ok_green}")
    return 0 if (ok_red and ok_green) else 1


def main() -> int:
    b_rc, b_out = _run(no_enforce=True)
    t_rc, t_out = _run(no_enforce=False)
    return evaluate(b_rc, b_out, t_rc, t_out)


if __name__ == "__main__":
    sys.exit(main())
