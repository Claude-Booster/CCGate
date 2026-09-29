"""b1b_ci_probe.py — CI-only: prove F3 truncates over-budget matched Bash output.

on-desk this cannot run — the org sandbox blocks Bash (spec §3/§7). Baseline (bashCap off)
sees full output; treatment (bashCap on) sees the ccgate F3 marker and less output. Step 0
prints raw PostToolUse input_data (print, not assert — the sandbox fakes calls silently)."""
import subprocess
import sys
from pathlib import Path

MARK = "ccgate F3"
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "run_b1b"


def evaluate(base_rc: int, base_out: str, treat_rc: int, treat_out: str) -> int:
    both = base_rc == 0 and treat_rc == 0
    ok = both and (MARK not in base_out) and (MARK in treat_out)
    print(f"PROBE_BOTH_CLEAN={both}")
    print(f"PROBE_TREATMENT_TRUNCATED={MARK in treat_out}")
    return 0 if ok else 1


def _run(enabled: bool) -> tuple[int, str]:
    # bashCapEnabled is config-only (no bool env override); write/remove a project config
    # around each run. Prefix must match the fixture command so the 50k-char output is capped.
    import json
    cfgdir = FIX / ".ccgate"
    cfgfile = cfgdir / "config.json"
    if enabled:
        cfgdir.mkdir(exist_ok=True)
        cfgfile.write_text(json.dumps({"bashCapEnabled": True, "bashCapPrefixes": ["python -c"]}),
                           encoding="utf-8")
    elif cfgfile.exists():
        cfgfile.unlink()
    try:
        r = subprocess.run([sys.executable, "-m", "ccgate.dispatch", "run", "--task", "task.txt"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=str(FIX))
        return r.returncode, r.stdout + r.stderr
    finally:
        if cfgfile.exists():
            cfgfile.unlink()


def main() -> int:
    b_rc, b_out = _run(enabled=False)
    t_rc, t_out = _run(enabled=True)
    return evaluate(b_rc, b_out, t_rc, t_out)


if __name__ == "__main__":
    sys.exit(main())
