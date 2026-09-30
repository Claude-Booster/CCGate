# Track B — Enforcement measurement experiment (design)

**Status:** design (brainstormed 2026-09-30). Gates the B1c build decision. No new product feature — this measures whether the enforcement already shipped (F1 on Read+Bash, F3 truncation) helps on real work, before building more efficiency features.

## 1. The question

Track B has F1 (Read deny + Bash-read deny) and F3 (Bash output truncation), all CI-proven to *fire*. None has been shown to *help*. The spec's success criterion (main-thread misses/1k, baseline 62/1k, `--no-enforce` vs enforced) has never been exercised. Before building B1c (F2 read cache — pure efficiency, no correctness story), measure whether content-reduction enforcement produces a net saving on a task you'd actually delegate.

## 2. Metrics (per run, from the run record via `run_audit`)

- **Primary — tokens per task:** `grand_total_input + output` summed over the run's assistant usage blocks.
- **Secondary — misses/1k:** from `run_audit`; reported for continuity with the 62/1k baseline, **not gated on** (F1/F3 target token *volume*, not cache misses — misses/1k may not move).
- **Interpretive — turns to completion:** count of assistant turns. The only thing that explains a negative (enforcement can save truncation tokens but spend more on recovery turns).
- **Gate — completion + reason:** did the run hit the verifiable done-condition (§4)? For a non-completion, record **why**: `gave_up` (agent stopped without the done-condition), `turn_cap` (hit the per-run limit, §7), or `error` (crash / SDK failure). A bare boolean can't tell whether enforcement *caused* a completion drop (§6) — only the reason can (a denied read that makes the model give up is a real harm; a turn-cap hit may just be a too-tight cap).
- **Diagnostic — F1 fires, F3 truncations:** counts of `rule:F1`/`rule:F3` events (excluding `summary:true`), to confirm each feature actually engaged.

## 3. Method

1. **Variance check first — 2 runs on the ENFORCED arm, identical config.** Variance on the arm claiming the effect is what matters.
2. **Derive N from that spread — do not default it.** Let `d = |run1 − run2| / mean` on tokens/task:
   - `d ≤ ~10%` → **N = 5/arm** is sufficient for the minimum-based rule.
   - `~10% < d ≤ ~25%` → **raise N (≥ 9/arm)** so the baseline *minimum* is a stable order statistic.
   - `d > ~25%` → the effect is **unmeasurable at feasible cost**; declare that (§8) and do **not** run a doomed A/B.
   The decision rule (§6) compares against the baseline *minimum* — an order statistic — so what N buys is the chance a real effect survives the spread. The number comes from the two runs, not from a guess.
3. **A/B — N runs per arm:** baseline = `ccgate run --no-enforce`, enforced = `ccgate run` (F1+F3 on). Same task, same `.contextignore`, same model.
4. Arms are **different sessions** (a denied read changes the trajectory), so compare **distributions**, not a paired diff.

## 4. Task (real, delegatable, verifiable)

Add the two missing `test_config` boundary cases (the 10M-ceiling `bashCap*Chars` fallback, deferred from B1b) **and** the deferred `tokens_injected` field on the F3 event (`bashcap.py`). Read-heavy (`config.py`, `bashcap.py`, `test_config.py`, `test_bashcap.py`, the B1b spec), edits across several files, a test run per iteration → exercises F1 (reads) and F3 (repeated large test output).

**Done-condition (machine-checkable, verified OUTSIDE the agent's session):** after the run ends, the **harness itself** runs `python -m pytest tests/test_config.py tests/test_bashcap.py` against the resulting working tree AND checks the produced code for the `tokens_injected` key on the F3 event. **Never trust the agent's own report of a passing test** — the org sandbox already showed the model will narrate a plausible tool result with nothing behind it. A run whose *harness-run* check fails is **incomplete** — excluded from the tokens/task comparison, counted in completion rate with its reason (§2).

## 5. `.contextignore` — realistic, and a finding either way

Use an ignore set a real user would write: lockfiles, build artifacts, `src/ccgate.egg-info/`, `~/.ccgate/runs/*`, large generated files. **Do NOT** craft `.contextignore` to force F1 to fire on files the task wouldn't touch. Before the A/B, confirm the task *naturally* reads at least one ignored path (F1 fires ≥ 1). **If F1 cannot trigger on real work in this repo, that is the headline finding** — F1 solves a problem this codebase doesn't have — reported, not engineered around. In that case the A/B measures F3 alone and says so.

## 6. Pre-declared decision rule (red state, declared before any number)

- **VOID** (experiment inconclusive, not a decision) if **either arm completes ≤ 50% of its runs** — at low completion neither median is meaningful.
- Otherwise **build B1c only if**: enforced **median tokens/task < the minimum observed baseline tokens/task** AND enforced **completion rate ≥ baseline completion rate**.
- **Otherwise, do not build B1c.**

Rationale: the effect must clear the *comparison's* noise, not one arm's; "enforced median below the baseline minimum" is the strict, unambiguous bar available when variance was only measured on the enforced arm. The completion condition prevents "saving" tokens by failing tasks. B1c is another content-reduction efficiency feature with no correctness story; if F1/F3 can't beat this bar on real work, B1c's shared premise is unproven.

## 7. Harness & environment

CI-only (org sandbox blocks on-desk Bash/reads). A script (`scripts/measure_ab.py`) runs `ccgate run` N times per arm against the task fixture, collects each run record path, and prints per-run metrics + per-arm aggregates (median tokens/task over completed runs, completion rate + reason breakdown, median turns, total F1 fires, total F3 truncations) and the decision-rule verdict.

- **Per-run cost cap (required):** the runs spend real subscription tokens; N runs of a multi-file task with a test loop is not free. Each `ccgate run` gets a **per-run turn cap** (SDK `max_turns`, threaded through a new `ccgate run --max-turns` option) so a pathological run can't burn the budget alone; a run that hits the cap is `turn_cap` non-completion (§2), not a crash. Set a sane default (e.g. 40 turns) and log the total across the experiment.
- **Clean-tree reset between runs:** the task edits real repo files, so the harness resets the working tree to a fixed clean base commit **before each run** and runs the done-check on the post-run tree. Runs are independent; the agent's edits are discarded per run (landing one good run's diff to actually clear the deferred backlog is an optional follow-up, not part of the measurement).
- A `workflow_dispatch` workflow runs it with `CLAUDE_CODE_OAUTH_TOKEN`. Pure metric-extraction, the derive-N rule, and the decision-rule function are unit-tested in-process; the runs themselves are CI.

## 8. Out of scope

Building B1c (this experiment gates it); changing F1/F3; any `.contextignore` engineering to flatter the result. The variance check may conclude the effect is unmeasurable at feasible N — that is a valid terminal result, not a failure to fix.

## 9. Review focus

- The done-condition is genuinely machine-checkable and identical across arms (an incomplete run can't masquerade as cheap).
- The decision rule is computed from data, not adjustable after seeing it (enforced median vs baseline *minimum*; the >50% void gate).
- F1-fires diagnostic is checked *before* trusting any F1 conclusion — a null F1 result is distinguished from "F1 never fired."
