# IMPL-SPEC — ccgate Automation

**Date:** 21 September 2026
**Supersedes:** `DECISION-BRIEF.md` (decision made below — no brainstorm required)
**Companion to:** `BUILD-SPEC.md`, `BUILD-SPEC-phase2.md`, `PATCH-010-phases.md`
**Reference:** `docs/research/2026-09-21-automation-surfaces.md`
**Status:** ready to implement

---

## 0. Architecture decision

**Track A — interactive Claude Code plugin — handles three of the four D1 cost drivers. Track B — an Agent SDK runner — handles the fourth and only runs for unattended work. No proxy.**

### 0.1 Correction to prior framing

Earlier analysis in this project concluded the interactive surface "structurally cannot reach" the D1 drivers, on the grounds that no hook can *trigger* a fix. That framing was wrong, and it was wrong in a way that made the problem look harder than it is.

Automation does not require triggering a correction. It requires **removing the possibility of the defect**. Under that framing:

| Driver | Interactive mechanism | Status |
|---|---|---|
| `D1.ttl_expired` | `ENABLE_PROMPT_CACHING_1H` + `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` in the settings `env` block | **eliminated** |
| `D1.tools_changed` | keep MCP tool definitions deferred; lint `alwaysLoad` out of `.mcp.json`. Deferred tools make connect/disconnect append-only, leaving the cached prefix intact | **eliminated** |
| `D1.model_switch` | `PreModelSwitch` hook denies a switch while the cache is warm and the projected saving does not clear the re-cache cost | **prevented** |
| `D1.compaction` timing | no hook can start compaction; `PreCompact` can only block | **not solvable interactively** |

Three of four are deterministic, hook-or-config enforced, and satisfy **I1**. Only compaction timing needs an owned loop.

### 0.2 Why not a proxy

A non-first-party `ANTHROPIC_BASE_URL` disables MCP tool-search deferral by default (issue #40314 measured 120K tool-definition tokens loaded upfront). That reintroduces `D1.tools_changed`, the driver Track A eliminates for free. Recoverable with `ENABLE_TOOL_SEARCH=true`, but the option trades a solved problem for an ops burden and a new failure surface. Rejected.

### 0.3 Disposition of the existing implementation

| Component | Disposition |
|---|---|
| `shape` / `shape --fix` | **promoted** — now the primary automation, extended per A1/A3 |
| `statusline` | kept, extended with the cache-warmth signal A2 needs |
| `miss_audit` | kept as diagnostic; expected to trend toward zero D1 findings once A1–A3 ship |
| `post_tool` | kept, extended with model/effort/warmth snapshotting |
| `pre_tool` rules | kept unchanged |
| `ledger` | extended to separate **prevented** from **avoided** |

Nothing is retired.

---

## 1. Prevention model

Every component below implements one of three patterns. Implementers must not blur them.

- **ELIMINATE** — change config so the defect cannot occur. Zero runtime cost, zero context cost. Always preferred.
- **PREVENT** — a hook denies the action that would cause the defect. Runtime cost, small context cost on firing.
- **RECOVER** — the defect is unavoidable; make it cheap. Applies only to compaction.

`miss_audit` keeps reporting causes, but a D1 finding after A1–A3 ship is now a **bug in ccgate**, not user behaviour. Treat it as such.

---

## 2. Track A components

### A1 — Session pinning (ELIMINATE)

**Problem.** Cache TTL expiry during idle, plus drift in model and effort across sessions.

**Mechanism.** `shape --fix` writes an `env` block into `~/.claude/settings.json`. Hooks cannot mutate the parent process environment, so this must be config, not runtime.

**Deferred effect (measured 2026-09-24).** Claude Code reads `settings.json` (env block AND hooks) and `hooks.json` only at session start — they do **not** hot-reload. Any `shape --fix` that touches these files therefore takes effect one session late: the user must restart Claude Code. `shape --fix`/`--apply` prints this notice when a staged fix targets `settings.json`/`hooks.json` (`_needs_restart_notice`). Do not assert a pin is live in the same session it was written.

```json
{
  "env": {
    "ENABLE_PROMPT_CACHING_1H": "1",
    "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h"
  }
}
```

Version floors: `ENABLE_PROMPT_CACHING_1H` requires ≥ 2.1.108; `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` requires ≥ 2.1.257 and accepts only `"5m"` or `"1h"`. Below a floor, omit the key and record a `version_gap` finding rather than writing a value that will be ignored.

Skip on subscription auth where the 1h TTL is already requested automatically. Detect via the status-line payload's `prompt_cache.ttl` on a prior session; if no prior session exists, write the keys anyway (harmless) and note the assumption.

**Assertion.** `session_start.py` on `source: startup` reads the resolved env, compares against intent, and writes a `pin_mismatch` finding to session state if they disagree. **It does not inject into context** — a startup notice on every session is a recurring context tax for a once-per-week problem.

**Config**

| Key | Default |
|---|---|
| `pinCacheTtl` | `true` |
| `pinnedModel` | `null` (no enforcement unless set) |
| `pinnedEffort` | `null` |

**Test.** `tests/pin_env.py` — given a settings file at each version floor boundary, assert correct keys written or omitted.

---

### A2 — Model switch guard (PREVENT)

**Problem.** Switching models mid-session invalidates the entire prefix. On a 200K-token Opus session the re-cache is on the order of $1.

**Hook.** `PreModelSwitch`, matcher `.*`.

**Input** (field names high-confidence, re-verify against installed version): `from_model`, `to_model`, plus common fields.

**Logic.**

```
1. If session state has no prompt_cache snapshot → ALLOW (cannot price it)
2. If prompt_cache.warm == false → ALLOW (nothing to lose)
3. recache_cost = recache_tokens_if_cold × rate_in(to_model) × write_multiplier
4. If recache_cost < modelSwitchFreeThreshold → ALLOW
5. If to_model is cheaper than from_model:
     projected_saving = current_context_tokens
                      × (rate_in(from_model) − rate_in(to_model))
                      × read_multiplier
                      × turns_remaining_est
     ALLOW if projected_saving > recache_cost, else DENY
6. If to_model is more expensive → ALLOW
   (an upgrade is a capability decision, not a cost decision; never block it)
7. If the user's last prompt named a model verbatim → ALLOW
   (explicit intent, per F0.1 intent capture)
```

**Output on deny.** `hookSpecificOutput.permissionDecision: "deny"` with `permissionDecisionReason` per F0.3. Must state the price and the override:

```
ccgate/modelswitch: switching to Sonnet now re-caches 210K tokens (~$0.79) —
the cheaper rate recovers ~$0.31 over an estimated 12 remaining turns —
override: /ccgate allow-switch
```

**Critical constraints.**

- `PreModelSwitch` **can block but cannot redirect.** There is no `updatedModel` field. Do not attempt to substitute a different model.
- A hook cancelled at its timeout also blocks. Timeout must be short (`modelSwitchTimeoutMs`, default 2000) and the handler must fail **open** — any internal exception exits 0 with no decision.
- Step 6 is non-negotiable. Blocking an upgrade to Opus because it costs money makes the tool user-hostile and will get it uninstalled.

**Config**

| Key | Default |
|---|---|
| `modelSwitchGuard` | `false` |
| `modelSwitchFreeThreshold` | `0.25` (USD) |
| `modelSwitchTimeoutMs` | `2000` |

**Tests.** `tests/model_switch.py` — 20 fixtures across cold/warm, upgrade/downgrade, explicit-intent, and missing-snapshot cases. Assert fail-open on a raised exception.

---

### A3 — Tool deferral enforcement (ELIMINATE)

**Problem.** MCP servers whose tool definitions sit in the cached prefix cause a full invalidation on connect or disconnect. With deferral, the same events only append.

**Mechanism.** `shape` lints and `shape --fix` remediates:

1. `alwaysLoad: true` in any `.mcp.json` server entry → flag; removal requires confirmation, since it may be deliberate.
2. A non-first-party `ANTHROPIC_BASE_URL` present without `ENABLE_TOOL_SEARCH=true` → **error severity**, autofix writes `ENABLE_TOOL_SEARCH: "true"` into the settings `env` block. This is the single highest-value check in `shape`; without it an MCP-heavy session loads 100K+ tokens of tool definitions before the first prompt.
3. Report the deferral state plainly: how many servers, how many deferred, how many always-loaded.

**Assertion.** `session_start.py` records the tool count from the first status-line payload. A jump above `toolCountJumpThreshold` (default 30) between sessions writes a finding.

**Config**

| Key | Default |
|---|---|
| `enforceToolDeferral` | `true` |
| `toolCountJumpThreshold` | `30` |

**Tests.** `tests/tool_deferral.py` — fixtures for first-party and gateway base URLs, `alwaysLoad` present and absent.

---

### A4 — Compaction recovery (RECOVER)

**Problem.** Compaction cannot be timed interactively, so make it cheap to survive.

**Hooks.** `PreCompact` and `SessionStart`.

**`pre_compact.py`:**

1. Write `sessions/<id>.task.md` — active plan or task list, file paths under edit, decisions made and rejected, failing-test state.
2. Flush the read cache entirely (this is also **G14**, the highest-severity gate — see `BUILD-SPEC-phase2.md` §F3.4).
3. Exit 0 without blocking.

**`PreCompact` has no `additionalContext` channel and its top-level `systemMessage` is discarded.** Writing to disk and re-injecting on the next `SessionStart` is the only path. An implementation that returns `systemMessage` from `PreCompact` will silently do nothing.

**`session_start.py`** on `source: compact` or `source: resume`:

1. Read `sessions/<id>.task.md`, truncate to `taskStateMaxTokens` (default 800), inject via `hookSpecificOutput.additionalContext`.
2. Flush the read cache (covers restarts where `PreCompact` did not fire in-process).
3. Charge the injection to the ledger.

On `source: startup` or `clear`, inject nothing.

**Config**

| Key | Default |
|---|---|
| `compactRecovery` | `true` |
| `taskStateMaxTokens` | `800` |
| `compactInstructions` | shipped default |

**Tests.** `tests/compact_recovery.py` — **G17** (state survives a round-trip byte-identical) and **G14** (zero read-cache denials for evicted paths).

---

### A5 — Compaction advisory (economics, not percentage)

`statusline.py` replaces the fixed `compactAdviseAt` threshold with the break-even rule:

```
advise when   projected_savings > recache_cost

  recache_cost      = post_compact_tokens × rate_in × write_multiplier
  projected_savings = (current_tokens − post_compact_tokens)
                      × rate_in × read_multiplier
                      × turns_remaining_est
```

`post_compact_tokens` comes from the session's own prior compaction boundaries where `transcript.py` can find them, otherwise `compactionRatioDefault` (0.30).

Compacting at 80% with three turns left is pure loss. A percentage threshold cannot express that; this rule can.

Advisory only in Track A. Blocking auto-compact via `autoCompactEnabled: false` is **not** shipped as a default — a blocked auto-compact with no replacement produces a hard context-limit failure.

---

## 3. Track B — Agent SDK runner

**Scope.** Unattended work only: CI, batch refactors, scheduled audits. Not a replacement for the interactive plugin, and not something that runs during normal development.

**Entry point.** `ccgate run --task <file> [--model <id>] [--max-turns N]`

**Mechanism.** Own the loop via the Agent SDK's `query()`. This yields the controls the interactive surface lacks:

| Control | Mechanism |
|---|---|
| Compaction at a chosen boundary | send `/compact` as the prompt string; result carries `compact_metadata.trigger` |
| Clear | send `/clear` as the prompt string |
| Model change | `setModel()` between tasks, never mid-task |
| Tool gating | `canUseTool` callback — reuse the Phase 2 rule engine directly |
| Server-side context management | `clear_tool_uses_20250919` with `clear_at_least` as the anti-thrash guard |
| Server-side compaction | `compact_20260112` with `instructions` and `pause_after_compaction` |

Beta headers: `context-management-2025-06-27`, `compact-2026-01-12`. `compact_20260112` requires Opus 4.6+ / Sonnet 4.6+ / Fable 5. Gate on model id and fall back to client-side `/compact` when unsupported.

**Prefer server-side.** Anthropic's guidance is that server-side compaction handles context management with less integration complexity, better token accounting, and no client-side limitations. It also reads from the existing prefix cache rather than re-tokenizing.

**Compaction trigger.** The A5 break-even rule, but acted on rather than advised. `turns_remaining_est` is knowable here — the runner has the task list.

**Measurement.** Call `count_tokens` with the `context_management` block before each request. The response gives `input_tokens` (after edits) and `context_management.original_input_tokens` (before). That difference is the only *measured* savings figure available anywhere in this project. Everything else in the ledger is inference.

**Config**

| Key | Default |
|---|---|
| `sdkRunnerEnabled` | `false` |
| `sdkCompactStrategy` | `"server"` (`server` / `client` / `none`) |
| `sdkClearAtLeast` | `20000` |
| `sdkKeepToolUses` | `3` |

**Tests.** `tests/sdk_runner.py` — assert `/compact` fires only at task boundaries, `setModel` never mid-task, and `count_tokens` deltas are recorded per request.

---

## 4. Ledger changes

Three categories now, reported separately. Collapsing them is a spec violation.

| Category | Meaning | Measurement |
|---|---|---|
| `tokens_prevented` | defect made impossible (A1, A3) | counterfactual — baseline vs post-fix startup cost |
| `tokens_avoided` | action denied at runtime (A2, F1–F3) | real size of the avoided payload |
| `tokens_measured` | server-reported (Track B only) | `original_input_tokens − input_tokens` |

`tokens_injected` is subtracted from all three. **I3** stands: if net is negative, the headline says so.

Counterfactual figures carry `~` and record their baseline in `assumptions`.

---

## 5. Acceptance gates

Additive to `BUILD-SPEC.md` §9 and `PATCH-010-phases.md`.

| ID | Gate | Threshold | Script |
|---|---|---|---|
| **G18** | Env pinning | correct keys written or omitted at each version floor | `tests/pin_env.py` |
| **G19** | Switch guard fails open | any internal exception → exit 0, no decision | `tests/model_switch.py` |
| **G20** | Upgrades never blocked | zero denials where `to_model` is more expensive | `tests/model_switch.py` |
| **G21** | Deferral enforcement | gateway base URL without `ENABLE_TOOL_SEARCH` → error severity | `tests/tool_deferral.py` |
| **G22** | PreCompact channel | no code path returns `systemMessage` or `additionalContext` from `PreCompact` | `tests/hook_contracts.py` |
| **G23** | SDK boundary discipline | `/compact` and `setModel` only at task boundaries | `tests/sdk_runner.py` |
| **G24** | Ledger separation | the three categories never summed into one figure | `tests/ledger_categories.py` |

G19 and G20 are the user-trust gates. A switch guard that throws, or that blocks a deliberate upgrade to Opus, gets the plugin uninstalled within a day.

---

## 6. Build order

> **SUPERSEDED (2026-09-26).** The Track A/B split below assumed hooks and
> `statusLine` work. On the reference machine an org policy blocks both. The
> tracks are redefined in
> `docs/superpowers/specs/2026-09-26-two-track-architecture-design.md`
> (Track A = read-only measurement; Track B = owned Agent SDK loop). A1/A3
> config remediation is unaffected and still ships first; A2/A4/A5 relocate to
> Track B. The A1–A5 *component logic and configs* in §§3–5 above remain valid
> and are ported by the new design — only the delivery mechanism and the
> build-order framing here are superseded.

```
A1  session pinning          ── config only, no hook, ship first
A3  tool deferral            ── config only, highest single-check value
      ↓
A4  compact recovery         ── PreCompact + SessionStart, G14 fixtures first
      ↓
A5  compaction economics     ── statusline change only
      ↓
A2  model switch guard       ── first new denial; G19/G20 before enabling
      ↓
B   SDK runner               ── separate entry point, does not touch Track A
```

A1 and A3 are pure config remediation, need no hooks, and are where the compounding savings are. They ship first regardless of anything else.

A2 ships last in Track A because it is the first component that can annoy the user mid-task.

---

## 7. What remains impossible

Document in the README; do not attempt.

1. Triggering `/compact`, `/clear`, or a model switch from inside an interactive session — by hook, slash command, skill, MCP tool, or env var. Confirmed by anthropics/claude-code #58538, #37307, #66246; #65586 closed NOT_PLANNED.
2. Redirecting a model switch to a different target. `PreModelSwitch` blocks only; no `updatedModel` field.
3. Disabling native microcompact (#7176).
4. Keepalive pings to hold a cache warm from inside an interactive session.

File a feature request for an in-session control-plane API, referencing #58538 and #65586.

---

## 8. Handover note

This spec assumes the existing ccgate implementation is present and working. Every component is additive. If any section conflicts with what is already built, the existing behaviour wins and the conflict is reported rather than silently resolved.

Implement in the order given in §6. Do not implement Track B until Track A ships and `miss_audit` confirms D1 findings have trended to zero — if they have not, A1 through A3 are not working and the SDK runner will inherit the same bug.
