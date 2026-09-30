"""b1a_ci_probe.py — CI-only: prove the F1 Bash-read deny blocks `cat <ignored>`.

Baseline (bashReadPrefixes empty) lets cat run -> the fixture SENTINEL appears (the read
happened). Treatment (bashReadPrefixes set) denies -> the run record carries an F1 bash-deny
event and the SENTINEL is absent. Pass condition is the deterministic event (not stdout prose)
plus the baseline sentinel (spec §9; feedback-verify-mechanism-not-green)."""
import json
import subprocess
import sys
from pathlib import Path

MARK = "SENTINEL_B1A_READ_OK"
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "run_b1a"


def _parse_bash_denies(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("run record:"):
            p = Path(line.split(":", 1)[1].strip())
            if not p.exists():
                return 0
            for jl in p.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(jl)
                    if (e.get("rule") == "F1" and e.get("surface") == "bash"
                            and e.get("summary")):
                        return int(e.get("denies", 0))
                except (json.JSONDecodeError, ValueError):
                    pass
    return 0


def evaluate(base_rc: int, base_out: str, treat_rc: int, treat_out: str, treat_denies: int) -> int:
    both = base_rc == 0 and treat_rc == 0
    ok = both and (MARK in base_out) and (treat_denies > 0) and (MARK not in treat_out)
    print(f"PROBE_BOTH_CLEAN={both}")
    print(f"PROBE_BASELINE_READ={MARK in base_out}")
    print(f"PROBE_TREATMENT_DENIED={treat_denies > 0}")
    return 0 if ok else 1


def _contextignore_for(deny_enabled: bool) -> str:
    """Baseline (deny disabled) uses an EMPTY .contextignore so no bash deny fires and the
    sentinel appears; treatment uses the real pattern. bashReadPrefixes stays at its (list-
    merged, non-emptyable) default in both — the toggle is .contextignore, not the prefix
    list. Review Finding 1: `bashReadPrefixes: []` can't disable via project config (list-merge)."""
    return "target_marker.txt\n" if deny_enabled else ""


def _run(deny_enabled: bool) -> tuple[int, str]:
    ci = FIX / ".contextignore"
    cfgdir = FIX / ".ccgate"
    cfg = cfgdir / "config.json"
    cfgdir.mkdir(exist_ok=True)
    ci.write_text(_contextignore_for(deny_enabled), encoding="utf-8")
    cfg.write_text(json.dumps({"bashEnabled": True}), encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-m", "ccgate.dispatch", "run", "--task", "task.txt"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=str(FIX))
        return r.returncode, r.stdout + r.stderr
    finally:
        if cfg.exists():
            cfg.unlink()
        ci.write_text("target_marker.txt\n", encoding="utf-8")  # restore committed fixture content


def main() -> int:
    b_rc, b_out = _run(deny_enabled=False)   # baseline: empty .contextignore -> cat runs
    t_rc, t_out = _run(deny_enabled=True)    # treatment: real pattern -> cat denied
    return evaluate(b_rc, b_out, t_rc, t_out, _parse_bash_denies(t_out))


if __name__ == "__main__":
    sys.exit(main())
