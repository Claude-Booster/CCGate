# Track A — Measurement & Honest Reporting — Design

**Date:** 2026-09-28
**Status:** design, pending user review
**Parent:** `docs/superpowers/specs/2026-09-26-two-track-architecture-design.md`
(Track A / Track B split). This spec covers **Track A only**; Track B gets its own
spec afterward, honouring the parent's "measure before enforce" sequencing.
**Baseline of record:** `docs/baseline-2026-09-27-fixed.md` (fixed-tool re-run).
**Invariants referenced, not restated:** I3, I4, I7 (`docs/BUILD-SPEC.md`);
§12.9 failure-first discipline; the D-code taxonomy (`src/ccgate/taxonomy.py`).

---

## 1. Scope & success criterion

### In scope
- A `--report` **view on the existing `ccgate audit` command** (§2): the I7 ledger
  block + the prevention-model framing (§4), over the same `run_audit` data.
- The I7 three-category ledger reported **honestly and per-cause** (§3).
- A **committed fixture corpus** with known-by-construction counts as the CI-runnable
  reproduction gate (§1 Layer 1).
- The subprocess-test fix (5 files) — **sequenced first** (§5).
- Removal of the throwaway `ci-probe.yml` from `main` (§5).

### Out of scope
- Track B, any enforcement, any owned SDK loop.
- Any rewrite of `ledger.py`'s `net`/G6/`notice_bytes` model — that is Track B's
  concern; rewriting it to emit zeros for Track A is busywork (Approach A, §2).
- Any fabricated `tokens_prevented` counterfactual number (§3).

### Success criterion — reproduction against a pinned corpus (concrete, pass/fail)

Track A alters no behaviour and claims no savings of its own (I7), so its success is
**not** a savings number and **not** the un-testable "a report a reader can trust."
The criterion is that **the new view reproduces known counts over a pinned corpus.**
This has two layers, because the real baseline corpus cannot be committed (1,104
transcripts, too large, full of local paths).

**Layer 1 — the committed gate: a reduced fixture corpus with known-by-construction
counts (this is the repeatable, CI-runnable, machine-independent gate).**
The plan builds a small set of **synthetic, PII-free JSONL transcripts** under
`tests/fixtures/` — engineered by construction to produce exact, known figures, and
**including both main-thread and subagent (`.../subagents/agent-*.jsonl`) transcripts**
so `by_origin` bucketing is exercised. The test asserts the exact counts the fixture is
built to yield (total misses, main misses, subagent misses, cache-read rate, hit ratio).
This is the only option that runs in CI and survives a machine change (the reason a file
list, path list, or hash manifest of the real corpus is rejected — all three break on a
new machine and none run in CI). Exact match, not "small drift"; the counts are known by
construction, so any deviation is a bug.

**Layer 2 — the one-time cross-check against the baseline of record (user terminal,
documented, not a committed test).**
Once, on the reference machine, run `ccgate audit --report` over the real local corpus
and confirm it reproduces `docs/baseline-2026-09-27-fixed.md`:

| Figure | Expected (baseline-2026-09-27-fixed) |
|---|---|
| Total misses | 11,918 |
| Main-thread misses | 2,865 |
| Subagent misses | 9,053 |
| Cache-read rate (token-weighted) | 96.4% |
| Hit ratio (request-level) | 89.7% |

This confirms the fixture is representative of the real summation path. It is a
user-terminal step recorded in the plan's evidence, **not** an ongoing CI gate (the
corpus grows, and it cannot be committed). Layer 1 is the gate that guards regressions;
Layer 2 is the one-time proof the gate reflects reality.

The honest-ledger properties in §3 and the bound-guard in §6 are **guards**, not the
criterion — they keep a wrong number from being printed; the criterion above is what
says the number is right.

## 2. Delivery — `ccgate audit --report` (Approach A on the ledger)

**One command, a new view.** `report` is a presentation mode over the same
`run_audit` output that `audit` already produces (`summary.tokens`, `by_origin`,
miss causes). Delivering it as `audit --report` rather than a second top-level
`ccgate report` command follows the project's own §12 Q5 decision (fewer commands;
every command description costs context in every session). The *view* is distinct —
a ledger block plus framing — but the *computation* and the *command surface* are
shared.

- `audit` default output: the existing miss-cause table (unchanged).
- `audit --report`: prints the I7 ledger (§3) + prevention framing (§4) + the
  ground-truth facts it just measured, then a pointer to the plain `audit` table for
  full cause attribution.
- `--report` composes with the existing selection/emit flags: `[paths]`,
  `--session ID`, `--since DURATION`, `--json`. Under `--json`, the ledger block is
  added to the existing report dict under a `ledger` key; no separate schema.
- **Single summation path.** The report view reads `run_audit`'s results; it does not
  re-implement token math. No double-counting.

**Ledger structure (Approach A).** `ledger.py`'s `net`/G6/`notice_bytes` model is
left untouched for Track B. The three I7 categories for the report view are computed
in the audit/report path from `usage`-block ground truth (which `run_audit` already
reads), not from `ledger.py`'s Track-B-shaped session dict.

## 3. The I7 ledger — honest, per-cause (§5 of parent)

The category is **not** a single "unmeasurable" null. Writing the whole category off
obscures the one part of the prevention model that still has headroom. Report it
**per cause**:

### `tokens_prevented` — per-cause, honest

| Prevention item | Value on this machine | Reason printed |
|---|---|---|
| **A1** (1h cache pin) | **unmeasurable** | already applied before the baseline; 258.8M `ephemeral_1h` tokens confirm it works — no clean "before" exists |
| **A3** (tool deferral / `tools_changed`) | **zero** | driver absent — 0 `D1.tools_changed` in the corpus; nothing to prevent here |
| **D4** (`claudemd_bloat`/excludes, `denyReads` on generated trees, `bashOutputMaxChars`, `skill_listing` overflow) | **measurable, not yet captured** | these are `shape` findings, **none applied**, each with a clean before; they compound on every session (the project's original highest-ROI claim) |

For **D4**, the report surfaces `shape`'s current findings (reusing the existing
`shape` check functions — `_check_claudemd_excludes`, `_check_deny_reads`,
`_check_skill_listing`, `bashOutputMaxChars`, etc.) as an **actionable, unapplied
list — with no token estimates in Track A.** This is settled, not left to the plan:
Track A lists the findings and marks the headroom as available-but-uncaptured, and
**attaches no number to any of them.** A `claudeMdExcludes` projection (token count for
files that would stop loading) is measurable in principle, but it is a **counterfactual**,
and this project has already been burned once by a counterfactual nobody could check
(the −114,073 dollar figure, §9 of the parent). The real before/after is produced later
by `shape --fix` measuring an actual applied change — not estimated here. Ship the list.

### `tokens_avoided`, `tokens_measured`, `tokens_injected`, `net`

| Category | Value | Reason printed |
|---|---|---|
| `tokens_avoided` | `0` | Track A denies nothing (runtime denial is Track B only) |
| `tokens_measured` | `0` | Track A measures no server-side compaction delta (Track B only) |
| `tokens_injected` | `0` | interactive ccgate injects nothing — hooks are policy-blocked |
| `net` | `0`, no net effect claimed | reporter, not actor (I7) |

Per I3, a negative net would say so in the headline; here it is `0` by construction.
The three categories are reported **separately** (collapsing them is a G24 violation,
parent §5).

## 4. Prevention-model framing (content the report prints)

One factual block. It must **not** claim interactive "keeps ELIMINATE" unqualified —
on this machine the only live ELIMINATE saving is A1 (already applied); A3's driver is
absent; and the D4 items are **available but not yet applied**. Stating ELIMINATE as a
present-tense running benefit overstates what is actually in effect today.

Accurate wording:

> **ELIMINATE remains available to interactive sessions** (config savings the org
> policy cannot reach). On this machine A1 (1h pin) is applied; A3's driver is absent;
> the D4 items are **unapplied headroom** — run `ccgate shape` to see what is not yet
> applied. **PREVENT and RECOVER** move to the owned loop (`ccgate run`) and apply only
> to Track B work.

Followed by the ground-truth facts it just measured (total/main/subagent misses,
cache-read rate, hit ratio) and a pointer: run `ccgate audit` (no `--report`) for the
full cause-attribution table. No overselling.

## 5. Housekeeping — sequenced

### Task 1 (must be first) — subprocess-test fix, with a terminal exit gate

Add `stdin=subprocess.DEVNULL` to the subprocess helpers in `test_baseline`,
`test_digest`, `test_miss_audit_g4g5`, `test_otel_reader`, `test_since_filter`
(root-caused in the handoff: cumulative fd-0 handle invalidation under full pytest
collection — a suite-only bug; the `ccgate run` spawn path is unaffected).

**Exit gate:** the full suite must be verified **27 → 0 green in the user's terminal**
(`python -m pytest -q`, ~6 min) **before Track A adds any tests.** Rationale: adding
tests to a suite whose failure baseline is 27 makes a new failure indistinguishable
from an existing one. This is the reason it is task 1, not merely "folded in."

**The agent cannot run this** (`DuplicateHandle` in the agent's process chain; ~20+ min
and unreliable). The full-suite confirmation is a **user-terminal step**, stated here
at the plan level, not per-task (parent §12 execution constraint).

**Capture the evidence.** "27 → 0" is the claim; the terminal output is the evidence.
Paste the actual `pytest -q` summary line into the plan's evidence log. It is the exit
gate for everything downstream — no Track A test is added, and no later task starts,
until that green-suite output is recorded.

### Independent — `ci-probe.yml` removal

Delete the throwaway `ci-probe.yml` from `main` via PR → `verify` → merge (branch
protected; normal flow, **no hook bypass**). Independent of the test fix and of the
Track A view; can land any time.

## 6. Error handling & testing

### Error handling
- No transcripts found → exit 0 with a message (matches `audit` today).
- Unrecognised `--since` → error exit with usage hint (matches `audit` today).
- **Bound-assertion failure → raise / non-zero exit, never print the bad number.**

### Bound assertions (§12.9 applied to Track A output)
Every figure with a by-construction bound is asserted before printing, and the
assertion must be **seen to fire** in a test:

- each ledger category `≥ 0`; net sign consistent with I3;
- cache-read rate ∈ [0,1]; misses ≤ requests; main + subagent misses = total misses;
- **every token quantity `≥ 0`** (`grand_total_input`, `cache_read`, `cache_creation`,
  `input`, `output`);
- **`cache_read + cache_creation ≤ grand_total_input`.**

The last two are the ones that would have caught the original defect: the 2026-09-26
failure was a **negative total** that no bound was watching (parent §9). The guard must
cover the shape of the defect that actually occurred, not only the new figures. This is
the same discipline `miss_audit` got in Plan 1 (`total_usd ≥ 0`).

### Tests the agent CAN run (in-process)
- **Reproduction over the committed fixture corpus** (the §1 Layer-1 gate): asserts the
  exact known-by-construction counts (total/main/subagent misses, cache-read rate, hit
  ratio). The §1 Layer-2 full-corpus cross-check is a user-terminal step, not this test.
- Ledger category values: `avoided == 0`, `measured == 0`, `injected == 0`; per-cause
  `tokens_prevented` shape (A1 unmeasurable, A3 zero, D4 findings present).
- `--json` shape: `ledger` key present with the category structure.
- Selection-flag plumbing: `--report` composes with `--session` / `--since` / `paths`.
- **Red→green guard test (§12.9):** feed a crafted impossible input (e.g. misses >
  requests, or a negative category) and watch the bound-assertion fire — the red state
  witnessed, then the guard shown to catch it.

TDD throughout — failing test first.

### Verification the agent CANNOT do (plan-level note)
The full pytest suite (Task 1's 27→0) and any subprocess/entry-point path verify only
in the **user's terminal or CI**. Individual in-process tests above DO run via the
agent.

## 7. Component disposition

| Item | Action | Notes |
|---|---|---|
| `scripts/miss_audit.py` `run_audit` | reuse unchanged | source of ground-truth token facts |
| `scripts/miss_audit.py` `main` | add `--report` flag + ledger/framing render | one command, new view |
| `dispatch.py` | no new subcommand | `--report` is a flag on `audit` |
| `ledger.py` | untouched | Track B concern (Approach A) |
| `scripts/shape.py` check fns | reuse for D4 findings | `_check_claudemd_excludes`, `_check_deny_reads`, `_check_skill_listing`, … |
| 5 test files | add `stdin=subprocess.DEVNULL` | Task 1, terminal exit gate |
| `tests/fixtures/` (new) | synthetic main + subagent JSONL, known counts | §1 Layer-1 gate; PII-free, CI-runnable |
| `ci-probe.yml` | delete from `main` | independent, PR→verify→merge |

## 8. Sequence

1. **Subprocess-test fix** → verify 27→0 green **in user's terminal**; record the
   `pytest -q` summary line as evidence (exit gate for everything below).
2. **`ci-probe.yml` removal** (independent; any time after or in parallel via PR).
3. **Build the committed fixture corpus** (§1 Layer 1): synthetic PII-free JSONL,
   main + subagent, with known-by-construction counts.
4. **`audit --report` view**: ledger (§3) + framing (§4), TDD, with the Layer-1
   fixture-reproduction test and the §6 bound-guard test.
5. **One-time Layer-2 cross-check** (user terminal): confirm the five
   `baseline-2026-09-27-fixed` figures reproduce over the real corpus; record in the
   plan's evidence log.
