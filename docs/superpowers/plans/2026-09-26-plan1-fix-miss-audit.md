# Plan 1 — Fix miss_audit (make measurement trustworthy)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `miss_audit` produce only numbers that are defensible, by removing the broken cost/turns layer and reporting raw ground-truth token counts, split by main vs subagent origin.

**Architecture:** One conceptual bug — treating `usage.input_tokens` as the grand total when it is fresh-uncached-only — produced both an impossible negative total cost and a 66,661-turn estimate. Rather than patch two call sites, we (a) put the correct grand-total concept in one place in `transcript.py`, (b) drop the undefensible dollar and turns-estimate layer entirely and report raw tokens instead (revisit pricing when Track B can measure turns properly), (c) bucket subagent transcripts separately since their cold starts are structural, not waste, and (d) correct a fix-hint that sends users chasing a Claude Code upgrade that would change nothing.

**Tech Stack:** Python 3.11+, stdlib only, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-two-track-architecture-design.md` (§11 Plan 1; §9 acceptance discipline). Baseline evidence: `docs/baseline-2026-09-26.md`.

## Global Constraints

- Python 3.11+, **stdlib only**, no new dependencies (BUILD-SPEC I5).
- Never emit the PII identifiers enumerated in `.githooks/pre-commit` (the user's real name, email local-part, employer, and handles). That hook blocks them in staged content and in git identity — do not paste real paths/usernames into committed files; use repo-relative paths.
- Git identity is `Developer` / `fredman08@users.noreply.github.com`. End each commit message with the `Co-Authored-By:` line from your session's current attribution reminder (do not hardcode one from this plan).
- **§12.9 discipline (binds every task):** each defect's red state must be *observed* before its fix — run the test (or command) against the current code and see it fail/produce the impossible value first. A regression test that never failed against the old code proves nothing. Any figure with a by-construction bound (token counts ≥ 0, `cache_read_rate` ∈ [0,1], `hit_ratio` ∈ [0,1]) must be asserted against that bound.
- **Reviewer instruction (subagent-driven execution):** for each task, the reviewer must confirm the observed failure **message** matches what the plan's "Expected: FAIL with …" predicts — not merely that *something* failed. An `ImportError` where the plan predicted a `KeyError` (or a missing-key `AssertionError`) means the test is not exercising the defect it claims to. The self-attested red→green loop — same agent writes the test, runs it, reports it went red — is exactly what let the negative total through; the reviewer seeing the actual red output is the structural fix.
- Token figures are exact integers from `usage` blocks (I4 ground truth). No `chars // 4` estimates in the reported numbers.

## Review Focus

- **Zero cache activity** (`cache_read + cache_creation == 0`): `cache_read_rate` must not `ZeroDivisionError` — pinned in Task 2's `test_empty_paths_all_zero_no_crash` (empty corpus → denom 0 → rate 0.0).
- **Corpus with zero subagent OR zero main transcripts**: `by_origin` must still contain both buckets with zeroed counts, no `KeyError` — pinned in Task 3.
- **Empty transcript list / all-empty transcripts**: summary is all zeros, `hit_ratio` defaults to 1.0, no crash — pinned in Task 2.
- **Genuinely unknown miss cause** (neither model-switch nor ttl-gap): still buckets to `D1.unclassified` with the corrected hint, not dropped — pinned in Task 4.
- **`applied_at` timestamp**: still a valid `YYYY-MM-DDTHH:MM:SSZ` string after the deprecation fix — pinned in Task 5.

## Scope note (deviation flagged for review)

The earlier §11 draft folded dead-hook removal (A1 `session_start` assertion, A4 `PreCompact`/`SessionStart` wiring) into Plan 1. **This plan defers those** to Plan 2. Removing the A4 hooks now would break the 82 passing hook tests and orphan `_extract_from_transcript` before Track B exists to receive it — churn with no benefit to measurement trustworthiness. Plan 1 keeps only the trivial, hook-independent `datetime.utcnow()` fix (Task 5). If you want the A1 assertion hook gone in Plan 1 regardless, say so and I'll add a task.

## File Structure

- `src/ccgate/transcript.py` — add `grand_total_input(u: Usage) -> int`, the canonical "total input tokens processed" concept. One home; no other module recomputes it.
- `src/ccgate/scripts/miss_audit.py` — remove the cost accumulation, `avoidable_usd`/`total_usd`, `_turns_remaining_est` and the `assumptions` block; add raw token totals + `cache_read_rate`; add main/subagent bucketing.
- `src/ccgate/taxonomy.py` — correct the `D1_UNCLASSIFIED` fix hint.
- `src/ccgate/scripts/shape.py` — replace deprecated `datetime.utcnow()`.
- `tests/test_miss_audit.py` — rewrite the tests that encoded the bug (they passed against broken code); add the new-behavior tests.
- `tests/test_transcript.py` — add the `grand_total_input` test.

---

### Task 1: `grand_total_input` helper in `transcript.py`

**Files:**
- Modify: `src/ccgate/transcript.py` (add function after the `Usage` dataclass, ~line 45)
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces: `grand_total_input(u: Usage) -> int` returning `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transcript.py
from ccgate.transcript import Usage, grand_total_input


def test_grand_total_input_sums_fresh_and_cached():
    u = Usage(input_tokens=100, cache_read_input_tokens=9000,
              cache_creation_input_tokens=500, output_tokens=42)
    assert grand_total_input(u) == 9600


def test_grand_total_input_differs_from_input_tokens_alone():
    # The whole bug class: input_tokens is fresh-only, not the grand total.
    u = Usage(input_tokens=3, cache_read_input_tokens=15000,
              cache_creation_input_tokens=0)
    assert grand_total_input(u) == 15003
    assert grand_total_input(u) != u.input_tokens
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_transcript.py::test_grand_total_input_sums_fresh_and_cached -v`
Expected: FAIL with `ImportError: cannot import name 'grand_total_input'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ccgate/transcript.py, immediately after the Usage dataclass
def grand_total_input(u: Usage) -> int:
    """Total input tokens processed for a request.

    input_tokens in a usage block is the fresh, uncached count only; the grand
    total is fresh + cache_read + cache_creation. Defining it once here keeps the
    "input_tokens is the total" misconception from reappearing at a call site.
    """
    return (
        u.input_tokens
        + u.cache_read_input_tokens
        + u.cache_creation_input_tokens
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_transcript.py -k grand_total_input -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/transcript.py tests/test_transcript.py
git commit -m "feat(transcript): add grand_total_input helper (canonical total-input concept)"
```

---

### Task 2: Drop dollar/turns layer; report raw tokens + cache_read_rate (defects 1 & 2)

**Files:**
- Modify: `src/ccgate/scripts/miss_audit.py:42-67` (remove `_turns_remaining_est`), `:83` (remove turns init), `:97-134` (remove cost accumulation, keep classification; add token totals), `:136-169` (rewrite summary), `:172-195` (rewrite render)
- Modify: `schema/ccgate.report.schema.json` — **required**: this schema currently requires `assumptions`, `avoidable_usd`, `total_usd`, `cost_usd`, `turns_remaining_est`; the new report omits all of them, so the schema must change in lockstep or `schema_check.py` fails.
- Modify: `tests/schema_check.py` — same required-key lists (verified consumers via grep 2026-09-26).
- Test: `tests/test_miss_audit.py` (remove `TestTurnsRemainingEst`, replace `test_report_schema_fields_present` and `test_d2_compaction_not_in_avoidable_total`)

**Downstream check already done (2026-09-26):** `grep -rn "cost_usd\|avoidable_usd\|total_usd\|turns_remaining\|assumptions"` across `src/ tests/ schema/` found exactly two consumers of these keys — `schema/ccgate.report.schema.json` and `tests/schema_check.py` (both updated in this task). `statusline.py`'s `total_cost_usd` is a *different* key (the statusline payload's cost) and is unaffected.

**Interfaces:**
- Consumes: `grand_total_input` (Task 1).
- Produces: `run_audit(paths, config) -> dict` whose `summary` has keys `total_requests, total_misses, expected_rebuilds, hit_ratio, cache_read_rate, tokens` and **no** `total_usd`/`avoidable_usd`; report has **no** `assumptions` block. `tokens` is `{"grand_total_input", "cache_read", "cache_creation", "input", "output"}`. Each `misses` entry is `{"cause", "count", "recached_tokens", "fix"}` (no `cost_usd`).

- [ ] **Step 1: Observe the red state in the current code**

Run: `python -m pytest tests/test_miss_audit.py::TestRunAudit::test_report_schema_fields_present -v`
Expected: PASS against current code — this is the point. The current test *asserts* `turns_remaining_est` exists, encoding the bug. Record that it passes, then rewrite it below so it fails against current code.

- [ ] **Step 2: Write the new failing tests**

Edit `tests/test_miss_audit.py`: change the import on line 5 to `from ccgate.scripts.miss_audit import attribute_miss, run_audit` (drop `_turns_remaining_est`); delete the entire `TestTurnsRemainingEst` class; replace `TestRunAudit::test_report_schema_fields_present`; and replace `TestRunAudit::test_d2_compaction_not_in_avoidable_total` with the count-based version below (its dollar-based body no longer applies, but the "D2 is not avoidable" intent is preserved). Add:

```python
# tests/test_miss_audit.py — replaces test_report_schema_fields_present
def test_report_has_raw_tokens_and_no_dollars_or_turns(self):
    report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
    s = report["summary"]
    # dollar/turns layer is gone
    assert "total_usd" not in s
    assert "avoidable_usd" not in s
    assert "assumptions" not in report
    # raw tokens present and non-negative (by-construction bound)
    assert set(s["tokens"]) == {
        "grand_total_input", "cache_read", "cache_creation", "input", "output"
    }
    assert all(v >= 0 for v in s["tokens"].values())
    assert 0.0 <= s["cache_read_rate"] <= 1.0
    assert 0.0 <= s["hit_ratio"] <= 1.0
    for m in report["misses"]:
        assert "cost_usd" not in m


def test_empty_paths_all_zero_no_crash(self):
    # Also the zero-cache guard: with no requests, cache_read + cache_creation == 0,
    # so cache_read_rate must be 0.0, not a ZeroDivisionError.
    report = run_audit([], DEFAULTS)
    s = report["summary"]
    assert s["total_requests"] == 0
    assert s["hit_ratio"] == 1.0
    assert s["tokens"]["grand_total_input"] == 0
    assert s["cache_read_rate"] == 0.0


def test_d2_compaction_present_but_not_in_avoidable_d1(self):
    # Replaces the old dollar-based test: D2.compaction is detected and listed,
    # but is not one of the avoidable D1 causes.
    report = run_audit([FIXTURES / "d2_compaction.jsonl"], DEFAULTS)
    causes = {m["cause"] for m in report["misses"]}
    assert taxonomy.D2_COMPACTION in causes
    assert taxonomy.D2_COMPACTION not in taxonomy.D1_ALL
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/test_miss_audit.py -k "raw_tokens or empty_paths" -v`
Expected: FAIL — current `run_audit` still emits `total_usd`/`assumptions` and has no `tokens`/`cache_read_rate` keys.

- [ ] **Step 4: Implement**

In `src/ccgate/scripts/miss_audit.py`:

1. Delete the `_turns_remaining_est` function (lines 42-67).
2. Add the import at the top: `from ccgate.transcript import grand_total_input` (alongside the existing transcript imports).
3. In `run_audit`, delete `total_usd = 0.0`, `avoidable_usd = 0.0`, `cause_cost` and the `turns_est, turns_derivation = ...` line. Add a token accumulator before the loop:

```python
    tokens = {"grand_total_input": 0, "cache_read": 0,
              "cache_creation": 0, "input": 0, "output": 0}
```

4. Replace the per-request cost block (current lines 98-104) and remove the `spec = get_model_spec(...)` line if `spec` is now unused; accumulate tokens instead:

```python
        for req, cls in classified:
            u = req.usage
            tokens["grand_total_input"] += grand_total_input(u)
            tokens["cache_read"]        += u.cache_read_input_tokens
            tokens["cache_creation"]    += u.cache_creation_input_tokens
            tokens["input"]             += u.input_tokens
            tokens["output"]            += u.output_tokens
```

5. In the MISS branch, delete the `miss_cost`/`cause_cost`/`avoidable_usd` lines; keep `cause_counts`, `cause_tokens`, and `attribute_miss`. In the EXPECTED_REBUILD branch keep `cause_counts`/`cause_tokens` for D2.
6. Rewrite the misses list and summary/return:

```python
    denom = tokens["cache_read"] + tokens["cache_creation"]
    cache_read_rate = (tokens["cache_read"] / denom) if denom > 0 else 0.0

    misses_list = []
    for cause in sorted(cause_counts.keys()):
        misses_list.append({
            "cause":           cause,
            "count":           cause_counts[cause],
            "recached_tokens": cause_tokens[cause],
            "fix":             taxonomy.FIX_HINTS.get(cause, ""),
        })
    misses_list.sort(key=lambda m: (-m["count"], m["cause"]))

    return {
        "sessions": all_sessions,
        "summary": {
            "total_requests":    total_requests,
            "total_misses":      total_misses,
            "expected_rebuilds": total_rebuilds,
            "hit_ratio":         round(hit_ratio, 6),
            "cache_read_rate":   round(cache_read_rate, 6),
            "tokens":            tokens,
        },
        "misses": misses_list,
    }
```

7. Rewrite `_render_table` to drop the cost column and the spend footer:

```python
def _render_table(report: dict) -> str:
    lines = []
    s = report["summary"]
    session_count = len(report["sessions"])
    lines.append(f"\nMISS AUDIT — {session_count} session(s)\n")
    lines.append(f"  {'cause':<30} {'misses':>6}  {'re-cached':>10}  fix")
    lines.append("  " + "-" * 60)
    for m in report["misses"]:
        tokens = m["recached_tokens"]
        tok_str = f"{tokens/1e6:.1f}M" if tokens >= 1e6 else f"{tokens/1e3:.0f}K"
        lines.append(f"  {m['cause']:<30} {m['count']:>6}  {tok_str:>10}  {m['fix']}")
    lines.append("")
    t = s["tokens"]
    lines.append(
        f"  cache-read rate: {s['cache_read_rate']*100:.1f}%  "
        f"(read {t['cache_read']/1e6:.0f}M / created {t['cache_creation']/1e6:.0f}M)"
    )
    lines.append(f"  hit ratio: {s['hit_ratio']*100:.1f}%  "
                 f"({s['total_misses']} misses / {s['total_requests']} requests)")
    return "\n".join(lines)
```

Remove any now-unused imports (`get_model_spec` if unused elsewhere; check `--assert` path still uses what it needs).

8. Update `schema/ccgate.report.schema.json`: drop `"assumptions"` from the top-level `required`; in `summary`, change `required` to `["total_requests", "total_misses", "expected_rebuilds", "hit_ratio", "cache_read_rate", "tokens"]` and replace the `avoidable_usd`/`total_usd` properties with `"cache_read_rate": {"type": "number"}` and `"tokens": {"type": "object"}`; in `misses.items`, drop `"cost_usd"` from `required` and from `properties`; delete the entire `"assumptions"` property block.

9. Update `tests/schema_check.py` `_check_report`: drop `"assumptions"` from the top-level key loop; change the summary key loop to `("total_requests", "total_misses", "expected_rebuilds", "hit_ratio", "cache_read_rate", "tokens")`; delete the `assumptions` block (lines 29-32); change the miss-entry key loop to `("cause", "count", "recached_tokens", "fix")`.

- [ ] **Step 5: Run to verify pass, and confirm the whole file's tests + schema check**

Run: `python -m pytest tests/test_miss_audit.py -v && python tests/schema_check.py`
Expected: PASS — new tests green; `TestTurnsRemainingEst` gone; `TestClassifyRequests`/`TestAttributeMiss`/`test_model_switch_fixture`/`test_clean_session_has_no_avoidable_misses` still pass (count/cause based); `schema_check.py` prints `G9 PASS`.

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/scripts/miss_audit.py tests/test_miss_audit.py schema/ccgate.report.schema.json tests/schema_check.py
git commit -m "fix(miss_audit): drop broken cost/turns layer; report raw tokens + cache_read_rate

Resolves the negative-total and 66661-turn defects by removing the undefensible
dollar/turns-estimate layer (both rooted in treating input_tokens as the grand
total) and reporting ground-truth token counts instead. Cost returns in a later
plan with a validated pricing table and a defensible turns estimator."
```

---

### Task 3: Bucket main vs subagent transcripts (defect 3)

**Files:**
- Modify: `src/ccgate/scripts/miss_audit.py` (add `is_subagent_transcript`; split request/miss counts by origin into `summary["by_origin"]`)
- Modify: `schema/ccgate.report.schema.json` and `tests/schema_check.py` (add `by_origin` to the summary contract)
- Test: `tests/test_miss_audit.py`

**Interfaces:**
- Consumes: `run_audit` from Task 2.
- Produces: `is_subagent_transcript(path: Path) -> bool`; `summary["by_origin"] = {"main": {"requests": int, "misses": int}, "subagent": {"requests": int, "misses": int}}`. Both buckets always present.

**§12.9 convention verified against the live corpus (2026-09-26):** `"subagents" in Path(p).parts` matched the `agent-` basename heuristic **exactly** on all 1090 local transcripts (1007 subagent / 83 main, set-equal). Real subagent path shape: `.../<session-uuid>/subagents/agent-<hash>.jsonl`. The convention is a cross-checked contract, not a guess — but re-run this check if a future Claude Code version changes the layout.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_miss_audit.py
from ccgate.scripts.miss_audit import is_subagent_transcript


def test_is_subagent_transcript_detects_subagents_dir(tmp_path):
    main = tmp_path / "session.jsonl"
    sub = tmp_path / "subagents" / "agent-abc123.jsonl"
    sub.parent.mkdir(parents=True)
    assert is_subagent_transcript(sub) is True
    assert is_subagent_transcript(main) is False


class TestByOrigin:
    def test_by_origin_always_has_both_buckets(self):
        report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
        bo = report["summary"]["by_origin"]
        assert set(bo) == {"main", "subagent"}
        assert set(bo["main"]) == {"requests", "misses"}
        assert set(bo["subagent"]) == {"requests", "misses"}
        # clean_15req is a main-thread fixture: all requests in main bucket
        assert bo["subagent"]["requests"] == 0
        assert bo["main"]["requests"] == report["summary"]["total_requests"]

    def test_main_and_subagent_misses_sum_to_total(self):
        report = run_audit([FIXTURES / "d1_model_switch.jsonl"], DEFAULTS)
        bo = report["summary"]["by_origin"]
        assert bo["main"]["misses"] + bo["subagent"]["misses"] == \
            report["summary"]["total_misses"]

    def test_subagent_misses_land_in_subagent_bucket(self, tmp_path):
        # THE defect this task fixes: subagent cold-start misses were counted as
        # avoidable main-thread waste. Build a real transcript under a subagents/
        # dir with a genuine miss (turn 2 re-processes 5000 tokens) and run it
        # through run_audit — its miss must land in the subagent bucket, not main.
        import json
        sub = tmp_path / "sess-uuid" / "subagents" / "agent-deadbeef.jsonl"
        sub.parent.mkdir(parents=True)
        lines = [
            {"type": "assistant", "timestamp": "2026-09-26T10:00:00.000Z",
             "message": {"model": "claude-sonnet-5", "usage": {
                 "input_tokens": 5000, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 5000,
                 "cache_creation": {"ephemeral_1h_input_tokens": 5000}}}},
            {"type": "assistant", "timestamp": "2026-09-26T10:00:05.000Z",
             "message": {"model": "claude-sonnet-5", "usage": {
                 "input_tokens": 5000, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 5000,
                 "cache_creation": {"ephemeral_1h_input_tokens": 5000}}}},
        ]
        sub.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        report = run_audit([sub], DEFAULTS)
        bo = report["summary"]["by_origin"]
        assert report["summary"]["total_misses"] >= 1
        assert bo["subagent"]["misses"] == report["summary"]["total_misses"]
        assert bo["main"]["misses"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_miss_audit.py -k "subagent or ByOrigin" -v`
Expected: FAIL — `is_subagent_transcript` does not exist; `by_origin` not in summary.

- [ ] **Step 3: Implement**

```python
# src/ccgate/scripts/miss_audit.py (module level)
def is_subagent_transcript(path: Path) -> bool:
    """True if this JSONL is a subagent transcript (lives under a 'subagents' dir).

    Subagents get a fresh cache by design, so their first request is always a
    miss — isolation working, not waste. Bucket them separately.
    """
    return "subagents" in Path(path).parts
```

In `run_audit`, before the per-request loop for each `path`, compute `origin = "subagent" if is_subagent_transcript(path) else "main"`. Add a `by_origin` accumulator initialised with both buckets:

```python
    by_origin = {
        "main":     {"requests": 0, "misses": 0},
        "subagent": {"requests": 0, "misses": 0},
    }
```

In the loop, increment `by_origin[origin]["requests"] += 1` per request, and `by_origin[origin]["misses"] += 1` in the MISS branch. Add `"by_origin": by_origin` to the `summary` dict in the return.

Then extend the schema contract:
- `schema/ccgate.report.schema.json`: add `"by_origin"` to the `summary` `required` list, and add the property `"by_origin": {"type": "object"}`.
- `tests/schema_check.py`: add `"by_origin"` to the summary key loop.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_miss_audit.py -v && python tests/schema_check.py`
Expected: PASS (all, including Task 2's tests); `schema_check.py` prints `G9 PASS`.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/scripts/miss_audit.py tests/test_miss_audit.py schema/ccgate.report.schema.json tests/schema_check.py
git commit -m "feat(miss_audit): bucket main vs subagent transcripts

92.6% of the local corpus is subagent transcripts whose cold starts are
structural, not avoidable waste. Report requests/misses split by origin so the
main-thread signal is not drowned by subagent cold starts."
```

---

### Task 4: Honest `D1.unclassified` fix hint (defect 4)

**Files:**
- Modify: `src/ccgate/taxonomy.py:39`
- Test: `tests/test_taxonomy.py`

**Interfaces:**
- Produces: `FIX_HINTS[D1_UNCLASSIFIED]` no longer mentions upgrading Claude Code.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_taxonomy.py
from ccgate import taxonomy


def test_unclassified_hint_names_the_policy_block():
    hint = taxonomy.FIX_HINTS[taxonomy.D1_UNCLASSIFIED].lower()
    # not just the absence of one word — pin the actual claim
    assert "upgrade" not in hint
    assert "attribution" in hint
    assert "statusline" in hint  # names why attribution is unavailable here
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_taxonomy.py::test_unclassified_hint_names_the_policy_block -v`
Expected: FAIL — current hint is `"upgrade Claude Code for cause attribution"` (contains "upgrade", lacks "statusline").

- [ ] **Step 3: Implement**

```python
# src/ccgate/taxonomy.py line 39
    D1_UNCLASSIFIED:          "cause attribution unavailable here (statusline blocked by policy) — escalate or use the Track B runner",
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_taxonomy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/taxonomy.py tests/test_taxonomy.py
git commit -m "fix(taxonomy): correct D1.unclassified hint — attribution is policy-blocked, not a version issue

CLI 2.1.278 already exceeds the attribution floor; miss_causes arrive via the
statusline payload, which org policy blocks. 'Upgrade Claude Code' would change
nothing. The honest hint points to escalation or the Track B runner."
```

---

### Task 5: Replace deprecated `datetime.utcnow()` in `shape.py`

**Files:**
- Modify: `src/ccgate/scripts/shape.py:856`
- Test: `tests/test_shape_fix.py`

**Interfaces:**
- Produces: `applied_at` still a `YYYY-MM-DDTHH:MM:SSZ` string, produced without a `DeprecationWarning`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_shape_fix.py`. This reuses the exact apply path from the existing `TestApplyFixes::test_report_startup_delta` (`stage_fixes` → `apply_fixes` under a patched `Path.home`):

```python
# tests/test_shape_fix.py — add these imports at top: `import re`, `import warnings`
def test_applied_at_has_no_deprecation_and_valid_format(tmp_path):
    with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
        staged = stage_fixes(cwd=str(tmp_path))
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            applied = apply_fixes(staged)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", applied["applied_at"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_shape_fix.py -k deprecation -v`
Expected: FAIL — `datetime.datetime.utcnow()` raises `DeprecationWarning` (promoted to error by the filter).

- [ ] **Step 3: Implement**

```python
# src/ccgate/scripts/shape.py line 856
        "applied_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
```

(The module imports `datetime` as a module — `datetime.datetime.now(datetime.timezone.utc)` is correct. Do not change the import.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_shape_fix.py -v`
Expected: PASS (all, including the existing apply tests).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/scripts/shape.py tests/test_shape_fix.py
git commit -m "fix(shape): replace deprecated datetime.utcnow() with timezone-aware now(UTC)"
```

---

## Final verification (after all tasks)

- [ ] Run the full suite: `python -m pytest -q` (note: subprocess-spawning CLI tests — `test_baseline`, `test_digest`, `test_otel_reader`, `test_since_filter`, `test_miss_audit_g4g5` — may error with `WinError 6/50` in a non-interactive shell; that is an environment limitation, not a regression. Run those specific files in an interactive terminal if you need them green.)
- [ ] Confirm `test_miss_audit.py`, `test_transcript.py`, `test_taxonomy.py`, `test_shape_fix.py` all pass.
- [ ] Re-run the baseline capture on the fixed tool and confirm no dollar figures are emitted and token totals are non-negative (the recomputed, trustworthy baseline that §7 of the spec defers to Plan 1).
