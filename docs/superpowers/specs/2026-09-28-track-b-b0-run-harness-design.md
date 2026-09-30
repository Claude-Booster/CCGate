# Track B / B0 — the `ccgate run` harness (walking skeleton) — Design

**Date:** 2026-09-28
**Status:** design, pending user review
**Parent:** `docs/superpowers/specs/2026-09-26-two-track-architecture-design.md`
(§3 Track B, §8 impossible list, §9/§12.9 acceptance discipline, §10 resolved decisions).
**Sibling done:** Track A (`docs/superpowers/specs/2026-09-28-track-a-measurement-design.md`) —
its `run_audit` / transcript reader are reused here.
**Handoff:** `docs/superpowers/HANDOFF-plan2.md` (verified facts: SDK `PreToolUse` deny holds
on-desk and in CI; org policy doesn't reach the token principal in CI; `/compact` instructions
work; `set_model()` exists; server-side compaction unavailable in SDK 0.2.128).

---

## 1. Why this doc / decomposition

Track B (§3 of the parent) is several subsystems: the run harness, F1–F3 enforcement,
A2 model economics, A4 compaction recovery, A5 compaction economics. That is too large
for one spec. This spec covers **B0 only — the walking-skeleton harness** everything else
plugs into. Later sub-projects each get their own spec → plan:

- **B1** — full F1–F3 enforcement (read-cache, Bash rewrite, richer `.contextignore`).
- **B2** — A2 model economics (`set_model()` at task boundaries).
- **B3** — A4 compaction recovery (`_extract` port, `/compact <instructions>` delivery).
- **B4** — A5 compaction economics (break-even, off-by-default logging first).

## 2. Scope & success criterion

### In scope
- `ccgate run --task <file>`: a ccgate-controlled `ClaudeSDKClient` loop, streaming stdout.
- **One real deny** — a thin F1 slice: deny `Read` of a path listed in `.contextignore`
  (§6), via a programmatic `PreToolUse` hook.
- A **session record** in Track A's JSONL shape, written to `~/.ccgate/runs/` (§5).
- **CI-runnability** as a first-class constraint (§7).
- The Track-B **success criterion, defined** (below) — not yet measured.

### Out of scope
- Full F1–F3, A2/A4/A5; miss attribution logic; any misses/1k *improvement* claim from B0.

### Success criterion — mechanism + substrate (what "B0 works" means)
1. **The deny is witnessed failing-then-holding (§12.9)** in a real `ccgate run` — **in CI
   as the required proof**, optionally reproduced on-desk (§7) — including the *shadowing*
   red state: the hook must be shown to fire **even when `Read` is in `allowed_tools`**,
   since that is the whole reason hooks were chosen over `can_use_tool`.
2. **The run writes `~/.ccgate/runs/<run-id>.jsonl`** that `ccgate audit` parses with **zero
   new reader code** (reuses Track A's `run_audit`).
3. **The spec states the Track-B measurement criterion**, to be measured once real
   enforcement lands (B1+):
   - **Metric:** main-thread misses per 1,000 requests, before vs after, **allowed to come
     back negative** (parent handoff — a weak result must be reportable as weak).
   - **"Comparable work" — operational definition:** the **same `--task` file run both
     ways** — once with enforcement disabled (`ccgate run --no-enforce`, the baseline) and
     once enabled (treatment) — misses/1k computed from each run's record via `ccgate
     audit`. Two caveats, stated now not discovered at B2:
     - **Trajectory divergence (the larger effect).** Disabling the hook does not merely
       turn off denial — it changes *what the agent reads*, and the two runs diverge from
       the first denied read onward. So "same task" yields two different sessions, not a
       controlled A/B. This is the honest comparison (it measures the real downstream effect
       of enforcement), but it is not a clean paired diff, and the write-up must say so.
     - **Model non-determinism.** Even identical config does not guarantee identical tool
       sequences run to run.
   - **Do NOT fix an N now** — you don't know the variance yet, and any N would be a guess
     (the exact `turns_remaining_est`-from-five-observations failure). Instead, **B1
     prerequisite (trigger, not a number):** run the same task twice with *identical* config
     and measure the misses/1k spread. If that run-to-run spread is larger than the
     enforcement effect you're trying to detect, single-run comparison is dead and you need
     N runs each way — and that two-run measurement is what tells you N. Decide N from data
     at B1; do not carry a guessed N into this spec.

## 3. SDK interaction model

**`ClaudeSDKClient`, not `query()`.** B0 is the foundation B2 (`set_model()` mid-session)
and B3 (`/compact` interjection at a chosen boundary) build on; neither works with a
one-shot `query()`. Choosing the stateful client now avoids a rewrite; the only cost is
B0 carries a stateful client it does not yet exercise.

```python
options = ClaudeAgentOptions(
    hooks={"PreToolUse": [HookMatcher(matcher="Read", hooks=[deny_contextignored_read])]},
    setting_sources=[],          # load-bearing: [] disables ALL filesystem settings so no
                                 # user/project/local allow rule can shadow the hook.
    allowed_tools=[...],         # includes "Read" — proving the hook denies a pre-approved tool
)
async with ClaudeSDKClient(options=options) as client:
    await client.query(task_prompt)
    async for msg in client.receive_response():
        recorder.append(msg)     # stream to stdout + record
```

- **Deny shape** (validated against SDK docs, README example): the hook is an async callback
  `(input_data, tool_use_id, context)` returning, to deny:
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": "<path> is in .contextignore"}}`, or `{}` to allow.
- **Why the hook, not `can_use_tool`** (§12.8): `can_use_tool` is invoked only when the
  CLI's rules evaluate to "ask" — not for `allowed_tools`/settings-permitted calls — so it
  cannot see every call. The `PreToolUse` hook does. This behaviour is **claimed by the
  README and therefore must be proven, not trusted** (§7 shadowing red state).

## 4. Components

A small `src/ccgate/run/` package, each unit one job:

| File | Responsibility | Testable without SDK? |
|---|---|---|
| `run/cli.py` | `ccgate run --task <file> [--no-enforce]`: read prompt, build options, drive the async loop, stream stdout, hand messages to the recorder | via a fake client |
| `run/policy.py` | `.contextignore` load + match; pure decision `(tool_name, tool_input) -> None \| deny_reason`; the `PreToolUse` callback wrapping it | yes (pure) |
| `run/record.py` | map SDK message → Track A JSONL entry; append-per-turn writer; terminal marker; run-id | yes (pure mapping) |
| `dispatch.py` | add `run` subcommand | n/a |
| `pyproject.toml` | add `claude-agent-sdk` dependency | n/a |

## 5. Session record & distinguishability

- **Location:** `~/.ccgate/runs/<run-id>.jsonl` — physically separate from
  `~/.claude/projects/`, so Track A and Track B records are distinguishable **by
  construction**, not by a path heuristic (§10). `ccgate audit ~/.ccgate/runs/` measures
  all Track B work with the existing `run_audit`.
- **run-id encodes the repo** (e.g. `<encoded-cwd>-<timestamp>-<shortuuid>`, reusing
  `transcript.encode_cwd`), so a single audit over the runs dir can **bucket by project**
  the way `by_origin` buckets by origin.
- **Shape:** Track A's JSONL — assistant entries `{"type":"assistant","timestamp",
  "message":{"model","usage":{...}}}` — so `read_transcript`/`run_audit` consume it
  unchanged. Exact SDK→usage field mapping (`input_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`, `output_tokens`, `ephemeral_*`) pinned in the plan against
  the SDK source.
- **Append-per-turn + terminal marker** (not atomic-at-end): each assistant turn is
  appended as it streams, so a crash mid-run leaves partial-but-valid data — the point of a
  long unattended run. On clean exit, `record.py` writes a terminal
  `{"type":"ccgate_run_end","status":"complete","run_id":...}` line. **Absence of that line
  = a crashed/incomplete run.** `read_transcript` already ignores non-`assistant` lines, so
  the marker never pollutes token counts; a small `run/record.py` helper reports
  completeness for callers that care. **Verify, don't assume, the marker is harmless to
  measurement:** an in-process test must show that `run_audit` (via `read_transcript`)
  produces *identical* token counts for a record with the terminal marker and the same
  record without it (§9). This project has been wrong before about "that line is ignored."
- **Raw material for later attribution** (§10): per-turn `usage` + model id + timestamp are
  captured now so B1+ can attribute misses the interactive surface can't (the handoff's open
  question). B0 does no attribution itself.

## 6. The one deny — thin F1 slice

Deny `Read` when its target path matches an entry in a `.contextignore` file (repo-root,
gitignore-style globs for B0 — full semantics are B1). The `PreToolUse` hook (matcher
`"Read"`) calls `policy.decide("Read", tool_input)`; a match returns the deny dict with a
reason naming the path. No match → `{}` (allow). No `.contextignore` present → allow all.
This is the exact `permissionDecision: "deny"` mechanism F1–F3 will all reuse; it stays and
grows in B1 (read-cache, Bash rewrite via `updated_input`, richer matching).

**`--no-enforce` safety (every baseline run, not just the test).** The `--no-enforce`
baseline (§2) omits the hook, so the agent **genuinely performs the reads the treatment
denies** — the same public-repo safety consideration as §7's hook-absent red state, but
applying to *every* baseline run rather than only the CI test. A baseline run must therefore
be pointed only at work whose `.contextignore`'d reads are safe to actually perform;
`ccgate run --no-enforce` is a measurement tool, not a safe default, and the flag's help
text says so.

## 7. CI-runnability & verification

Splits like Track A's did (the agent cannot run the SDK loop to green — auth, network,
`DuplicateHandle`):

**In-process (agent-runnable, no auth):**
- `policy.py`: match/decision, including the **deny's red state** (a `.contextignore`'d path
  denied; a non-matching path allowed).
- `record.py`: SDK-message → JSONL mapping produces entries `read_transcript` parses;
  append-per-turn ordering; terminal-marker presence/absence → completeness helper; **marker
  harmlessness — `run_audit` token counts identical with and without the terminal marker**
  (§5).
- **Fake `ClaudeSDKClient`** driving `cli.py` end-to-end: hook wired, stream consumed,
  record written, deny handled — deterministic, no auth/network.

**Real integration (CI `workflow_dispatch`; optional user-terminal smoke):**
Uses `CLAUDE_CODE_OAUTH_TOKEN` (proven pattern from the ci-probe spike). Two red→green
observations, both §12.9:
1. **The deny** — a real `ccgate run` over the pinned task tries to `Read` the pinned
   target; assert denied and the reason recorded.
2. **The shadowing** (load-bearing, its own red state) — **same config, `Read` in
   `allowed_tools`**: with the hook **absent**, the read **succeeds**; with the hook
   **present**, it is **denied**. This proves the hook fires for a pre-approved tool — the
   reason hooks were chosen over `can_use_tool` — rather than trusting the README. ccgate does
   **not** set `permission_mode="bypassPermissions"`: `allowed_tools` already auto-approves the
   listed tools non-interactively (verified in CI — the baseline read succeeds without bypass),
   and leaving the SDK's default permission gate in place keeps it as a backstop behind the
   deny hook (defense in depth). Bypass was investigated and rejected: it removes that backstop
   and makes hook correctness the *only* boundary once `Bash` is in play, so `tools` is kept
   explicitly minimal instead.

   **CI finding — sensitive-filename guard (fixture design):** Claude Code hard-denies reading
   files whose names look like secrets (`*secret*`, `*credential*`, `.env`, keys), independent
   of `permission_mode` — a safety guard, not a promptable permission. An early fixture named
   `target_secret.txt` was therefore denied in the *baseline* too, masking the shadowing proof
   (the read looked denied with no hook). The fixture uses a neutral name (`target_marker.txt`)
   so the **only** deny in the treatment is ccgate's own hook.

**Public-repo safety (explicit):** the hook-absent red-state run *actually performs the
Read*. The task prompt and target path are **pinned to a committed fixture file** under the
test tree — never a real or sensitive path — so the red run cannot touch anything that
matters. Stated here because this executes in a public repo.

## 8. Error handling
- Missing/unreadable `--task` file → clear error, non-zero exit, no client started.
- No `.contextignore` → allow all; run proceeds normally.
- SDK/auth failure → surfaced, non-zero exit; the append-per-turn record holds whatever
  completed and lacks the terminal marker (correctly reads as incomplete).
- `--no-enforce` → hook omitted from options (baseline runs for the §2 measurement).

## 9. Testing summary
- **Agent runs:** all §7 in-process tests (pure `policy`/`record`, fake-client harness),
  TDD, deny red state witnessed in-process.
- **CI/user-terminal only (state at plan level, not per-task):** the two real-SDK
  integration observations (deny + shadowing), and any full `ccgate run` smoke.

## 10. Deferred (not B0)
Full F1–F3 (B1), A2 `set_model` (B2), A4 `/compact` (B3), A5 break-even (B4), and miss
*attribution* logic. B0 only captures the raw material and proves the boundary.
