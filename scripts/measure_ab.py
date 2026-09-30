"""measure_ab.py — CI-only: A/B the F1/F3 enforcement effect on a real task (spec).

Variance check (2 enforced runs) -> derive N -> A/B (N/arm) with a per-run turn cap and a
clean-tree reset before each run -> harness-run done-check -> pre-declared verdict. Runs cost
real tokens; the turn cap bounds a pathological run. The done-check shells out to pytest from
here — never trusts the agent's own report (spec §4)."""
import os
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ccgate.measure import (  # noqa: E402
    extract_run_metrics, derive_n, decide, classify_completion, tokens_injected_present,
    ceiling_test_added,
)

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests" / "fixtures" / "measure_ab"
MAX_TURNS = 40
DONE_TESTS = ["tests/test_config.py", "tests/test_bashcap.py"]


def assemble_report(baseline: list[dict], enforced: list[dict], diag: dict) -> dict:
    verdict = decide(baseline, enforced)
    f1_fired = diag.get("f1_fires_total", 0) > 0
    notes = ""
    if not f1_fired:
        notes = ("F1 did not fire on real work in this repo — the A/B measures F3 alone "
                 "(spec §5 headline finding).")
    return {**verdict, "f1_fired": f1_fired, "diagnostics": diag, "notes": notes}


def _reset_tree(base_sha: str) -> None:
    # DESTRUCTIVE (git reset --hard + git clean). Refuse outside CI so a local invocation can't
    # silently wipe a developer's working tree (review Finding 4).
    if not os.environ.get("CI"):
        raise RuntimeError("measure_ab is CI-only: it hard-resets the working tree. "
                           "Set CI=1 to confirm you are in a disposable checkout.")
    subprocess.run(["git", "reset", "--hard", base_sha], cwd=str(REPO), check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fdq", "src", "tests"], cwd=str(REPO), check=True,
                   capture_output=True, text=True)


def _done_ok() -> bool:
    """Harness-run done-check (spec §4): the AGENT's report is ignored. Requires (a) the target
    tests pass, (b) the boundary (ceiling) test was actually added to test_config.py, and (c)
    the tokens_injected field was added to the F3 event — so 'green' implies the work was done,
    not that the pre-existing base tests still pass (review Finding 1)."""
    r = subprocess.run([sys.executable, "-m", "pytest", *DONE_TESTS, "-q"],
                       cwd=str(REPO), capture_output=True, text=True)
    if r.returncode != 0:
        return False
    bashcap = (REPO / "src" / "ccgate" / "run" / "bashcap.py").read_text(encoding="utf-8")
    test_config = (REPO / "tests" / "test_config.py").read_text(encoding="utf-8")
    return tokens_injected_present(bashcap) and ceiling_test_added(test_config)


def _one_run(enforce: bool, base_sha: str) -> dict:
    _reset_tree(base_sha)
    args = [sys.executable, "-m", "ccgate.dispatch", "run", "--task", str(FIX / "task.txt"),
            "--max-turns", str(MAX_TURNS)]
    if not enforce:
        args.append("--no-enforce")
    r = subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    rec = None
    for line in (r.stdout + r.stderr).splitlines():
        if line.startswith("run record:"):
            rec = line.split(":", 1)[1].strip()
    metrics = extract_run_metrics(rec) if rec and Path(rec).exists() else {
        "tokens_total": 0, "turns": 0, "f1_fires": 0, "f3_truncations": 0, "misses_per_1k": 0.0}
    max_turns_hit = metrics.get("turns", 0) >= MAX_TURNS
    complete, reason = classify_completion(_done_ok(), r.returncode, max_turns_hit)
    return {**metrics, "complete": complete, "reason": reason}


def main() -> int:
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                              capture_output=True, text=True).stdout.strip()
    v1, v2 = _one_run(True, base_sha), _one_run(True, base_sha)   # variance check: 2 enforced runs
    n = derive_n(v1["tokens_total"], v2["tokens_total"])
    print(f"VARIANCE enforced tokens: {v1['tokens_total']} vs {v2['tokens_total']} -> N={n}")
    if n is None:
        print("VERDICT: UNMEASURABLE (variance > 25% — effect not detectable at feasible cost)")
        _reset_tree(base_sha)
        return 0
    enforced = [v1, v2] + [_one_run(True, base_sha) for _ in range(max(0, n - 2))]
    baseline = [_one_run(False, base_sha) for _ in range(n)]
    e_done = [r for r in enforced if r["complete"]]
    diag = {"f1_fires_total": sum(r["f1_fires"] for r in enforced),
            "f3_truncations_total": sum(r["f3_truncations"] for r in enforced),
            "enforced_median_turns": statistics.median(r["turns"] for r in e_done) if e_done else None,
            "enforced_median_misses_per_1k": statistics.median(r["misses_per_1k"] for r in e_done) if e_done else None,
            "enforced_reasons": [r["reason"] for r in enforced],
            "baseline_reasons": [r["reason"] for r in baseline]}
    rep = assemble_report(baseline, enforced, diag)
    _reset_tree(base_sha)   # leave the tree clean
    print(f"VERDICT: {rep['verdict']} — {rep['reason']}")
    print(f"F1_FIRED={rep['f1_fired']} DIAG={rep['diagnostics']}")
    print(f"COMPLETION baseline={rep.get('baseline_rate')} enforced={rep.get('enforced_rate')}")
    if rep["notes"]:
        print("NOTES:", rep["notes"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
