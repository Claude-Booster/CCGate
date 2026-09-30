"""b1b_ci_probe.py — CI-only: prove F3 truncates over-budget matched Bash output.

on-desk this cannot run — the org sandbox blocks Bash (spec §3/§7). Baseline (bashCap off)
sees full output; treatment (bashCap on) sees the ccgate F3 marker and less output. Detection
reads the run-record JSONL (deterministic) rather than grepping stdout (model-dependent)."""
import json
import subprocess
import sys
from pathlib import Path

MARK = "ccgate F3"
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "run_b1b"


def _parse_truncations(output: str) -> int:
    """Extract F3 truncation count from a ccgate run output string.

    The run record path is printed as 'run record: <path>' by cli.main(). The F3 hook
    writes a summary event there; grepping stdout for the marker is unreliable because
    updatedToolOutput goes to the model, not to stdout (spec §7 — CI-only proof)."""
    for line in output.splitlines():
        if line.startswith("run record:"):
            path = Path(line.split(":", 1)[1].strip())
            if not path.exists():
                return 0
            for jline in path.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(jline)
                    if (ev.get("type") == "ccgate_event"
                            and ev.get("rule") == "F3"
                            and ev.get("summary")):
                        return int(ev.get("truncations", 0))
                except (json.JSONDecodeError, ValueError):
                    pass
    return 0


def evaluate(base_rc: int, base_truncations: int, treat_rc: int, treat_truncations: int) -> int:
    both = base_rc == 0 and treat_rc == 0
    ok = both and (base_truncations == 0) and (treat_truncations > 0)
    print(f"PROBE_BOTH_CLEAN={both}")
    print(f"PROBE_TREATMENT_TRUNCATED={treat_truncations > 0}")
    return 0 if ok else 1


def _run(enabled: bool) -> tuple[int, str]:
    # bashEnabled is config-only (no bool env override); write/remove a project config
    # around each run. Prefix must match the fixture command so the 50k-char output is capped.
    cfgdir = FIX / ".ccgate"
    cfgfile = cfgdir / "config.json"
    if enabled:
        cfgdir.mkdir(exist_ok=True)
        cfgfile.write_text(json.dumps({"bashEnabled": True, "bashCapPrefixes": ["python -c"]}),
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
    return evaluate(b_rc, _parse_truncations(b_out), t_rc, _parse_truncations(t_out))


if __name__ == "__main__":
    sys.exit(main())
