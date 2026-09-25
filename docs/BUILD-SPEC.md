# BUILD-SPEC — `ccgate`

**A deterministic context and prompt-cache gate for Claude Code.**

Version: 1.0 (draft)
Status: pre-implementation
Target: Claude Code plugin, Python 3.11+, stdlib only

---

## 1. Purpose

`ccgate` measures, attributes, and enforces token efficiency in Claude Code sessions.

It exists because two different problems are routinely conflated:

| Problem | Metric | Current tooling |
|---|---|---|
| Context is too large | tokens in window | well covered (`/context`, ccusage, CCO) |
| Context is not cached | `hit_ratio`, miss causes | **effectively uncovered** |

The second is the higher-leverage problem. Cache reads bill at roughly 10% of standard input rate, so a session at 60% hit ratio costs materially more than the same session at 92% — with identical context size and identical work done. Claude Code now exposes a *diagnosed* miss cause per session (`prompt_cache.last_miss_cause`, `prompt_cache.miss_causes`). Nothing in the ecosystem consumes it.

`ccgate` treats a cache miss as a **defect with an attributable cause**, not as ambient cost.

### 1.1 Non-goals

- Not a usage dashboard. `ccusage` and `cc-analyzer` already do historical cost reporting; `ccgate` must not duplicate them.
- Not a RAG / semantic-retrieval layer. No embeddings, no vector store, no reranking.
- Not an LLM-in-the-loop optimizer. Every decision path is deterministic and local.
- Not a replacement for `/compact`, `/clear`, or auto-compaction.
- Does not modify, truncate, or rewrite the conversation transcript.

### 1.2 Design invariants

These are load-bearing. Violating any of them is a build failure, not a style disagreement.

- **I1 — Rules live in scripts and schemas, never in prose.** No behavior may depend on Claude reading an instruction and complying. Prose drifts across runs; a `PreToolUse` hook does not.
- **I2 — Silent by default.** A context optimizer that narrates on every tool call spends the context it claims to protect. Hooks emit into context only when the message is *actionable*, capped per session.
- **I3 — Net accounting or nothing.** Every savings figure is net of `ccgate`'s own injected tokens. If net is negative, the report says so in the headline.
- **I4 — Ground truth over estimation.** Token figures come from the transcript's `usage` blocks and the status-line payload. Character-count heuristics are permitted only as a labelled fallback, and must carry a `~` prefix wherever surfaced.
- **I5 — No network, no telemetry, no dependencies.** Stdlib only. All state under one directory. Fully wipeable.
- **I6 — Read-only until Phase 2.** Enforcement ships only after measurement has produced a baseline.

---

## 2. Cost model

Per-turn billed input is:

```
billed_input = uncached_input        × rate_in
             + cache_creation_tokens × rate_in × write_multiplier
             + cache_read_tokens     × rate_in × read_multiplier
```

Both multipliers are model-dependent and must be read from a pricing table, not hardcoded at the call site. Cache TTL is also per-session, not constant: the main conversation gets one hour on a Claude subscription within plan usage, and five minutes on usage credits, API keys, and cloud providers, unless `promptCacheTtl` says otherwise. A cache-break guard that assumes 5 minutes will fire spuriously on ~92% of a subscription session's idle windows.

Context is laid out in three layers. A change to a lower layer invalidates everything after it:

| Layer | Contents | Invalidated by |
|---|---|---|
| System prompt | core instructions, tool definitions | tool-set change, Claude Code upgrade |
| Project context | CLAUDE.md, auto memory, unscoped rules | `/clear`, `/compact`, restart |
| Conversation | messages, tool results | every turn (append-only, cache-safe) |

Model and effort level sit outside the layer table but are part of the cache key on most models.

---

## 3. Defect taxonomy

`ccgate` classifies every unit of avoidable spend into exactly one of these. The taxonomy is the schema; reports, gates, and config keys all derive from it.

### D1 — Cache invalidation (avoidable)

| Code | Trigger | Detection |
|---|---|---|
| `D1.model_switch` | `/model` mid-session, `opusplan` plan-mode toggle, skill frontmatter naming a different `model` | model id changes between adjacent transcript entries |
| `D1.effort_change` | `/effort` mid-session | status-line `effort.level` delta + miss in same window |
| `D1.tools_changed` | MCP server connect/disconnect with non-deferred tools, plugin enable/disable, bare tool-name deny rule added | `last_miss_cause.causes` contains `tools_changed`; `tools_added` / `tools_removed` give magnitude |
| `D1.system_prompt_changed` | Claude Code upgrade, `--append-system-prompt` change on resume | `causes` contains `system_prompt_changed`; `system_char_delta` gives magnitude |
| `D1.fast_mode_toggle` | fast mode enabled mid-session | status-line `fast_mode` transition false→true |
| `D1.ttl_expired` | idle longer than the session's real TTL | `causes` contains `ttl_expired_5m`, or `warm: false` with `recache_tokens_if_cold` > 0 |
| `D1.image_eviction` | oldest images/PDFs dropped when a request would exceed the image cap | miss with no other diagnosed cause + image count drop |

### D2 — Cache invalidation (expected)

`D2.compaction` and `D2.tool_result_clearing`. Reported separately and **never counted against the hit-ratio gate** — Claude Code already classifies these as `expected_rebuilds`, not `misses`. Counting them as defects produces a metric users learn to ignore.

### D3 — Redundant context

| Code | Meaning |
|---|---|
| `D3.reread` | same file, same byte range, unmodified since last read |
| `D3.blocked_path` | read of a path matching `.contextignore` or a `permissions.deny` rule that should exist |
| `D3.full_read_large` | full read of a file above `bigFileLines` where a `offset`/`limit` read would have served |
| `D3.unbounded_output` | a single `tool_response` above `unboundedOutputTokens` |

### D4 — Startup overhead

`D4.claudemd_bloat`, `D4.skill_listing`, `D4.mcp_unused`, `D4.rules_unscoped`. Paid before the first prompt, on **every** session. Cuts here repay more than anything in D3.

### D5 — Structural

`D5.main_context_exploration` — a long read-only exploration streak in the main conversation that should have been a subagent. Detected, advised once, never blocked.

---

## 4. Data source contract

Each source is versioned and must degrade, not crash, when a field is absent.

### 4.1 Status-line stdin (primary live signal)

Configured via `statusLine` in settings; the script receives a JSON object on stdin and prints to stdout. It runs locally and consumes **no API tokens**. Required reads:

```
context_window.used_percentage
context_window.context_window_size
context_window.current_usage.{input_tokens,
                              output_tokens,
                              cache_creation_input_tokens,
                              cache_read_input_tokens}
prompt_cache.{warm, caching_observed, ttl, expires_at,
              requests, misses, expected_rebuilds, hit_ratio,
              cache_write_tokens, miss_recache_tokens,
              last_miss_at, recache_tokens_if_cold}
prompt_cache.last_miss_cause.{causes[], tools_added, tools_removed, system_char_delta}
prompt_cache.miss_causes{}
model.id
effort.level
fast_mode
session_id
transcript_path
cost.total_cost_usd
rate_limits.five_hour.used_percentage        (subscription only)
```

Version floors produce three distinct capability bands:

| Band | Version | `prompt_cache` | `miss_causes` | Capability |
|---|---|---|---|---|
| 1 — pre-cache | < 2.1.251 | absent | absent | Transcript-derived hit ratio only; all cause attribution suppressed |
| 2 — cache, no causes | 2.1.251–2.1.259 | present | absent | Live hit ratio from status-line; cause attribution suppressed |
| 3 — full | ≥ 2.1.260 | present | present | Full functionality |

**Discriminator rule:** use `miss_causes` field presence (not `last_miss_cause`) to detect the 2.1.260 floor. `miss_causes` is a dict present and non-null from session start on ≥ 2.1.260, even before any miss occurs. `last_miss_cause` is only populated after a miss and is therefore absent in clean sessions regardless of version — using it as a discriminator produces false floor detections. Concretely: `prompt_cache` absent → Band 1; `prompt_cache` present and `miss_causes` absent → Band 2; both present → Band 3.

Absence handling: `current_usage` is `null` before the first API call and again after `/compact` until the next call. `rate_limits` is absent for API-key auth. `recache_tokens_if_cold` is `null` immediately after a compaction. Treat absent ≠ zero.

Sizing: read `COLUMNS` from the environment; `tput cols` does not work inside a status-line script.

### 4.2 Transcript JSONL (ground truth, post-hoc)

`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Assistant entries carry `message.usage` with `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, and `cache_creation` split into `ephemeral_1h_input_tokens` / `ephemeral_5m_input_tokens` — the last of which is how the session's real TTL is determined.

The cwd→directory encoding replaces path separators, colons and spaces. **Implement and unit-test this encoding for Windows explicitly**; it is the single most common source of "no transcripts found" in comparable tools.

Never read a pre-computed `costUSD` field. Always recompute from token counts against the pricing table.

### 4.3 Hook payloads

Common input fields: `session_id`, `transcript_path`, `cwd`, `hook_event_name`. `PreToolUse` adds `tool_name` and `tool_input`; `PostToolUse` adds `tool_response`.

Output contract that matters:
- `PreToolUse` → `hookSpecificOutput.permissionDecision` of `allow` / `deny` / `ask`, plus `permissionDecisionReason`, plus optional `updatedInput` to rewrite the call.
- `PostToolUse` → `hookSpecificOutput.additionalContext` is the **only** field that enters context. Plain stdout on exit 0 goes to the debug log. This is the mechanism that makes I2 enforceable.
- Hook output above 10,000 characters is written to a file and Claude receives a preview plus a path.
- Exit code 2 surfaces stderr to Claude as an error.

### 4.4 Commands (human-facing, not parsed)

`/context`, `/context all`, `/usage`, `/skill-doctor`, `/doctor`, `/insights`. `ccgate` links to these rather than reimplementing them. Specifically: **do not build a CLAUDE.md analyzer** — `/doctor` already emits trim proposals for a checked-in CLAUDE.md, and `/skill-doctor` already reports unused loaded skills and their context cost. `ccgate` gates on their outcomes, not their logic.

### 4.5 API `count_tokens` (Phase 3, Agent SDK path only)

A `count_tokens` call carrying a `context_management` block returns `input_tokens` after clearing and `context_management.original_input_tokens` before it. That difference is a *measured* savings figure, not a heuristic — the only one available. Use it for the `clear_tool_uses_20250919` evaluation in Phase 3.

---

## 5. Components

```
ccgate/
  .claude-plugin/plugin.json
  hooks/hooks.json
  bin/ccgate                     # single dispatcher, one process per invocation
  src/ccgate/
    __init__.py
    dispatch.py                  # arg → handler, lazy imports only
    model.py                     # pricing + window table, TTL resolution
    transcript.py                # JSONL reader, cwd encoding, usage extraction
    taxonomy.py                  # D-codes, single source of truth
    state.py                     # atomic JSON I/O, per-session file locking
    ledger.py                    # net accounting (§6)
    hooks/
      pre_tool.py                # enforcement  (Phase 2)
      post_tool.py               # measurement  (Phase 1)
      session_end.py             # finalize     (Phase 1)
    scripts/
      statusline.py              # live budget + cache line     (Phase 0)
      miss_audit.py              # cause attribution report     (Phase 0)
      shape.py                   # static config lint           (Phase 0)
      baseline.py                # CI startup-overhead gate      (Phase 3)
  schema/
    ccgate.config.schema.json
    ccgate.session.schema.json
    ccgate.report.schema.json
  skills/ccgate/SKILL.md         # /ccgate — report surface, disable-model-invocation: true
  tests/
```

### 5.1 `statusline.py` — live gate (Phase 0)

Single line, ≤ `COLUMNS`. Renders unconditionally:

```
[Opus] ▓▓▓▓▓░░░░░ 52%  ·  cache 91%  ·  $1.24
```

Escalates only on an actionable state:

| Condition | Rendered |
|---|---|
| `warm == false` and `recache_tokens_if_cold` > `coldRecacheWarnTokens` | `COLD — next turn re-caches 312K` |
| `hit_ratio` < `hitRatioFloor` and `requests` ≥ `minRequestsForRatio` | `cache 61% ↓ (tools_changed ×3)` |
| `used_percentage` ≥ `compactAdviseAt` | `→ /compact` |
| `caching_observed == false` after N requests | `NO CACHE REPORTED` |

Must not shell out to `git` on every invocation without a session-keyed temp cache; the script runs on every assistant message with a 300 ms debounce.

### 5.2 `miss_audit.py` — the novel component (Phase 0)

Input: one or more transcript paths, or `--session <id>`, or `--since 7d`.
Output: JSON conforming to `ccgate.report.schema.json`, plus a rendered table.

Algorithm:

1. Parse transcript into an ordered request list with `usage` per request.
2. Mark each request as HIT / MISS / EXPECTED_REBUILD using Claude Code's own thresholds: a request counts as a miss when it re-processed more than 5% **and** at least 2,000 tokens of what it could have read from cache, with no compaction or tool-result clearing to explain the shortfall.
3. For each MISS, attribute a D1 code by joining against (a) `miss_causes` from the status-line snapshot recorded at the nearest `PostToolUse`, and (b) locally observable deltas — model id change, effort change, `fast_mode` transition, idle gap versus resolved TTL.
4. Price each miss: `miss_recache_tokens × rate_in × write_multiplier`, minus what those tokens would have cost as cache reads.
5. Rank by cost. Emit.

Output shape:

```
MISS AUDIT — 6 sessions · 14 Sep – 20 Sep

  cause                      misses   re-cached      cost   fix
  D1.model_switch                 9       2.1M      $10.50   pin model at session start
  D1.ttl_expired                  6       1.4M       $7.00   /compact before stepping away
  D1.tools_changed                4       780K       $3.90   start MCP servers at launch
  D2.compaction                  11       2.9M          —    expected

  avoidable: $21.40 of $48.10 session spend (44%)
```

Determinism requirement: the same transcript set must produce byte-identical output. No timestamps in the body, no wall-clock-dependent bucketing.

### 5.3 `shape.py` — static config lint (Phase 0)

Pure static analysis of settings and memory files. No session data. Checks:

| Check | Rule |
|---|---|
| `claudeMdLines` | each loaded CLAUDE.md ≤ 200 lines |
| `claudeMdExcludes` | in a repo with >1 package and no excludes, flag |
| `denyReads` | flag checked-in `dist/`, `build/`, `vendor/`, `*.generated.*` with no matching `permissions.deny` `Read(...)` rule; patterns must end `/**/*` not `/**` so directories stay listable |
| `skillListing` | `skillListingBudgetFraction` (default 0.01) versus measured total description characters; flag predicted truncation |
| `skillSideEffects` | skills whose name matches deploy/commit/publish/send without `disable-model-invocation: true` |
| `outputCaps` | `bashOutputMaxChars` / `taskOutputMaxChars` set where the session history shows repeated large outputs |
| `cacheTtl` | API-key or cloud-provider auth with `promptCacheTtl` unset → recommend `1h` |
| `worktreeSparse` | `--worktree` in use on a monorepo with no `worktree.sparsePaths` |

Exit non-zero on any `error`-severity finding. Severity per check is configurable; defaults ship in the schema.

### 5.4 `post_tool.py` — silent measurement (Phase 1)

Fires on `Read|Edit|Write|Glob|Grep|Bash|Agent|mcp__*`. Records, per session:

- tool name, real `len(tool_response)`, derived token estimate
- for `Read`: path, offset, limit, mtime, line count
- running per-tool cost profile (mean, max, total) — after three calls a tool is budgeted from its **observed** cost, not a constant. An `mcp__*` "list all issues" call and a one-row lookup share a tool name and differ by two orders of magnitude.
- a snapshot of the last status-line payload if one is on disk

Emits `additionalContext` **only** when a single result exceeds `unboundedOutputTokens`, and at most `maxNoticesPerSession` times. The notice is one line naming the fix (`pipe through tail/grep`, `read with offset/limit`).

Must be race-safe: `post_tool.py` and `statusline.py` both touch session state. Use an atomic write plus a per-session lock file.

### 5.5 `pre_tool.py` — enforcement (Phase 2)

Three deterministic rules, in order:

1. **`.contextignore`** — glob deny, project root then `~/.claude/.contextignore`. On match, `permissionDecision: deny` with a reason naming the pattern and suggesting `Grep`. CRLF must be stripped from patterns before matching, and `\` must match `/` in globs, or the file is silently inert on Windows.
2. **Read cache** — deny when path + byte range are unchanged since last read *in this session*. Allow on: first read, mtime change, non-overlapping range, subagent-originated read (tracked in a separate namespace so a subagent's read is not blocked by the parent's). Re-allow after staleness: `staleTimeMs`, `staleFiles` other files loaded since, or `staleTokenRatio` of the budget loaded since — all scaled to the session's real window size, not a 200K assumption.
3. **Bash output rewriting** — for matched command prefixes, return `updatedInput` with a filter appended. Ships with test-runner and log-grep rules (`pytest`, `cargo test`, `jest`, `go test`, `npm test`, `mvn test`, `grep`, `find`); extensible via config. Prefix matching is **literal only** — no regex. Any `bashRewriteRules` entry whose prefix contains regex metacharacters (`.`, `*`, `+`, `?`, `[`, `(`) is rejected at load time with a clear error message. This is the single highest-yield rule in the component — it converts tens of thousands of tokens of test output into hundreds.

Every deny must be reversible by config, and every deny reason must state how to override it.

---

## 6. Net accounting

This section is the honesty contract. It is also where comparable tools are weakest.

**Primary metric is `tokens_avoided` (raw context tokens not appended).** Report it plainly.

**Do not claim full-price savings on a blocked re-read.** A re-read appends content to a growing, cached prefix. The correct dollar model is:

```
usd_avoided(blocked_read) =
      file_tokens × rate_in × write_multiplier            # the cache write it would have cost
    + file_tokens × rate_in × read_multiplier × turns_remaining_est
```

`turns_remaining_est` is an assumption. It must be recorded in the report payload as `assumptions.turns_remaining_est` with its derivation, and the dollar figure must carry a `~`. A raw-token claim is defensible; an unqualified dollar claim is not.

Algorithm: bootstrap at the constant 5 for the first three turns of a session. Once three or more actual turn gaps are observed, switch to a rolling median of observed turn counts for the current session. Both the bootstrap constant and the switch threshold are recorded in `assumptions.turns_remaining_est`.

**Subtract `ccgate`'s own cost.** Every `additionalContext` string emitted is measured and accumulated as `tokens_injected`. Reports lead with:

```
net = tokens_avoided − tokens_injected
```

If `net < 0`, the headline says `ccgate cost you 4.1K tokens this session` and the report tells the user which rule to turn off. No exceptions, no rounding toward favourable.

---

## 7. Configuration

`~/.ccgate/config.json`, validated against `schema/ccgate.config.schema.json`. Project override at `.ccgate/config.json`; arrays merge, scalars override.

Every value is range-validated. **An out-of-range or misspelled key is ignored in favour of the default and flagged**, never silently applied — a typo must not be able to make a hook behave wildly.

| Key | Default | Effect |
|---|---|---|
| `hitRatioFloor` | 0.85 | status-line escalation and G4 |
| `minRequestsForRatio` | 10 | below this, ratio is noise |
| `coldRecacheWarnTokens` | 100000 | cold-cache warning threshold |
| `compactAdviseAt` | 0.80 | fraction of window before `→ /compact` |
| `unboundedOutputTokens` | 10000 | single-result notice threshold |
| `maxNoticesPerSession` | 4 | hard cap on injected lines (I2) |
| `bigFileLines` | 500 | full-read warning |
| `staleTimeMs` | 600000 | read-cache re-allow after idle |
| `staleFiles` | 8 (at 200K; scaled) | read-cache eviction proxy |
| `staleTokenRatio` | 0.10 | read-cache eviction proxy |
| `readCacheEnabled` | false until Phase 2 | |
| `bashRewriteRules` | `[]` | prefix → filter suffix |

Environment overrides for one-off experiments: `CCGATE_<UPPER_SNAKE>`.

---

## 8. State

```
~/.ccgate/
  config.json
  sessions/<session-id>.json      # per-session measurement + ledger
  locks/<session-id>.lock
  tools.json                      # learned per-tool cost profile
  pricing.json                    # model table, hand-maintained, dated
  reports/                        # miss_audit output
```

Wipeable with `ccgate clean --all`. No file contents are ever stored — paths, byte counts, and token counts only.

---

## 9. Acceptance gates

Each gate is a script that exits 0 or 1. These run in CI on the `ccgate` repo itself and are the definition of done.

| ID | Gate | Threshold | Script |
|---|---|---|---|
| **G1** | Startup overhead | first-turn input tokens in a fixture repo ≤ 12,000 | `baseline.py` |
| **G2** | CLAUDE.md size | every loaded file ≤ 200 lines | `shape.py` |
| **G3** | Skill listing | predicted description chars ≤ `skillListingBudgetFraction` × window chars | `shape.py` |
| **G4** | Cache hit ratio | ≥ 0.85 on synthesised fixture sessions with ≥ 10 requests | `miss_audit.py --assert` |
| **G5** | Avoidable misses | zero `D1.model_switch` or `D1.tools_changed` in synthesised fixture sessions | `miss_audit.py --assert` |
| **G6** | Self-cost | `tokens_injected` ≤ 2% of session input **and** `net > 0` | `ledger.py --assert` |
| **G7** | Hook latency | p95 < 50 ms per invocation over 500 replayed payloads | `tests/perf_hooks.py` |
| **G8** | Determinism | same transcript set → byte-identical `miss_audit` output across 3 runs | `tests/determinism.py` |
| **G9** | Schema conformance | all emitted JSON validates against `schema/*.json` | `tests/schema.py` |
| **G10** | Cross-platform paths | cwd→transcript-dir encoding round-trips on POSIX and Windows fixtures | `tests/paths.py` |
| **G11** | Version floor | Band 1 (absent `prompt_cache`) degrades to transcript fallback; Band 2 (`prompt_cache` present, `miss_causes` absent) serves live hit ratio with cause attribution suppressed — both without traceback | `tests/degrade.py` |

G7 is the one most likely to fail first. Python interpreter startup is 30–50 ms before any work happens. Mitigation is in §11.

**Measurability caveat (2026-09-25).** The 30–50 ms startup figure assumes a clean environment. On the reference Windows machine (Intune-managed, OneDrive-synced tree) interpreter startup *alone* is 1.4–5 s with ±1–2 s run-to-run variance — 28–100× the budget — and Defender tuning cannot reduce it (settled negative; see `BUILD-SPEC-phase2.md` §F0.0). G7 therefore cannot be validated here: its p95 is dominated by spawn + startup noise, not the code under test. Treat G7 as requiring a clean CI environment, and anywhere a rule could be justified by either tokens or time, use tokens — server-independent and noise-free relative to timing.

---

## 10. Phases

Mirrors the read-only-first pattern: prove the measurement before touching behaviour.

**Phase 0 — Read-only introspection.** `statusline.py`, `miss_audit.py`, `shape.py`, `model.py`, `transcript.py`, schemas. No hooks registered. No writes into context. Deliverable: a baseline miss-audit over two weeks of existing transcripts, plus a `shape.py` report on the target repo. Gates: G2, G3, G8, G9, G10, G11.

**Phase 1 — Silent measurement.** `post_tool.py`, `session_end.py`, `state.py`, `ledger.py`, learned tool costs. `additionalContext` limited to the unbounded-output notice. Gates: add G6, G7.

**Phase 2 — Enforcement.** `pre_tool.py`: `.contextignore`, read cache, bash rewriting. Each rule independently toggleable and off by default until its own A/B over recorded sessions shows positive net. Gates: add G4, G5.

**Phase 3 — CI and fleet.** `baseline.py` as a repo gate; pattern digest export/import for team sharing (relative paths and counts only, audited before write *and* on import, absolute paths rejected rather than sanitised); optional OTel reader for `claude_code.token.usage` by `type` label. Evaluate `clear_tool_uses_20250919` via `count_tokens` for any Agent SDK surface. Gates: add G1.

---

## 11. Risks and known failure modes

| Risk | Impact | Mitigation |
|---|---|---|
| **Hook latency** — Python startup on every tool call | fails G7, users disable the plugin | single dispatcher, lazy imports, no schema validation in the hook path, no `git` calls, consider a compiled or resident variant if p95 stays above 50 ms |
| **The tool becomes the cost** | violates I3 | G6 is a hard gate; `maxNoticesPerSession` capped; silent-by-default |
| **Field drift** — `prompt_cache` shape changes | silent wrong numbers | pin version floors, `tests/degrade.py`, treat unknown `causes` values as `D1.unclassified` rather than dropping them |
| **Windows path encoding** | audit reports "no transcripts found" and looks broken | G10, with fixtures, before Phase 0 ships |
| **Read-cache false positives** | blocks a legitimate read, user loses trust permanently | conservative staleness, subagent namespace separation, every deny states its override |
| **Counting expected rebuilds as defects** | metric becomes noise, users stop reading it | D2 is structurally excluded from G4/G5 |
| **Duplicating `/doctor` and `/skill-doctor`** | maintenance burden for no gain | explicitly out of scope per §4.4 |
| **Stale community priors** | e.g. "MCP costs 18K tokens/turn" — tool definitions are deferred by default now, so idle MCP servers are cheap | all thresholds derive from measured session data, never from received wisdom |

---

## 12. Resolved design decisions

1. **`turns_remaining_est`** — rolling session median, bootstrapped at 5 for the first three turns. See §6 for the full algorithm and recording requirements.

2. **Effort-change detection below the version floor** — suppressed entirely. Below the `prompt_cache` version floor, all cause attribution is suppressed regardless of which status-line deltas are observable. Effort-change deltas are still recorded in session state (they are cheap and may be useful for future analysis), but they do not feed the audit report. Misses are filed as `D1.unclassified`, pointing the user toward a Claude Code upgrade as the fix.

3. **Bash rewrite safety** — literal prefix match only. No regex in `bashRewriteRules` prefixes. Any entry containing regex metacharacters is rejected at load time. See §5.5 for the shipped default set.

4. **Fixture sessions for G4/G5** — synthesised JSONL only. No recorded real transcripts in the repo. One fixture per D1 code exercises that miss pattern explicitly; one clean 15-request fixture (consistent model, no effort changes, warm cache) covers G4/G5 directly. A one-time generator script (not shipped) documents how each fixture was constructed.

5. **Scope of the `/ccgate` skill** — one command only. Sub-reports are reached via arguments (`/ccgate audit`, `/ccgate shape`). Config editing is out of scope for the skill entirely: users edit `~/.ccgate/config.json` directly or run `ccgate config set <key> <value>` from the terminal. The skill description stays under ~80 words.

---

## 13. References

Primary sources, all consulted 13 Sep 2026:

- Claude Code — Reduce token usage: `https://code.claude.com/docs/en/costs#reduce-token-usage`
- Claude Code — How Claude Code uses prompt caching: `https://code.claude.com/docs/en/prompt-caching`
- Claude Code — Customize your status line (field reference): `https://code.claude.com/docs/en/statusline`
- Claude Code — Explore the context window: `https://code.claude.com/docs/en/context-window`
- Claude Code — Extend Claude Code (context cost by feature): `https://code.claude.com/docs/en/features-overview`
- Claude Code — Large codebases: `https://code.claude.com/docs/en/large-codebases`
- Claude Code — Hooks reference: `https://code.claude.com/docs/en/hooks`
- Claude Code — Monitoring usage (OTel): `https://code.claude.com/docs/en/monitoring-usage`
- Anthropic — Lessons from building Claude Code: Prompt caching is everything: `https://claude.com/blog/lessons-from-building-claude-code-prompt-caching-is-everything`
- Anthropic — Effective context engineering for AI agents: `https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents`
- Claude Platform — Context editing: `https://platform.claude.com/docs/en/build-with-claude/context-editing`

Prior art reviewed (for what to avoid duplicating):

- `ryoppippi/ccusage` — historical cost from JSONL
- `yorch/cc-analyzer` — session browser, recomputes cost from tokens rather than trusting `costUSD`
- `egorfedorov/claude-context-optimizer` — hook-based read cache, `.contextignore`, net-savings accounting, learned per-tool costs
- `nadimtuhin/claude-token-optimizer` — hook templates for read/bash guards
