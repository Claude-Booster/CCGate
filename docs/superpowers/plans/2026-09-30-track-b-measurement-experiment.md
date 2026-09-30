# Track B — Enforcement Measurement Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the CI harness and pure decision logic to measure whether F1/F3 enforcement produces a net tokens-per-task saving on a real task, and apply a pre-declared rule that gates the B1c build decision.

**Architecture:** Pure, unit-tested logic in a new `src/ccgate/measure.py` (metric extraction from a run record, the derive-N rule, the decision rule) plus a thin CI-only orchestrator `scripts/measure_ab.py` (variance check → derive N → A/B with per-run turn cap and clean-tree reset → harness-run done-check → verdict). A `--max-turns` option is added to `ccgate run` so each run has a cost cap.

**Tech Stack:** Python 3.11+, `claude-agent-sdk` (optional `run` extra), pytest, stdlib `statistics`/`subprocess`.

**Spec:** `docs/superpowers/specs/2026-09-30-track-b-measurement-experiment-design.md`

## Global Constraints

- **This measures; it does not build B1c.** The experiment gates that decision (spec §1, §8).
- **Metric:** tokens/task primary (`grand_total_input + output`); misses/1k secondary (report, don't gate); turns + completion-rate interpretive (spec §2).
- **Decision rule (pre-declared, computed from data):** VOID if either arm completes ≤50% of runs; else build B1c only if enforced median tokens/task < the **minimum** observed baseline tokens/task AND enforced completion rate ≥ baseline completion rate; else don't build (spec §6).
- **Derive N from the variance spread, don't default it:** `d ≤ ~10%` → N=5; `~10–25%` → N≥9; `d > ~25%` → unmeasurable, don't run the A/B (spec §3).
- **Done-check runs OUTSIDE the agent's session** — the harness runs pytest itself; never trust the agent's own test report (spec §4).
- **Per-run turn cap** so a pathological run can't burn the budget; a capped run is `turn_cap` non-completion, not a crash (spec §7).
- **Realistic `.contextignore`; F1-not-firing is a finding**, not something to engineer around (spec §5).
- **No git hook bypass; TDD.** Pure logic is agent-runnable; the A/B runs are CI-only (org sandbox).

## Review Focus

- **A non-completing run must not look "cheap"** — it's excluded from the tokens median but counted in completion rate with a reason (`gave_up`/`turn_cap`/`error`). → Task 3 + Task 4 tests.
- **The decision rule uses baseline *minimum*, not mean/median** — an incorrectly-coded rule that compares against baseline mean would pass effects that don't clear the spread. → Task 3 test.
- **derive_n boundaries** — exactly at d=10% and d=25% (which branch owns the boundary). → Task 3 test.
- **Metric extraction tolerates a run record with F1/F3 summary lines** — `summary:true` events must not be double-counted as fires (same dual shape as the probes). → Task 2 test.
- **`--max-turns` omitted → no cap** (None passed through, not 0) so normal `ccgate run` is unchanged. → Task 1 test.

---

## Task 1: `--max-turns` cost cap on `ccgate run`

**Files:**
- Modify: `src/ccgate/run/cli.py` (`_build_options`, `_factory`, `run_task`, `main`)
- Test: `tests/test_run_cli.py`

**Interfaces:**
- Produces: `run_task(..., max_turns: int | None = None)`; options dict key `"max_turns"`; `ccgate run --max-turns N`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_run_cli.py
def test_max_turns_in_options_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    from ccgate.run.cli import _build_options
    from ccgate.config import load_config
    opts = _build_options([], enforce=True, config=load_config(), recorder=None, max_turns=40)
    assert opts["max_turns"] == 40


def test_max_turns_defaults_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    from ccgate.run.cli import _build_options
    from ccgate.config import load_config
    opts = _build_options([], enforce=True, config=load_config(), recorder=None)
    assert opts["max_turns"] is None      # omitted -> no cap
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_run_cli.py -k max_turns -q`
Expected: FAIL — `_build_options()` has no `max_turns` param / no `"max_turns"` key.

- [ ] **Step 3: Thread `max_turns` through**

In `src/ccgate/run/cli.py`, change `_build_options` signature to `def _build_options(patterns, enforce, config, recorder, max_turns=None):` and add `"max_turns": max_turns,` to the returned dict. In `_factory`, pass it to the SDK (only meaningful when not None; the SDK default is None):

```python
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        hooks=hooks,
        setting_sources=options["setting_sources"],
        tools=options["tools"],
        allowed_tools=options["allowed_tools"],
        max_turns=options["max_turns"],
    ))
```

In `run_task`, add `max_turns: int | None = None` to the signature and pass it: `options = _build_options(patterns, enforce, config, recorder, max_turns)`. In `main`, add the arg and pass it:

```python
    parser.add_argument("--max-turns", type=int, default=None,
                        help="Per-run turn cap (measurement cost control). Omit for no cap.")
    ...
    path = asyncio.run(run_task(prompt, enforce=not args.no_enforce, cwd=Path.cwd(),
                                client_factory=_factory, max_turns=args.max_turns))
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_run_cli.py -q`
Expected: PASS (existing run-cli tests unaffected — they omit `max_turns`, which defaults to None; update `test_factory_builds_two_pretooluse_matchers` only if it inspects the dict — it does not).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/cli.py tests/test_run_cli.py
git commit -m "feat(run): --max-turns cost cap on ccgate run"
```

---

## Task 2: metric extraction from a run record

**Files:**
- Create: `src/ccgate/measure.py`
- Test: `tests/test_measure.py`

**Interfaces:**
- Produces: `extract_run_metrics(record_path: str | Path) -> dict` → `{"tokens_total": int, "turns": int, "f1_fires": int, "f3_truncations": int, "misses_per_1k": float, "complete_marker": bool}`.

- [ ] **Step 1: Confirm the record shape (read, don't guess)**

Read `src/ccgate/run/record.py` (`append_assistant`, `append_event`, `finish`) to confirm: assistant lines carry `message.usage` with `input_tokens`/`cache_read_input_tokens`/`cache_creation_input_tokens`/`output_tokens`; `ccgate_event` lines carry `rule` and optional `summary`; the terminal line is `ccgate_run_end`. If a field name differs, use the confirmed name and ledger a `Ruling:`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_measure.py
import json
from pathlib import Path
from ccgate.measure import extract_run_metrics


def _write_record(tmp_path):
    lines = [
        {"message": {"model": "m", "usage": {"input_tokens": 100, "cache_read_input_tokens": 900,
                                             "cache_creation_input_tokens": 0, "output_tokens": 50}}},
        {"message": {"model": "m", "usage": {"input_tokens": 10, "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 200, "output_tokens": 20}}},
        {"type": "ccgate_event", "rule": "F1", "surface": "bash", "matched_token": "x.lock"},
        {"type": "ccgate_event", "rule": "F3", "command_prefix": "pytest", "chars_elided": 5000},
        {"type": "ccgate_event", "rule": "F3", "summary": True, "truncations": 1},  # must NOT count as a fire
        {"type": "ccgate_run_end"},
    ]
    p = tmp_path / "run.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return p


def test_extract_run_metrics(tmp_path):
    m = extract_run_metrics(_write_record(tmp_path))
    # tokens_total = (100+900+0 + 50) + (10+0+200 + 20) = 1050 + 230 = 1280
    assert m["tokens_total"] == 1280
    assert m["turns"] == 2                    # two assistant usage lines
    assert m["f1_fires"] == 1
    assert m["f3_truncations"] == 1           # summary line excluded
    assert m["complete_marker"] is True       # ccgate_run_end present
    assert m["misses_per_1k"] >= 0            # secondary, not gated


def test_extract_incomplete_record_has_no_marker(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps({"message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0, "output_tokens": 1}}}) + "\n", encoding="utf-8")
    assert extract_run_metrics(p)["complete_marker"] is False
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_measure.py -q`
Expected: FAIL — `No module named 'ccgate.measure'`.

- [ ] **Step 4: Implement**

```python
# src/ccgate/measure.py
"""measure.py — pure metrics + decision logic for the Track B enforcement A/B (spec)."""
from __future__ import annotations

import json
from pathlib import Path


def extract_run_metrics(record_path) -> dict:
    """Parse a ccgate run record (JSONL) into experiment metrics. Pure — no SDK, no subprocess.
    misses_per_1k is a cache-miss approximation (requests that created cache beyond the first),
    secondary and not gated (spec §2)."""
    tokens_total = 0
    turns = 0
    f1_fires = 0
    f3_truncations = 0
    complete_marker = False
    cache_creations = 0
    for line in Path(record_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message") if isinstance(ev, dict) else None
        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            u = msg["usage"]
            gti = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0))
            tokens_total += gti + u.get("output_tokens", 0)
            turns += 1
            if u.get("cache_creation_input_tokens", 0) > 0:
                cache_creations += 1
            continue
        if ev.get("type") == "ccgate_event" and not ev.get("summary"):
            if ev.get("rule") == "F1":
                f1_fires += 1
            elif ev.get("rule") == "F3":
                f3_truncations += 1
            continue
        if ev.get("type") == "ccgate_run_end":
            complete_marker = True
    misses = max(0, cache_creations - 1)   # the first request always creates cache
    misses_per_1k = (misses / turns * 1000) if turns else 0.0
    return {"tokens_total": tokens_total, "turns": turns, "f1_fires": f1_fires,
            "f3_truncations": f3_truncations, "misses_per_1k": misses_per_1k,
            "complete_marker": complete_marker}
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_measure.py -q`
Expected: PASS (2).

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/measure.py tests/test_measure.py
git commit -m "feat(measure): extract_run_metrics from a run record"
```

---

## Task 3: derive-N rule + decision rule

**Files:**
- Modify: `src/ccgate/measure.py`
- Test: `tests/test_measure.py`

**Interfaces:**
- Produces: `derive_n(t1: float, t2: float) -> int | None` (None = unmeasurable); `decide(baseline: list[dict], enforced: list[dict]) -> dict` → `{"verdict": "VOID"|"BUILD_B1C"|"NO_BUILD", "reason": str, ...}`. Each arm entry is `{"tokens_total": int, "complete": bool}`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_measure.py
from ccgate.measure import derive_n, decide


def test_derive_n_boundaries():
    assert derive_n(1000, 1000) == 5          # d=0 -> 5
    assert derive_n(1000, 1090) == 5          # d≈8.6% (<=10) -> 5
    assert derive_n(1000, 1200) == 9          # d≈18% (10<d<=25) -> 9


def test_derive_n_unmeasurable():
    assert derive_n(1000, 1300) is None       # d≈26% (>25) -> None
    assert derive_n(1000, 2000) is None       # d≈67% -> None


def test_decide_excludes_incomplete_from_median():
    base = [{"tokens_total": t, "complete": True} for t in (1000, 1100, 1200)]
    enf = ([{"tokens_total": t, "complete": True} for t in (800, 820, 810)]
           + [{"tokens_total": 0, "complete": False}, {"tokens_total": 0, "complete": False}])
    d = decide(base, enf)   # 3/5 = 60% complete -> not void; median over COMPLETED = 810 (not dragged by 0s)
    assert d["verdict"] == "BUILD_B1C" and d["enforced_median"] == 810


def test_decide_void_when_low_completion():
    base = [{"tokens_total": 100, "complete": True}] + [{"tokens_total": 0, "complete": False}] * 4
    enf = [{"tokens_total": 50, "complete": True}] + [{"tokens_total": 0, "complete": False}] * 4
    assert decide(base, enf)["verdict"] == "VOID"     # 1/5 each -> <=50%


def test_decide_build_when_enforced_below_baseline_min():
    base = [{"tokens_total": t, "complete": True} for t in (1000, 1100, 1200, 1050, 1150)]
    enf = [{"tokens_total": t, "complete": True} for t in (800, 850, 820, 830, 810)]
    d = decide(base, enf)
    assert d["verdict"] == "BUILD_B1C"      # enforced median 820 < baseline min 1000


def test_decide_no_build_when_within_baseline_range():
    base = [{"tokens_total": t, "complete": True} for t in (1000, 1100, 1200, 1050, 1150)]
    enf = [{"tokens_total": t, "complete": True} for t in (1020, 1080, 1090, 1030, 1060)]
    d = decide(base, enf)
    assert d["verdict"] == "NO_BUILD"       # enforced median 1060 >= baseline min 1000
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_measure.py -k "derive_n or decide" -q`
Expected: FAIL — `derive_n`/`decide` not defined.

- [ ] **Step 3: Implement**

```python
# add to src/ccgate/measure.py
import statistics


def derive_n(t1: float, t2: float) -> int | None:
    """N per arm from the two enforced variance runs. d = |t1-t2| / mean on tokens/task.
    d<=10% -> 5; 10<d<=25% -> 9; d>25% -> None (unmeasurable at feasible cost) (spec §3)."""
    mean = (t1 + t2) / 2
    if mean == 0:
        return 5
    d = abs(t1 - t2) / mean
    if d <= 0.10:
        return 5
    if d <= 0.25:
        return 9
    return None


def _completed(arm: list[dict]) -> list[dict]:
    return [r for r in arm if r.get("complete")]


def decide(baseline: list[dict], enforced: list[dict]) -> dict:
    """Pre-declared decision rule (spec §6). Computed from data, not adjustable after seeing it."""
    b_done, e_done = _completed(baseline), _completed(enforced)
    b_rate = len(b_done) / len(baseline) if baseline else 0.0
    e_rate = len(e_done) / len(enforced) if enforced else 0.0
    if b_rate <= 0.5 or e_rate <= 0.5:
        return {"verdict": "VOID", "reason": f"completion too low (baseline {b_rate:.0%}, enforced {e_rate:.0%})",
                "baseline_rate": b_rate, "enforced_rate": e_rate}
    baseline_min = min(r["tokens_total"] for r in b_done)
    enforced_median = statistics.median(r["tokens_total"] for r in e_done)
    if enforced_median < baseline_min and e_rate >= b_rate:
        verdict = "BUILD_B1C"
        reason = f"enforced median {enforced_median} < baseline min {baseline_min} and completion held"
    else:
        verdict = "NO_BUILD"
        reason = (f"enforced median {enforced_median} not below baseline min {baseline_min}"
                  if enforced_median >= baseline_min else
                  f"enforced completion {e_rate:.0%} < baseline {b_rate:.0%}")
    return {"verdict": verdict, "reason": reason, "baseline_min": baseline_min,
            "enforced_median": enforced_median, "baseline_rate": b_rate, "enforced_rate": e_rate}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_measure.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/measure.py tests/test_measure.py
git commit -m "feat(measure): derive_n + pre-declared decision rule"
```

---

## Task 4: done-check + completion-reason classification

**Files:**
- Modify: `src/ccgate/measure.py`
- Test: `tests/test_measure.py`

**Interfaces:**
- Produces: `classify_completion(done_ok: bool, returncode: int, max_turns_hit: bool) -> tuple[bool, str]` → `(complete, reason)` with reason in `{"ok","gave_up","turn_cap","error"}`; `tokens_injected_present(bashcap_source: str) -> bool` (checks the F3 event includes the field).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_measure.py
from ccgate.measure import classify_completion, tokens_injected_present


def test_classify_completion():
    assert classify_completion(True, 0, False) == (True, "ok")
    assert classify_completion(False, 0, True) == (False, "turn_cap")
    assert classify_completion(False, 1, False) == (False, "error")
    assert classify_completion(False, 0, False) == (False, "gave_up")


def test_tokens_injected_present():
    assert tokens_injected_present('event = {"rule": "F3", "tokens_injected": n}') is True
    assert tokens_injected_present('event = {"rule": "F3", "chars_elided": n}') is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_measure.py -k "classify or tokens_injected" -q`
Expected: FAIL — not defined.

- [ ] **Step 3: Implement**

```python
# add to src/ccgate/measure.py
def classify_completion(done_ok: bool, returncode: int, max_turns_hit: bool) -> tuple[bool, str]:
    """Map a run outcome to (complete, reason). Order matters: a done run is 'ok' regardless;
    otherwise a turn-cap hit and a crash are distinguished from a plain give-up (spec §2)."""
    if done_ok:
        return True, "ok"
    if max_turns_hit:
        return False, "turn_cap"
    if returncode != 0:
        return False, "error"
    return False, "gave_up"


def tokens_injected_present(bashcap_source: str) -> bool:
    """True if the F3 event in bashcap source records the tokens_injected field (done-condition
    part 2). A cheap textual check — the harness's pytest run is the real done gate (spec §4)."""
    return "tokens_injected" in bashcap_source
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_measure.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/measure.py tests/test_measure.py
git commit -m "feat(measure): completion classification + tokens_injected done-check"
```

---

## Task 5: A/B harness + fixture + workflow (CI-only)

**Files:**
- Create: `scripts/measure_ab.py`
- Create: `tests/fixtures/measure_ab/task.txt`, `tests/fixtures/measure_ab/.contextignore`
- Create: `.github/workflows/measure-ab.yml`
- Test: `tests/test_measure_ab_harness.py` (pure report/verdict assembly)

**Interfaces:**
- Consumes: `extract_run_metrics`, `derive_n`, `decide`, `classify_completion` (Task 2–4); `ccgate run --max-turns` (Task 1).

- [ ] **Step 1: Create the fixture task + realistic `.contextignore`**

`tests/fixtures/measure_ab/task.txt`:
```
Add the two missing boundary test cases to tests/test_config.py for the 10,000,000 upper bound
of bashCapHeadChars and bashCapTailChars (an out-of-range high value must fall back to the
default), and add a `tokens_injected` field to the F3 event recorded in
src/ccgate/run/bashcap.py (the marker's own character length). Run
`python -m pytest tests/test_config.py tests/test_bashcap.py` and make it pass. Then stop.
```
`tests/fixtures/measure_ab/.contextignore` (realistic — what a real user ignores):
```
*.lock
package-lock.json
src/ccgate.egg-info/
.ccgate/runs/
```

- [ ] **Step 2: Write the harness's pure assembly test**

`tests/test_measure_ab_harness.py`:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from measure_ab import assemble_report  # noqa: E402


def test_assemble_report_verdict_and_diagnostics():
    baseline = [{"tokens_total": t, "complete": True, "reason": "ok"} for t in (1000, 1100, 1200)]
    enforced = [{"tokens_total": t, "complete": True, "reason": "ok"} for t in (800, 820, 810)]
    diag = {"f1_fires_total": 0, "f3_truncations_total": 6}
    rep = assemble_report(baseline, enforced, diag)
    assert rep["verdict"] == "BUILD_B1C"
    assert rep["f1_fired"] is False           # F1 never fired -> headline finding (spec §5)
    assert "F1 did not fire" in rep["notes"]
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_measure_ab_harness.py -q` → FAIL (`No module named 'measure_ab'`).

- [ ] **Step 4: Implement the harness**

`scripts/measure_ab.py`:
```python
"""measure_ab.py — CI-only: A/B the F1/F3 enforcement effect on a real task (spec).

Variance check (2 enforced runs) -> derive N -> A/B (N/arm) with a per-run turn cap and a
clean-tree reset before each run -> harness-run done-check -> pre-declared verdict. Runs cost
real tokens; the turn cap bounds a pathological run."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ccgate.measure import (  # noqa: E402
    extract_run_metrics, derive_n, decide, classify_completion, tokens_injected_present,
)

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests" / "fixtures" / "measure_ab"
MAX_TURNS = 40
DONE_TESTS = ["tests/test_config.py", "tests/test_bashcap.py"]


def assemble_report(baseline: list[dict], enforced: list[dict], diag: dict) -> dict:
    verdict = decide(baseline, enforced)
    f1_fired = diag.get("f1_fires_total", 0) > 0
    notes = []
    if not f1_fired:
        notes.append("F1 did not fire on real work in this repo — the A/B measures F3 alone "
                     "(spec §5 headline finding).")
    return {**verdict, "f1_fired": f1_fired, "diagnostics": diag, "notes": " ".join(notes)}


def _reset_tree(base_sha: str) -> None:
    subprocess.run(["git", "reset", "--hard", base_sha], cwd=str(REPO), check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fdq", "src", "tests"], cwd=str(REPO), check=True,
                   capture_output=True, text=True)


def _done_ok() -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", *DONE_TESTS, "-q"],
                       cwd=str(REPO), capture_output=True, text=True)
    if r.returncode != 0:
        return False
    return tokens_injected_present((REPO / "src" / "ccgate" / "run" / "bashcap.py").read_text(encoding="utf-8"))


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
    # Variance check: 2 enforced runs.
    v1, v2 = _one_run(True, base_sha), _one_run(True, base_sha)
    n = derive_n(v1["tokens_total"], v2["tokens_total"])
    print(f"VARIANCE enforced tokens: {v1['tokens_total']} vs {v2['tokens_total']} -> N={n}")
    if n is None:
        print("VERDICT: UNMEASURABLE (variance > 25% — effect not detectable at feasible cost)")
        return 0
    enforced = [v1, v2] + [_one_run(True, base_sha) for _ in range(max(0, n - 2))]
    baseline = [_one_run(False, base_sha) for _ in range(n)]
    diag = {"f1_fires_total": sum(r["f1_fires"] for r in enforced),
            "f3_truncations_total": sum(r["f3_truncations"] for r in enforced)}
    rep = assemble_report(baseline, enforced, diag)
    _reset_tree(base_sha)   # leave the tree clean
    print(f"VERDICT: {rep['verdict']} — {rep['reason']}")
    print(f"F1_FIRED={rep['f1_fired']} DIAG={rep['diagnostics']}")
    if rep["notes"]:
        print("NOTES:", rep["notes"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the harness assembly test**

Run: `python -m pytest tests/test_measure_ab_harness.py -q`
Expected: PASS.

- [ ] **Step 6: Write the workflow**

`.github/workflows/measure-ab.yml`:
```yaml
name: measure-ab
on: workflow_dispatch
jobs:
  measure:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: npm install -g @anthropic-ai/claude-code
      - run: pip install -e ".[run,dev]"
      - run: git config user.email "ci@example.com" && git config user.name "ci"
      - name: Enforcement A/B
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        run: python scripts/measure_ab.py
```

- [ ] **Step 7: Commit**

```bash
git add scripts/measure_ab.py tests/fixtures/measure_ab tests/test_measure_ab_harness.py .github/workflows/measure-ab.yml
git commit -m "test(measure): A/B harness + fixture + workflow (CI-only)"
```

- [ ] **Step 8: USER/CI VERIFICATION (agent cannot run this)**

Register the workflow on `main` (PR → verify → merge, like the probes), then `gh workflow run measure-ab.yml --ref <this-branch>`. Read the printed `VARIANCE`, `VERDICT`, `F1_FIRED`, `DIAG`, `NOTES`. Record:

Evidence log (fill in): `N=____ verdict=____ enforced_median=____ baseline_min=____ F1_fired=____ f3_truncations=____ completion baseline/enforced=____/____`

Then apply the decision rule's verdict to the B1c question. If `F1_FIRED=False`, note the headline finding (spec §5) regardless of the token verdict.

---

## Sequencing note

Tasks 1–4 are agent-runnable in-process (TDD; pure metrics/rules + the cli flag). Task 5's real A/B is CI-only (org sandbox blocks Bash/reads). Order is strict 1→2→3→4→5. Task 5 Step 8 is USER/CI-verified — the experiment's actual numbers, and the B1c decision, come from that run.
