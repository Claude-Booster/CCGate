# Handoff → Plan 2 (fresh session seed)

Written 2026-09-28 to seed a fresh, cheaper session for Plan 2. Read this + the
spec docs below, then start with the two blockers, then Plan 2 brainstorming.

## Read first (spec docs)
- `docs/superpowers/specs/2026-09-26-two-track-architecture-design.md` — the architecture (Track A measurement / Track B owned loop). §12.8 (SDK enforcement verified), §9 (§12.9 failure-first discipline).
- `docs/BUILD-SPEC.md` §11–§12 — invariants, risks, resolved decisions.
- `docs/baseline-2026-09-27-fixed.md` — the trustworthy pre-Track-B baseline (fixed miss_audit).
- Memory: `feedback_superpowers_workflow` (follow the flow for EVERY task, incl. ops/deploy/investigation), `project_org_policy_blocks_hooks`, `reference_hook_latency_environment` (subprocess tests unrunnable via agent Bash — DuplicateHandle).

## Status
- **Plan 1 complete** (miss_audit fixed): raw tokens, no dollars, subagent bucketing, honest hints, datetime fix. Local branch `plan1-fix-miss-audit`, full history local.
- **Public snapshot pushed** to `github.com/Claude-Booster/CCGate` `main` (PII-clean, no history; `.githooks/` + `docs/superpowers/` excluded). CI PII gate `verify.yml` live on main; `BLOCKED_PATTERNS` secret set; main protected requiring `verify`.
- **Key result:** of 11,918 misses, 2,865 main-thread vs 9,053 subagent (76% structural). "Avoidable" was mostly subagent noise.

## Verified channel list (2026-09-26/27)
**Blocked by org policy (`allowManagedHooksOnly`, kind="org"):** all hooks (user/project/local/flag), `statusLine`.
**Working:** transcript JSONL read (~15s lag, ground-truth `usage`); MCP servers (own tools only, can't observe other tools); git hooks (PII scanner); `permissions` allow/deny; skills + `ccgate` CLI; **Agent SDK programmatic hooks — verified: a `PreToolUse` deny held** (Track B's load-bearing fact; does NOT depend on CI).
**Unverified (the CI probe answers these):** does the org policy reach a CI runner; auth via `CLAUDE_CODE_OAUTH_TOKEN`; egress.

## Subprocess-test failures — RESOLVED (2026-09-28), Track B NOT blocked
Ran on the user's terminal: 27 failed (`WinError 6 DuplicateHandle`), reproduces there too. Root-caused via systematic-debugging:
- Raw subprocess works outside pytest; single subprocess tests pass in isolation; collect-all + run-one fails → cumulative contamination.
- Bisected to the hook-test cluster (test_pre_tool + test_session_end + test_session_start): none alone, together they tip fd 0's handle invalid → later `subprocess.run` (inheriting fd 0) fails at DuplicateHandle. The 2 ccgate source `open()` sites are properly closed → pytest capfd fd-hygiene, NOT a product leak.
- **Track B (`ccgate run`) spawn path is UNAFFECTED** — SDK spawns the CLI fine; runtime subprocess works. This is a pytest-suite-only bug.
- **FIX (Plan 2 task):** add `stdin=subprocess.DEVNULL` to the subprocess helpers in `test_baseline`, `test_digest`, `test_miss_audit_g4g5`, `test_otel_reader`, `test_since_filter`. Small, mechanical.
  - **Verification status: partial, not proven.** Confirmed only that it makes ONE test (`test_baseline::test_passes_on_fixture`) pass under full collection — strong evidence the mechanism is right, NOT proof the whole suite goes green. The 27→0 confirmation MUST be a full `python -m pytest -q` run in the **user's terminal** (~6min; 20+min and unreliable via the agent — do not attempt there). Only that run closes it.

## CI probe — DONE (2026-09-28), Track-B-in-CI is GREEN
`CLAUDE_CODE_OAUTH_TOKEN` set; probe built (`ci-probe.yml`, workflow_dispatch), merged to main (PR #2), triggered (run 36384205614, success). Results:
- **PROBE_AUTH_EGRESS: OK** — OAuth token authenticates the SDK + API egress works from a GitHub-hosted runner.
- **PROBE_SDK_PRETOOL_HOOK_FIRED: 1 ['Bash']** — the programmatic PreToolUse hook fired AND denied in CI. Track B enforcement works in CI.
- **PROBE_POLICY_REACH: no allowManagedHooksOnly marker** — the org hook-blocking policy does NOT reach the runner. Smoking gun: CI `policy-limits.json` shows `"kind":"token"` vs the machine's `"kind":"org"`. **Policy follows the interactive org login, not the OAuth-token principal.** CI is clean.

**Conclusion: Track B is viable on-desk AND in CI. No blocker remains.**

**⚠️ LOAD-BEARING DEPENDENCY (re-verify, don't treat as settled):** the entire CI story rests on `kind="token"` staying outside the org policy's scope. This is a *mechanism* (policy scoped to the interactive login, not the token principal) that the org could change — if the org rescopes remote settings to cover token principals, `allowManagedHooksOnly` would land in CI and Track-B-in-CI breaks. Re-run the `ci-probe` (or its policy-reach check) if: the org changes Claude enterprise settings, the token is regenerated, or a Track B CI run starts behaving as if hooks are blocked. Treat this as the single assumption most likely to silently invalidate the CI design.

**Cleanup owed (spike hygiene):** `ci-probe.yml` is throwaway and still on `main` — remove it (PR→verify→merge) as an early Plan 2 task, or leave it (dispatch-only, harmless) if you want to re-run.

## CI probe design (DONE — see "CI probe — DONE" above for results; kept for reference)
Throwaway `workflow_dispatch`-only workflow that installs `claude` CLI + `claude-agent-sdk`, runs ~10 lines of `query()` with a programmatic `PreToolUse` deny over a fixed prompt, prints: auth status, whether the deny fired, whether the call completed.
- Reads `CLAUDE_CODE_OAUTH_TOKEN` from the repo secret.
- **`workflow_dispatch` only — no `pull_request_target`, no PR-authored code checkout in the token job** (public repo, subscription credential).
- Runners are GitHub-hosted (0 self-hosted) → egress open there; the egress question only bites if self-hosted corporate runners are introduced.
- Answers: (a) auth works? (b) **policy reach** — does remote-settings sync follow the account (→ `allowManagedHooksOnly` lands in CI too) or the machine (→ runners clean)? Check whether a settings-derived hook is blocked while the code-supplied SDK hook still fires. (c) egress holds?
- Must land on `main` (dispatchable) via PR→verify→merge (branch protected).

## Plan 2 scope (brainstorm opens here)
- **Track B usage = substantial** AND **must be CI-runnable** (user requirement) — "runs in CI" is a first-class Track B design constraint, not an afterthought. A negative on the policy-reach probe reshapes the CI story (but not the on-desk story — SDK deny is already proven locally).
- **Track A** (measurement re-baseline on the fixed tool + reporting) likely ships before Track B — measure before enforce.
- **Track B needs a success criterion — define it in the brainstorm, before building.** Right now nothing states what "Track B works" means, which is the exact failure this project spent a week unlearning. Tie it to the baseline's actionable number: **main-thread misses per 1,000 requests, measured before vs after, on comparable work** (baseline: 2,865 main-thread misses across 45,991 main-thread requests ≈ 62/1k). Without a before/after on a comparable workload, Track B ships and no one can say if it helped.
- **Open question for the brainstorm — does Track B close the attribution gap?** `D1.unclassified` is 11,405 (≈96% of misses) because on-desk attribution needs the statusline `miss_causes` payload, which org policy permanently blocks. But Track B's owned loop can read cache behaviour directly (`get_context_usage`, `usage` blocks, compact_boundary metadata) — so it may attribute misses its own sessions can't attribute on-desk. If it does, that's a real, independent argument for Track B beyond enforcement. Worth resolving in the brainstorm.
- **Small task — subprocess-test fix:** apply the verified `stdin=subprocess.DEVNULL` fix (5 files, above). Do early so the suite is green before Track B adds `ccgate run` entry-point tests.
- **CI probe (spike): DONE** — Track B viable in CI (auth/egress OK, SDK hook enforces, org policy doesn't reach the runner). Remaining: remove the throwaway `ci-probe.yml` from main.
- **Deferred Ruling-2 residue:** the CI PII gate (`verify.yml`) is already live — done.
- **Execution constraint:** the AGENT (this tool + its subagents) cannot run the full pytest suite or subprocess/entry-point tests to green — `DuplicateHandle` in the agent's process chain, and the cumulative fd-0 bug. Full-suite and `ccgate run` integration verification must run in the **user's terminal or CI**. State this in the Plan 2 plan, not per-task. (Individual in-process tests DO run via the agent.)
