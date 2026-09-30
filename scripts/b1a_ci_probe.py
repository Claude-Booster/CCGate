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


def _run(readers_enabled: bool) -> tuple[int, str]:
    cfgdir = FIX / ".ccgate"
    cfg = cfgdir / "config.json"
    cfgdir.mkdir(exist_ok=True)
    body = {"bashEnabled": True, "bashReadPrefixes": (["cat"] if readers_enabled else [])}
    cfg.write_text(json.dumps(body), encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-m", "ccgate.dispatch", "run", "--task", "task.txt"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=str(FIX))
        return r.returncode, r.stdout + r.stderr
    finally:
        if cfg.exists():
            cfg.unlink()


def main() -> int:
    b_rc, b_out = _run(readers_enabled=False)
    t_rc, t_out = _run(readers_enabled=True)
    return evaluate(b_rc, b_out, t_rc, t_out, _parse_bash_denies(t_out))


if __name__ == "__main__":
    sys.exit(main())
