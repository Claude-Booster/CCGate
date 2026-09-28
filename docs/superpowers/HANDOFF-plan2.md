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

## Two blockers (yours, before Plan 2 brainstorming is worth its cost)
1. **Run `python -m pytest -q` in your own terminal**, paste the tail. Settles whether the 25 CLI-dispatch/subprocess tests pass on your machine (they fail via the agent Bash tool with `WinError 6 DuplicateHandle` regardless of sandbox — unprovable from the agent side). These are the exact spawn path `ccgate run` uses; if they fail for you too, Track B's entry point is broken before it's written. (Next time repro with ONE test, not the suite — the last full run took 3.5h.)
2. **Set `CLAUDE_CODE_OAUTH_TOKEN`** (from `claude setup-token`) as a repo secret on `Claude-Booster/CCGate`. NOT an API key — the managed corporate account does not provide `ANTHROPIC_API_KEY`; single-user automation via the OAuth token is the permitted path.

## CI probe design (spike — build in the fresh session, don't re-derive)
Throwaway `workflow_dispatch`-only workflow that installs `claude` CLI + `claude-agent-sdk`, runs ~10 lines of `query()` with a programmatic `PreToolUse` deny over a fixed prompt, prints: auth status, whether the deny fired, whether the call completed.
- Reads `CLAUDE_CODE_OAUTH_TOKEN` from the repo secret.
- **`workflow_dispatch` only — no `pull_request_target`, no PR-authored code checkout in the token job** (public repo, subscription credential).
- Runners are GitHub-hosted (0 self-hosted) → egress open there; the egress question only bites if self-hosted corporate runners are introduced.
- Answers: (a) auth works? (b) **policy reach** — does remote-settings sync follow the account (→ `allowManagedHooksOnly` lands in CI too) or the machine (→ runners clean)? Check whether a settings-derived hook is blocked while the code-supplied SDK hook still fires. (c) egress holds?
- Must land on `main` (dispatchable) via PR→verify→merge (branch protected).

## Plan 2 scope (brainstorm opens here)
- **Track B usage = substantial** AND **must be CI-runnable** (user requirement) — "runs in CI" is a first-class Track B design constraint, not an afterthought. A negative on the policy-reach probe reshapes the CI story (but not the on-desk story — SDK deny is already proven locally).
- **Track A** (measurement re-baseline on the fixed tool + reporting) likely ships before Track B — measure before enforce.
- **Deferred Ruling-2 residue:** the CI PII gate (`verify.yml`) is already live — done.
- **Execution constraint:** Track B's spawn/entry-point integration tests CANNOT run through the agent or subagents (DuplicateHandle). They must run in the user's terminal or CI. State this in the Plan 2 plan, not per-task.
