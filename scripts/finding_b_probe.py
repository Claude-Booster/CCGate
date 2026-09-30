"""finding_b_probe.py — CI-only diagnostic for Finding B (Track B unattended-loop premise).

Both measure-ab variance runs gave_up at MAX_TURNS=40 on a bounded task built from the project's
own deferred items. This runs the SAME task ONCE at max_turns=80 and captures WHY it stopped, not
just that it did, so 'gave up again' can be split:
  - completes            -> the 40 cap was the problem; premise survives.
  - turns==80            -> still hitting the cap; task needs >80 turns (capability/efficiency).
  - turns<80, pytest fail -> task incomplete (too hard / model stuck).
  - turns<80, pytest pass but artifacts missing -> done-gate too strict, not a capability verdict.
The agent's last output (tail) disambiguates looping vs stuck vs thought-it-was-done. Also confirms
the deferred Task-4 minor: does the recorded turn count actually reach the cap?"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ccgate.measure import (  # noqa: E402
    extract_run_metrics, tokens_injected_present, ceiling_test_added, classify_completion,
)

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests" / "fixtures" / "measure_ab"
MAX_TURNS = 80
DONE_TESTS = ["tests/test_config.py", "tests/test_bashcap.py"]


def _reset(base: str) -> None:
    subprocess.run(["git", "reset", "--hard", base], cwd=str(REPO), check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fdq", "src", "tests"], cwd=str(REPO), check=True,
                   capture_output=True, text=True)


def main() -> int:
    if not os.environ.get("CI"):
        raise RuntimeError("finding_b_probe is CI-only: it hard-resets the working tree.")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                          capture_output=True, text=True).stdout.strip()
    _reset(base)
    r = subprocess.run([sys.executable, "-m", "ccgate.dispatch", "run", "--task", str(FIX / "task.txt"),
                        "--max-turns", str(MAX_TURNS)],
                       cwd=str(REPO), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    out = r.stdout + r.stderr
    rec = next((ln.split(":", 1)[1].strip() for ln in out.splitlines()
                if ln.startswith("run record:")), None)
    m = extract_run_metrics(rec) if rec and Path(rec).exists() else {"turns": 0, "tokens_total": 0}
    # done-gate component breakdown (harness-run, never the agent's report)
    pt = subprocess.run([sys.executable, "-m", "pytest", *DONE_TESTS, "-q"],
                        cwd=str(REPO), capture_output=True, text=True)
    pytest_ok = pt.returncode == 0
    ti = tokens_injected_present((REPO / "src" / "ccgate" / "run" / "bashcap.py").read_text(encoding="utf-8"))
    ct = ceiling_test_added((REPO / "tests" / "test_config.py").read_text(encoding="utf-8"))
    done_ok = pytest_ok and ti and ct
    turns = m["turns"]
    max_hit = turns >= MAX_TURNS
    complete, reason = classify_completion(done_ok, r.returncode, max_hit)
    print(f"FINDINGB turns={turns}/{MAX_TURNS} max_turns_hit={max_hit} tokens={m['tokens_total']} rc={r.returncode}")
    print(f"FINDINGB done_ok={done_ok} pytest_ok={pytest_ok} tokens_injected={ti} ceiling_test_added={ct}")
    print(f"FINDINGB complete={complete} reason={reason}")
    print("FINDINGB --- agent output tail (last ~2500 chars) ---")
    print(out[-2500:])
    _reset(base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
