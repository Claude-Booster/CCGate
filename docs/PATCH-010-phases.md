# PATCH-010 — Reorder §10 Phases by Realized Savings

**Target:** `BUILD-SPEC.md` v1.0
**Sections touched:** §10 (replace), §1 (amend), §7 (append), §9 (append)
**Type:** targeted patch — do not regenerate `BUILD-SPEC.md`
**Status:** proposed

---

## Rationale

v1.0 ordered phases by novelty: cache-miss attribution first, because nothing else in the ecosystem does it. That was the wrong axis. Novelty and savings are unrelated, and the ordering buried the only component that saves tokens permanently.

Three facts force the reorder:

1. **The D1 causes cannot be intercepted.** `/model`, MCP connect, and idle timeout are not tool calls. No `PreToolUse` hook sits in their path. `miss_audit` can only diagnose and then rely on a human changing a habit — which is exactly the prose-dependent compliance loop invariant **I1** exists to forbid. Diagnosis has value; it is not enforcement, and it must not be sequenced as though it were.

2. **D4 compounds; nothing else does.** Startup overhead is paid before the first prompt of *every* session, forever. A one-time `claudeMdExcludes` and deny-rule edit that trims 8K off startup repays on every session for the life of the repo. No per-session lever compounds like that, and `shape` needs no hooks, no state, and no runtime.

3. **Bash output rewriting is the largest per-session lever by an order of magnitude.** Converting a 40K-token test run to 400 tokens, on every test run, outweighs the read cache and `.contextignore` combined. v1.0 placed it third inside Phase 2, behind the riskiest component.

---

## §10 — REPLACE ENTIRE SECTION

> ## 10. Phases
>
> Ordered by **(savings × certainty) ÷ risk**, not by novelty. Each phase ships independently and must show positive net before the next begins.
>
> ---
>
> ### Phase 0 — Static remediation
>
> `shape.py`, `shape --fix`, `model.py`, `transcript.py`, schemas. **No hooks. No runtime. No state.**
>
> Pure static analysis of `settings.json` and memory files, plus a safe-subset autofix. Targets D4 exclusively: `claudeMdExcludes`, `permissions.deny` read rules, `bashOutputMaxChars` / `taskOutputMaxChars`, skill-listing overflow, `promptCacheTtl` on non-subscription auth, `worktree.sparsePaths`.
>
> `promptCacheTtl` deserves separate mention: on API-key and cloud-provider auth it eliminates the entire `D1.ttl_expired` class deterministically, in config, with no hook. It is the one D1 cause that is preventable rather than merely diagnosable.
>
> **Exit criterion:** measured startup token count before and after `--fix`, recorded in `reports/`. If the delta is under 2,000 tokens the repo was already lean and later phases should be re-justified against that.
>
> Gates: G2, G3, G9, G10.
>
> ---
>
> ### Phase 1 — Diagnostics
>
> `statusline.py`, `miss_audit.py`, `post_tool.py`, `state.py`, `ledger.py`, learned per-tool costs.
>
> Read-only with respect to behaviour. Nothing is blocked, nothing is rewritten. `additionalContext` is emitted only for the unbounded-output notice, capped at `maxNoticesPerSession`.
>
> Labelled honestly in all output: **this phase saves nothing directly.** Its product is attribution plus the baseline that Phase 2's A/B tests are measured against. The one behavioural effect it does have is the status-line cold-cache warning, which changes what the user does in the moment — count that as diagnostics, not savings.
>
> Gates: G6, G7, G8, G11.
>
> ---
>
> ### Phase 2 — Output control
>
> First phase that denies or alters a tool call. Three sub-phases, each independently toggleable, each **off by default until its own A/B over recorded sessions shows positive net**. Ordered by yield first, with the risk note attached rather than driving the order.
>
> | | Component | Config key | Yield | Risk |
> |---|---|---|---|---|
> | 2a | Bash output rewriting | `bashRewriteEnabled` | high | medium — alters execution |
> | 2b | Context ignore | `contextignoreEnabled` | medium | low — denial only |
> | 2c | Read cache | `readCacheEnabled` | medium | **high** — compaction hazard |
>
> 2c ships last despite comparable yield to 2b. It carries the one failure mode in this tool that can make Claude hallucinate rather than merely waste tokens: blocking a re-read of content that compaction has already evicted. See `BUILD-SPEC-phase2.md` §F3.4.
>
> Full component specs: `BUILD-SPEC-phase2.md`.
>
> Gates: G4, G5, G12, G13, G14, G15.
>
> ---
>
> ### Phase 3 — Compaction economics
>
> `autoCompactMode`, `PreCompact` instruction injection, durable task state.
>
> Compaction is not a saving on its own — it forces a full re-cache at write rate, which is why v1.0 classified it `D2.compaction` and excluded it from the hit-ratio gate. It pays only when enough turns remain to amortise that cost. This phase makes the trigger point an economic decision instead of a fixed percentage.
>
> Carries the same unresolved `turns_remaining_est` dependency as §6 and §12 Q1. Do not start Phase 3 before Q1 is settled.
>
> Full spec: `BUILD-SPEC-phase2.md` §F4.
>
> Gates: G16, G17.
>
> ---
>
> ### Phase 4 — CI and fleet
>
> `baseline.py` as a repo gate. Pattern digest export/import (relative paths and counts only; audited on write *and* on import, absolute paths rejected rather than sanitised). Optional OTel reader for `claude_code.token.usage` by `type` label.
>
> Gates: G1.
>
> ---
>
> ### Phase dependency graph
>
> ```
> Phase 0 ──┬─→ Phase 2a ──→ Phase 2b ──→ Phase 2c
>           │
> Phase 1 ──┴─→ Phase 3 (requires Q1 resolved)
>           │
>           └─→ Phase 4
> ```
>
> Phase 0 and Phase 1 are independent and may be built in parallel. Phase 2 requires Phase 1's ledger to measure net. Phase 3 requires Phase 1's transcript reader.

---

## §1.2 — APPEND INVARIANT

> - **I7 — Savings claims are separated from diagnostic claims.** Any component that cannot alter behaviour is labelled a diagnostic in every surface it appears in, and its output never contributes to a `tokens_avoided` figure. Reports state realized savings and attributed-but-unrealized cost as two distinct numbers.

---

## §7 — APPEND CONFIG KEYS

> | Key | Default | Effect |
> |---|---|---|
> | `bashRewriteEnabled` | `false` | Phase 2a master switch |
> | `contextignoreEnabled` | `false` | Phase 2b master switch |
> | `readCacheEnabled` | `false` | Phase 2c master switch |
> | `autoCompactMode` | `"advise"` | `advise` / `tune` / `drive` — see §F4 |
>
> All four are additionally forced off by `CCGATE_DISABLE=1` in the environment, which must be honoured before any config file is read.

---

## §9 — APPEND GATES

> | ID | Gate | Threshold | Script |
> |---|---|---|---|
> | **G12** | Bash rewrite safety | zero rewrites emitted for any compound command in the adversarial corpus | `tests/rewrite_safety.py` |
> | **G13** | Exit-code fidelity | rewritten command preserves the original exit code across 40 fixture commands | `tests/rewrite_exit.py` |
> | **G14** | Read-cache eviction safety | zero denials issued for any path whose content was evicted by compaction or tool-result clearing | `tests/readcache_evict.py` |
> | **G15** | Intent override | zero denials for a path named verbatim in the triggering user prompt | `tests/intent_override.py` |
> | **G16** | Compaction economics | `drive` mode never fires when projected remaining turns fall below the amortisation threshold | `tests/compact_econ.py` |
> | **G17** | State durability | task state file survives a compaction round-trip byte-identical | `tests/state_durable.py` |
>
> G14 is the highest-severity gate in the suite. It is the only failure mode that produces incorrect model behaviour rather than wasted tokens.

---

## Knock-on: §5 component tree

Add under `src/ccgate/hooks/`:

```
      pre_compact.py                 # Phase 3 — instruction injection + cache flush
      user_prompt.py                 # Phase 2 — intent capture for override
```

`user_prompt.py` is required by Phase 2b and 2c, not Phase 3. It records paths named verbatim in the user's message so `pre_tool.py` can exempt them (G15). Without it, both denial rules will eventually block a file the user explicitly asked for, which is the fastest way to lose trust in the tool permanently.
