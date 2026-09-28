# Track A — Measurement & Honest Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ccgate audit --report` view that reports the I7 ledger honestly and per-cause, backed by a committed synthetic-fixture reproduction gate, and get the test suite green first.

**Architecture:** `--report` is a presentation flag on the existing `audit` command; it reuses `run_audit`'s ground-truth summation and adds a small pure module (`src/ccgate/report.py`) holding bound assertions, the I7 ledger computation, and rendering. `ledger.py` is left untouched (Track B's concern). The reproduction gate is a committed synthetic fixture corpus with counts known by construction.

**Tech Stack:** Python 3.14, pytest, stdlib only (no `claude-agent-sdk` — that is Track B).

**Spec:** `docs/superpowers/specs/2026-09-28-track-a-measurement-design.md`

## Global Constraints

- **`ledger.py` is not modified.** Its `net`/G6/`notice_bytes` model is Track B's concern (spec §2, Approach A).
- **No fabricated token numbers.** D4 `shape` findings are listed with **no** token estimates; A1 is `unmeasurable`, A3 is `0` (spec §3).
- **Three ledger categories reported separately** — never collapsed into one number (spec §3; G24).
- **Every by-construction bound is asserted before any figure prints, and each assertion must be witnessed failing in a test** (spec §6; parent §12.9).
- **No git hook bypass** — never `--no-verify`/`-n`/`HUSKY=0`/`core.hooksPath` on any commit/push (user CLAUDE.md). A failing pre-commit hook is fixed, not bypassed.
- **No hardcoded absolute paths** — use `Path(__file__)`, `Path.home()`, `tmp_path` (user CLAUDE.md).
- **The agent cannot run the full pytest suite or subprocess/entry-point tests to green** (`DuplicateHandle`, ~20+ min, unreliable). Full-suite verification (Task 1) and the Layer-2 cross-check (Task 8) are **user-terminal steps**. Individual in-process tests (Tasks 3–7) DO run via the agent (spec §6, parent §12).

## Review Focus

- **Empty corpus under `--report`** (no transcripts found): must exit 0 with a message, print no ledger, never crash. → Task 7 test.
- **Zero-request / zero-miss summary**: `hit_ratio` and `cache_read_rate` denominators are 0; `assert_bounds` must accept it (rates default in-range, `0 ≤ 0`). → Task 4 test.
- **`--report --json`**: the `ledger` key must be present and well-formed, and `assert_bounds` must still run before emission. → Task 7 test.
- **`run_shape` finds no D4 issues**: the D4 section must render "none found", not an empty crash or a fabricated zero-savings claim. → Task 6 test.
- **Corrupt summary where `main + subagent misses ≠ total`** (the shape of a summation bug): `assert_bounds` must fire. → Task 4 guard test.

---

### Task 1: Subprocess-test fix (fd-0 hygiene) — must be first

Root cause (handoff): under full pytest collection, a cluster of hook tests tips fd 0's
handle invalid; later subprocess helpers that **inherit** fd 0 fail at `DuplicateHandle`.
The fix gives each such helper an explicit closed stdin. Only the 5 helpers that spawn
**without** `input=` are affected (an `input=` already supplies a PIPE stdin).

**Files:**
- Modify: `tests/test_baseline.py:12` (helper `_run`)
- Modify: `tests/test_digest.py:17` (helper `_run`)
- Modify: `tests/test_miss_audit_g4g5.py:19` (helper `_run`)
- Modify: `tests/test_otel_reader.py:22` (helper `_run`) and the three `subprocess.Popen` serve calls (`:179`, `:204`, `:231`)
- Modify: `tests/test_since_filter.py:131` (the `subprocess.run` in the `_run` helper)

**Interfaces:**
- Produces: a green full suite (27 → 0). No code symbols; this is test-harness hygiene.

- [ ] **Step 1: Add `stdin=subprocess.DEVNULL` to `tests/test_baseline.py` `_run`**

```python
    result = subprocess.run(
        [sys.executable, "-m", "ccgate.scripts.baseline", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,   # fd-0 hygiene: do not inherit pytest's contaminated stdin
    )
```

- [ ] **Step 2: Add the same `stdin=subprocess.DEVNULL` to `tests/test_digest.py:17`, `tests/test_miss_audit_g4g5.py:19`, and `tests/test_since_filter.py:131`**

Add `stdin=subprocess.DEVNULL,` as an argument to each `subprocess.run(...)` call in those helpers. Do not change any other argument.

- [ ] **Step 3: Add `stdin=subprocess.DEVNULL` to all four spawns in `tests/test_otel_reader.py`**

The `_run` helper at `:22` (`subprocess.run`) and the three `subprocess.Popen(... "serve" ...)` calls at `:179`, `:204`, `:231` all inherit fd 0. Add `stdin=subprocess.DEVNULL,` to each.

- [ ] **Step 4: Spot-check one affected test in isolation (agent-runnable)**

Run: `python -m pytest tests/test_baseline.py -q`
Expected: PASS in isolation (confirms the edit is syntactically valid and the DEVNULL arg is accepted). This does **not** prove the full-suite fix — Step 5 does.

- [ ] **Step 5: USER-TERMINAL EXIT GATE — full suite green, evidence captured**

Ask the user to run, in their terminal (not the agent):
```
python -m pytest -q
```
Expected: `27 failed` → `0 failed` (all pass), ~6 min. **Paste the actual final summary line** (e.g. `NNN passed in MMs`) into this plan's evidence log below. **No task after this one starts until that green output is recorded.**

Evidence log (fill in): `__________________________________________________`

- [ ] **Step 6: Commit**

```bash
git add tests/test_baseline.py tests/test_digest.py tests/test_miss_audit_g4g5.py tests/test_otel_reader.py tests/test_since_filter.py
git commit -m "test: give subprocess helpers explicit DEVNULL stdin (fd-0 hygiene)"
```

---

### Task 2: Remove throwaway `ci-probe.yml` from `main` (independent)

Spike hygiene (handoff). Independent of every other task; can run any time. This targets
`main`, not the `plan2-track-a` branch — do it as its own PR.

**Files:**
- Delete: `.github/workflows/ci-probe.yml` (on `main`)

**Interfaces:** none.

- [ ] **Step 1: Branch from up-to-date `main`**

```bash
git fetch origin
git checkout -b chore/remove-ci-probe origin/main
```

- [ ] **Step 2: Remove the workflow**

```bash
git rm .github/workflows/ci-probe.yml
git commit -m "chore: remove throwaway ci-probe workflow (spike done)"
```

- [ ] **Step 3: Push and open a PR (outward action — confirm with user first)**

```bash
git push -u origin chore/remove-ci-probe
gh pr create --base main --title "chore: remove throwaway ci-probe workflow" --body "Spike complete (Track-B-in-CI verified). Removing the dispatch-only probe."
```
Wait for the `verify` check to pass, then merge. **Do not bypass branch protection or hooks.**

---

### Task 3: Committed fixture corpus + Layer-1 reproduction gate

Build synthetic, PII-free transcripts whose miss/hit/token counts are known by
construction, covering both a main-thread and a subagent transcript, then assert
`run_audit` reproduces them exactly. This is the spec §1 Layer-1 gate — CI-runnable and
machine-independent. Agent-runnable (in-process).

**Files:**
- Create: `tests/fixtures/track_a_report/main.jsonl`
- Create: `tests/fixtures/track_a_report/subagents/agent-1.jsonl`
- Create: `tests/test_report_fixture.py`

**Interfaces:**
- Consumes: `ccgate.scripts.miss_audit.run_audit(paths: list[Path], config: dict) -> dict`; `ccgate.transcript` (fixture is read via `run_audit`).
- Produces: a fixture whose exact figures are: `total_requests=5`, `total_misses=2`, `expected_rebuilds=0`, `hit_ratio=0.6`, `cache_read_rate=0.25`, `by_origin={main:{requests:3,misses:1}, subagent:{requests:2,misses:1}}`, `tokens={grand_total_input:404000, cache_read:100000, cache_creation:300000, input:4000, output:190}`.

- [ ] **Step 1: Write `tests/fixtures/track_a_report/main.jsonl`**

Three assistant requests: HIT (creates 100000 cache), HIT (full cache read), MISS
(cache read drops to 0 → 100000 re-processed; same model, 30s gaps < 300s TTL → unclassified).

```json
{"type":"assistant","timestamp":"2026-09-27T10:00:00.000Z","message":{"model":"claude-opus-4-8","usage":{"input_tokens":1000,"cache_read_input_tokens":0,"cache_creation_input_tokens":100000,"output_tokens":50}}}
{"type":"assistant","timestamp":"2026-09-27T10:00:30.000Z","message":{"model":"claude-opus-4-8","usage":{"input_tokens":1000,"cache_read_input_tokens":100000,"cache_creation_input_tokens":0,"output_tokens":50}}}
{"type":"assistant","timestamp":"2026-09-27T10:01:00.000Z","message":{"model":"claude-opus-4-8","usage":{"input_tokens":1000,"cache_read_input_tokens":0,"cache_creation_input_tokens":100000,"output_tokens":50}}}
```

- [ ] **Step 2: Write `tests/fixtures/track_a_report/subagents/agent-1.jsonl`**

Two assistant requests: HIT (creates 50000 cache), MISS (cache read 0 → 50000
re-processed). Lives under a `subagents/` dir so `is_subagent_transcript` buckets it.

```json
{"type":"assistant","timestamp":"2026-09-27T10:00:00.000Z","message":{"model":"claude-opus-4-8","usage":{"input_tokens":500,"cache_read_input_tokens":0,"cache_creation_input_tokens":50000,"output_tokens":20}}}
{"type":"assistant","timestamp":"2026-09-27T10:00:20.000Z","message":{"model":"claude-opus-4-8","usage":{"input_tokens":500,"cache_read_input_tokens":0,"cache_creation_input_tokens":50000,"output_tokens":20}}}
```

- [ ] **Step 3: Write the failing reproduction test**

```python
from pathlib import Path

from ccgate.scripts.miss_audit import run_audit

FIXTURES = Path(__file__).parent / "fixtures" / "track_a_report"


def _paths():
    return [FIXTURES / "main.jsonl", FIXTURES / "subagents" / "agent-1.jsonl"]


def test_fixture_reproduces_known_counts():
    report = run_audit(_paths(), {})
    s = report["summary"]
    assert s["total_requests"] == 5
    assert s["total_misses"] == 2
    assert s["expected_rebuilds"] == 0
    assert s["hit_ratio"] == 0.6
    assert s["cache_read_rate"] == 0.25
    assert s["by_origin"] == {
        "main": {"requests": 3, "misses": 1},
        "subagent": {"requests": 2, "misses": 1},
    }
    assert s["tokens"] == {
        "grand_total_input": 404000,
        "cache_read": 100000,
        "cache_creation": 300000,
        "input": 4000,
        "output": 190,
    }
```

- [ ] **Step 4: Run to verify it passes (fixture designed to match)**

Run: `python -m pytest tests/test_report_fixture.py -q`
Expected: PASS. If any figure mismatches, the fixture math is wrong — fix the fixture, not the test, until it matches the by-construction counts above.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/track_a_report tests/test_report_fixture.py
git commit -m "test: add Track A fixture corpus with known-by-construction counts (Layer-1 gate)"
```

---

### Task 4: `report.assert_bounds` — the by-construction guard

Pure function that raises on any impossible figure, before anything prints. This is the
guard the original negative-total bug lacked. Agent-runnable.

**Files:**
- Create: `src/ccgate/report.py`
- Create: `tests/test_report.py`

**Interfaces:**
- Consumes: a `summary` dict shaped like `run_audit(...)["summary"]`.
- Produces: `assert_bounds(summary: dict) -> None` (raises `ValueError` on violation).

- [ ] **Step 1: Write failing tests (valid summary passes; each violation fires)**

```python
import pytest

from ccgate.report import assert_bounds

VALID = {
    "total_requests": 5, "total_misses": 2, "hit_ratio": 0.6, "cache_read_rate": 0.25,
    "by_origin": {"main": {"requests": 3, "misses": 1},
                  "subagent": {"requests": 2, "misses": 1}},
    "tokens": {"grand_total_input": 404000, "cache_read": 100000,
               "cache_creation": 300000, "input": 4000, "output": 190},
}


def test_valid_summary_passes():
    assert_bounds(VALID)  # must not raise


def test_zero_requests_passes():
    empty = {"total_requests": 0, "total_misses": 0, "hit_ratio": 1.0,
             "cache_read_rate": 0.0,
             "by_origin": {"main": {"requests": 0, "misses": 0},
                           "subagent": {"requests": 0, "misses": 0}},
             "tokens": {"grand_total_input": 0, "cache_read": 0,
                        "cache_creation": 0, "input": 0, "output": 0}}
    assert_bounds(empty)  # must not raise


def test_misses_exceed_requests_fires():
    bad = {**VALID, "total_misses": 6}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_origin_misses_mismatch_fires():
    bad = {**VALID, "by_origin": {"main": {"requests": 3, "misses": 1},
                                  "subagent": {"requests": 2, "misses": 0}}}
    with pytest.raises(ValueError):
        assert_bounds(bad)  # 1 + 0 != total_misses 2


def test_negative_token_fires():
    bad = {**VALID, "tokens": {**VALID["tokens"], "cache_read": -1}}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_cache_exceeds_grand_total_fires():
    bad = {**VALID, "tokens": {**VALID["tokens"], "grand_total_input": 100}}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_rate_out_of_range_fires():
    bad = {**VALID, "cache_read_rate": 1.5}
    with pytest.raises(ValueError):
        assert_bounds(bad)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_report.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccgate.report'`.

- [ ] **Step 3: Implement `assert_bounds` in `src/ccgate/report.py`**

```python
"""report.py — Track A honest reporting: bound guards, I7 ledger, rendering.

Pure functions. ledger.py (Track B's net model) is intentionally untouched.
"""
from __future__ import annotations


def assert_bounds(summary: dict) -> None:
    """Raise ValueError if any by-construction bound is violated (spec §6, §12.9).

    Guards the shape of the original defect: a negative/impossible figure that no
    bound was watching. Called before any figure is printed.
    """
    reqs = summary["total_requests"]
    misses = summary["total_misses"]
    if reqs < 0 or misses < 0:
        raise ValueError(f"negative count: requests={reqs} misses={misses}")
    if misses > reqs:
        raise ValueError(f"misses {misses} > requests {reqs}")

    by = summary["by_origin"]
    origin_misses = by["main"]["misses"] + by["subagent"]["misses"]
    if origin_misses != misses:
        raise ValueError(f"origin misses {origin_misses} != total misses {misses}")

    for name in ("hit_ratio", "cache_read_rate"):
        v = summary[name]
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"{name}={v} not in [0,1]")

    t = summary["tokens"]
    for k, v in t.items():
        if v < 0:
            raise ValueError(f"token quantity {k}={v} < 0")
    if t["cache_read"] + t["cache_creation"] > t["grand_total_input"]:
        raise ValueError(
            f"cache_read+cache_creation ({t['cache_read'] + t['cache_creation']}) "
            f"> grand_total_input ({t['grand_total_input']})"
        )
```

- [ ] **Step 4: Run to verify all pass**

Run: `python -m pytest tests/test_report.py -q`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/report.py tests/test_report.py
git commit -m "feat(report): add assert_bounds by-construction guard"
```

---

### Task 5: `report.compute_ledger` — honest, per-cause I7 ledger

**Files:**
- Modify: `src/ccgate/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: `shape_findings: list[dict]` from `ccgate.scripts.shape.run_shape(...)` (finding dicts keyed by `"check"`).
- Produces: `compute_ledger(shape_findings: list[dict]) -> dict` with keys `tokens_prevented` (sub-keys `a1`, `a3`, `d4`), `tokens_avoided`, `tokens_measured`, `tokens_injected`, `net`. `_D4_CHECKS: frozenset[str]`.

- [ ] **Step 1: Write failing tests**

```python
from ccgate.report import compute_ledger, _D4_CHECKS


def test_ledger_zeros_are_honest():
    led = compute_ledger([])
    assert led["tokens_avoided"] == 0
    assert led["tokens_measured"] == 0
    assert led["tokens_injected"] == 0
    assert led["net"] == 0


def test_ledger_prevented_is_per_cause():
    led = compute_ledger([])
    prevented = led["tokens_prevented"]
    assert prevented["a1"]["status"] == "unmeasurable"
    assert prevented["a3"]["value"] == 0
    assert prevented["d4"]["status"] == "available_unapplied"
    assert prevented["d4"]["findings"] == []


def test_ledger_d4_filters_shape_findings():
    findings = [
        {"check": "claudeMdExcludes", "severity": "warning"},
        {"check": "denyReads", "severity": "warning"},
        {"check": "toolDeferralSummary", "severity": "info"},  # not D4 → excluded
    ]
    led = compute_ledger(findings)
    d4 = led["tokens_prevented"]["d4"]["findings"]
    assert {f["check"] for f in d4} == {"claudeMdExcludes", "denyReads"}
    assert all(c in _D4_CHECKS for c in {"claudeMdExcludes", "denyReads", "claudeMdLines", "skillListing"})


def test_ledger_prevented_has_no_token_estimate():
    """D4 findings carry no fabricated number (spec §3)."""
    led = compute_ledger([{"check": "claudeMdExcludes", "severity": "warning"}])
    d4 = led["tokens_prevented"]["d4"]
    assert "value" in d4 and d4["value"] is None
    for f in d4["findings"]:
        assert "estimated_tokens" not in f
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_report.py -k ledger -q`
Expected: FAIL with `ImportError: cannot import name 'compute_ledger'`.

- [ ] **Step 3: Implement in `src/ccgate/report.py`**

```python
# D4 / ELIMINATE-headroom checks that run_shape actually emits today. bashOutputMaxChars
# (_check_output_caps) is a Phase-0 stub returning [], so it is not listed until it emits.
_D4_CHECKS = frozenset({"claudeMdLines", "claudeMdExcludes", "skillListing", "denyReads"})


def compute_ledger(shape_findings: list[dict]) -> dict:
    """Honest, per-cause I7 ledger (spec §3). No fabricated numbers.

    A1: unmeasurable (pin pre-applied). A3: 0 (driver absent). D4: shape findings,
    available-but-unapplied, listed WITHOUT token estimates (counterfactual). Track A
    denies/measures/injects nothing, so those categories are 0.
    """
    d4_findings = [f for f in shape_findings if f.get("check") in _D4_CHECKS]
    return {
        "tokens_prevented": {
            "a1": {"value": None, "status": "unmeasurable",
                   "reason": "1h pin already applied before baseline; no clean 'before' exists"},
            "a3": {"value": 0, "status": "not_applicable",
                   "reason": "driver absent — 0 D1.tools_changed in corpus"},
            "d4": {"value": None, "status": "available_unapplied",
                   "reason": "shape findings, not yet applied; no estimate (counterfactual)",
                   "findings": d4_findings},
        },
        "tokens_avoided": 0,   # Track A denies nothing
        "tokens_measured": 0,  # Track A measures no server-side compaction delta
        "tokens_injected": 0,  # interactive ccgate injects nothing (hooks blocked)
        "net": 0,              # reporter, not actor (I7)
    }
```

- [ ] **Step 4: Run to verify all pass**

Run: `python -m pytest tests/test_report.py -k ledger -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/report.py tests/test_report.py
git commit -m "feat(report): honest per-cause I7 ledger (compute_ledger)"
```

---

### Task 6: `report.render_report` — framing + facts

**Files:**
- Modify: `src/ccgate/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: `summary` dict (as Task 4) and `ledger` dict (as Task 5).
- Produces: `render_report(summary: dict, ledger: dict) -> str`.

- [ ] **Step 1: Write failing tests**

```python
from ccgate.report import render_report, compute_ledger

SUMMARY = {
    "total_requests": 5, "total_misses": 2, "hit_ratio": 0.6, "cache_read_rate": 0.25,
    "by_origin": {"main": {"requests": 3, "misses": 1},
                  "subagent": {"requests": 2, "misses": 1}},
    "tokens": {"grand_total_input": 404000, "cache_read": 100000,
               "cache_creation": 300000, "input": 4000, "output": 190},
}


def test_render_states_prevention_model_accurately():
    out = render_report(SUMMARY, compute_ledger([]))
    assert "ELIMINATE remains available" in out
    assert "PREVENT and RECOVER" in out
    assert "ccgate run" in out


def test_render_reports_zeros_and_facts():
    out = render_report(SUMMARY, compute_ledger([]))
    assert "avoided" in out and "0" in out
    assert "2" in out          # total misses
    assert "25.0%" in out      # cache-read rate
    assert "ccgate shape" in out   # pointer to unapplied config
    assert "ccgate audit" in out   # pointer to cause table


def test_render_d4_none_found():
    out = render_report(SUMMARY, compute_ledger([]))
    assert "none found" in out.lower()


def test_render_d4_lists_findings():
    led = compute_ledger([{"check": "claudeMdExcludes", "severity": "warning"}])
    out = render_report(SUMMARY, led)
    assert "claudeMdExcludes" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_report.py -k render -q`
Expected: FAIL with `ImportError: cannot import name 'render_report'`.

- [ ] **Step 3: Implement in `src/ccgate/report.py`**

```python
def render_report(summary: dict, ledger: dict) -> str:
    """Human-readable Track A report: honest ledger + accurate prevention framing.

    Does not overstate live ELIMINATE: A1 is applied, A3 absent, D4 unapplied (spec §4).
    """
    s, by, t = summary, summary["by_origin"], summary["tokens"]
    prevented = ledger["tokens_prevented"]
    lines: list[str] = []

    lines.append("\nTRACK A — MEASUREMENT REPORT")
    lines.append("=" * 60)
    lines.append(
        "ELIMINATE remains available to interactive sessions (config savings the "
        "org policy cannot reach). On this machine A1 (1h pin) is applied; A3's "
        "driver is absent; the D4 items are unapplied headroom — run `ccgate shape` "
        "to see what is not yet applied. PREVENT and RECOVER move to the owned loop "
        "(`ccgate run`) and apply only to Track B work."
    )

    lines.append("\nI7 ledger (categories reported separately):")
    lines.append(f"  tokens_prevented / A1: unmeasurable ({prevented['a1']['reason']})")
    lines.append(f"  tokens_prevented / A3: 0 ({prevented['a3']['reason']})")
    d4 = prevented["d4"]["findings"]
    if d4:
        lines.append("  tokens_prevented / D4: available, unapplied (no estimate) —")
        for f in d4:
            lines.append(f"      - {f['check']} [{f.get('severity', '?')}]")
    else:
        lines.append("  tokens_prevented / D4: none found")
    lines.append(f"  tokens_avoided:  {ledger['tokens_avoided']}  (Track A denies nothing)")
    lines.append(f"  tokens_measured: {ledger['tokens_measured']}  (no server-side delta)")
    lines.append(f"  tokens_injected: {ledger['tokens_injected']}  (interactive injects nothing)")
    lines.append(f"  net:             {ledger['net']}  (reporter, not actor — I7)")

    lines.append("\nGround-truth facts (this corpus):")
    lines.append(f"  total misses:    {s['total_misses']}  "
                 f"(main {by['main']['misses']} / subagent {by['subagent']['misses']})")
    lines.append(f"  cache-read rate: {s['cache_read_rate'] * 100:.1f}%")
    lines.append(f"  hit ratio:       {s['hit_ratio'] * 100:.1f}%  "
                 f"({s['total_requests'] - s['total_misses']}/{s['total_requests']})")
    lines.append("\nRun `ccgate audit` (no --report) for the full cause-attribution table.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify all pass**

Run: `python -m pytest tests/test_report.py -k render -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/report.py tests/test_report.py
git commit -m "feat(report): render honest Track A report with accurate framing"
```

---

### Task 7: Wire `--report` into `ccgate audit`

**Files:**
- Modify: `src/ccgate/scripts/miss_audit.py:168-237` (the `main` function / argparse)
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: `report.assert_bounds`, `report.compute_ledger`, `report.render_report`; `ccgate.scripts.shape.run_shape`.
- Produces: `ccgate audit --report` (human) and `ccgate audit --report --json` (adds `ledger` key to the report dict). `--report` composes with `--session` / `--since` / `paths`.

- [ ] **Step 1: Write failing tests (call `main` in-process; capture stdout)**

```python
import json as _json
from pathlib import Path

from ccgate.scripts import miss_audit

FIX = Path(__file__).parent / "fixtures" / "track_a_report"
PATHS = [str(FIX / "main.jsonl"), str(FIX / "subagents" / "agent-1.jsonl")]


def test_report_flag_human_output(capsys):
    miss_audit.main(["--report", *PATHS])
    out = capsys.readouterr().out
    assert "TRACK A — MEASUREMENT REPORT" in out
    assert "PREVENT and RECOVER" in out
    assert "cache-read rate: 25.0%" in out


def test_report_flag_json_has_ledger(capsys):
    miss_audit.main(["--report", "--json", *PATHS])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["ledger"]["tokens_avoided"] == 0
    assert payload["ledger"]["tokens_prevented"]["a1"]["status"] == "unmeasurable"
    assert payload["summary"]["total_misses"] == 2


def test_report_flag_empty_corpus_exits_zero(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(miss_audit, "find_transcripts", lambda **kw: [], raising=False)
    import pytest
    with pytest.raises(SystemExit) as ei:
        miss_audit.main(["--report", "--since", "1d"])
    assert ei.value.code == 0
```

Note: `test_report_flag_empty_corpus_exits_zero` patches the module-level
`find_transcripts` name; import it into `miss_audit` at top level (see Step 3) so the
patch target exists.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_report.py -k report_flag -q`
Expected: FAIL (`--report` not a recognized argument / `AttributeError`).

- [ ] **Step 3: Add the `--report` flag and branch in `miss_audit.main`**

Add the argument alongside the existing ones:

```python
    parser.add_argument("--report", action="store_true", dest="report_mode",
                        help="Honest I7 ledger + prevention-model framing (Track A)")
```

After `report = run_audit(paths, config)` and before the existing `if args.emit_json:` block, insert:

```python
    if args.report_mode:
        from ccgate import report as report_view
        from ccgate.scripts.shape import run_shape
        report_view.assert_bounds(report["summary"])   # never print an impossible figure
        ledger = report_view.compute_ledger(run_shape())
        if args.emit_json:
            report["ledger"] = ledger
            print(json.dumps(report, indent=2))
        else:
            print(report_view.render_report(report["summary"], ledger))
        return
```

To make the empty-corpus test's patch target exist, add near the top of `miss_audit.py`:

```python
from ccgate.transcript import find_transcripts
```

and change the two lazy `from ccgate.transcript import find_transcripts` calls inside
`main` to use the module-level import (delete the inner imports).

- [ ] **Step 4: Run to verify all pass**

Run: `python -m pytest tests/test_report.py -q`
Expected: PASS (all report tests, including Tasks 4–6).

- [ ] **Step 5: Verify the default `audit` path is unchanged (agent-runnable)**

Run: `python -m pytest tests/test_miss_audit_g4g5.py -q`
Expected: PASS — but note this file spawns subprocesses; if it hits `DuplicateHandle`
under the agent, that is the known harness limitation (Task 1 fixed the suite for the
user's terminal), not a regression. Confirm the non-subprocess tests in `tests/test_report.py` pass.

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/scripts/miss_audit.py tests/test_report.py
git commit -m "feat(audit): add --report view (honest I7 ledger + framing)"
```

---

### Task 8: One-time Layer-2 cross-check (user terminal)

Confirm the fixture-backed summation reproduces the real baseline of record over the
real corpus. Documented evidence, not a committed test (the real corpus cannot be
committed; spec §1 Layer 2).

**Files:** none (records evidence into this plan).

- [ ] **Step 1: USER-TERMINAL — run the report over the real corpus**

Ask the user to run, in their terminal:
```
ccgate audit --report --json > track-a-report.json
```
(or `python -m ccgate.dispatch audit --report --json > track-a-report.json`)

- [ ] **Step 2: Confirm the five baseline figures reproduce**

From `track-a-report.json`, check against `docs/baseline-2026-09-27-fixed.md`:

| Figure | Expected |
|---|---|
| `summary.total_misses` | 11,918 |
| `summary.by_origin.main.misses` | 2,865 |
| `summary.by_origin.subagent.misses` | 9,053 |
| `summary.cache_read_rate` | ≈ 0.964 |
| `summary.hit_ratio` | ≈ 0.897 |

(The corpus has grown since the baseline; small deviation from growth is expected here —
unlike the Layer-1 fixture, which must match exactly. Large divergence means the
summation path regressed.)

Evidence log (fill in): `total_misses=____  main=____  subagent=____  cache_read_rate=____  hit_ratio=____`

- [ ] **Step 3: Record the outcome in the plan and finish**

If the figures reproduce, Track A is complete. Proceed to
`superpowers:finishing-a-development-branch`.
