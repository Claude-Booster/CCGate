# BUILD-SPEC — Phase 2 / 3 Components

**Companion to:** `BUILD-SPEC.md` v1.0, as amended by `PATCH-010-phases.md`
**Scope:** F1 `contextignoreEnabled` · F2 `bashRewriteEnabled` · F3 `readCacheEnabled` · F4 auto-compact
**Status:** pre-implementation

All four inherit the invariants in `BUILD-SPEC.md` §1.2. Three are restated because they bind hardest here:

- **I2 — silent by default.** A denial reason enters context. Every rule below has a token cost per firing, and that cost is subtracted in the ledger.
- **I3 — net or nothing.** Each component reports its own net independently. A component with negative net is auto-disabled, not merely flagged.
- **I6 → I7 — no component ships enabled.** Each requires its own A/B over recorded sessions.

---

## F0 — Shared machinery

### F0.0 Per-invocation interpreter cost — decide before building F1/F3 (measured 2026-09-24)

F1 and F3 add a `PreToolUse` hook that runs on **every** Read/Edit/Write/Bash. That is a Python process spawn per tool call, and on the reference Windows machine the *interpreter startup alone* — no ccgate work — measured ~1.4 s via a fast shell (PowerShell), and 5–25 s when the interpreter resolved through the WindowsApps App Execution Alias stub or was spawned via Git Bash (MSYS fork emulation). G7's 50 ms budget was written for this hook; ~1.4 s is 28× over it, and that is the *floor* after the alias trap is removed.

Two findings feed this:
- Bare `python` on this machine resolves to a 0-byte WindowsApps alias stub adding ~5 s of AppX activation per launch. `shape` now lints for this (`hookInterpreter` check); the machine-level remedy is toggling the alias off in Windows Settings.
- Even the real interpreter starts in ~1.4 s here (healthy baseline ~200 ms). The excess was suspected to be Defender scanning, but this is now a settled negative (2026-09-25): on this Intune/GP-managed image a `Add-MpPreference` exclusion is silently reverted within ~24 h (the `HKLM\...\Policies\...\Windows Defender\Exclusions` key is GP-owned and reapplied over non-policy additions), UAC elevation switches to a different admin profile so it can't be applied to the user session anyway, and the estimated ~430 ms effect is below the machine's ±1000–2000 ms run-to-run variance (p50 was actually *lower* after the exclusion reverted). Startup is an irreducible ~1.4–5 s here. The dominant variance driver is OneDrive-backed path access — and CCGate's own `src/` tree is OneDrive-synced.

**Decision required before F1/F3:** fresh-Python-per-call is **not viable** for per-tool-call denial on this machine — interpreter startup is 1.4–5 s with ±1–2 s variance, 28–100× over G7's 50 ms budget, and no Defender/exclusion lever exists to reduce it (settled negative, above). F1/F3 require a resident process (daemon/socket) or compiled shim. A1/A3/A4/A5 remain fine as-is — they run once per session or per compaction, where seconds are invisible. A secondary lever worth testing independently: moving the package off the OneDrive-synced tree to cut import-time variance.

**One measurement gates the daemon — take it before writing any code.** Time a no-op `command` hook: a `hooks.json` entry whose command is just `cmd /c exit 0`, no interpreter. This isolates *how Claude Code spawns a hook on this machine* from any interpreter cost. If the no-op costs seconds, the spawn path itself is the bottleneck — likely an MSYS/Git Bash shell (bare `true` through Git Bash measured 5–8 s here) — and no client choice (curl, python, compiled shim) can rescue it: the daemon is dead on arrival and Phase 2 needs a different answer entirely. If the no-op is fast, curl is a sound client and the daemon is worth building. This number cannot be taken in a compaction-continued session (hooks don't reload); measure it in a fresh post-restart session, ideally with `claude --debug` to read the hook timing.

**Daemon note — gated on the no-op number above, NOT a committed decision.** If the no-op is fast: a `command` hook runs a fast-spawning native client (Windows 11 ships `curl`) against a resident stdlib `http.server` daemon that holds state and answers in single-digit ms. There is **no `http` hook type** — hooks are `command`/`prompt`/`agent`, and http/SSE is MCP-transport, not hooks — so a command-hook client against a resident server is the only spawn-avoiding shape available. Before writing code, spec three things: (1) bind loopback-only with a token — a local service that can deny tool calls is a new attack surface; (2) define daemon lifecycle explicitly — who starts it, crash behavior, port conflicts, concurrent projects; (3) ensure read-cache state surviving the process boundary does not worsen the G14 eviction hazard — state outliving a session is exactly how you deny a re-read of content that is already gone.

**A/B methodology for I6→I7 — control the task, not the stopwatch.** Component promotion must not compare one recorded session to another chronologically. The threat is not instrument noise (the token ledger has none) but uncontrolled task variance: a session refactoring 40 files and one debugging a single function differ in `tokens_avoided` by an order of magnitude whether or not the rule is enabled — the same non-comparability that produced the misleading Defender sequence (2598 < 3056). Use paired replay: same fixture transcript, configs interleaved, many trials, report a confidence interval. Name one confound explicitly — if the WindowsApps-alias fix landed anywhere inside a measurement window, it alone explains multi-second drops unrelated to the variable under test.

### F0.1 Intent override (`user_prompt.py`)

Required by F1 and F3. Without it both will eventually deny a file the user explicitly asked for.

`UserPromptSubmit` hook. Extracts from the raw prompt text:

- quoted or backticked path-like tokens
- bare tokens matching `[\w./-]+\.\w{1,8}` that resolve to an existing file under `cwd`
- directory names followed by `/`

Writes to `sessions/<id>.json` under `intent.paths` with the turn index. `pre_tool.py` exempts any path in `intent.paths` from **all** denial rules for `intentTtlTurns` turns (default 3).

Emits nothing into context. Exit 0, empty stdout.

Cost: one hook invocation per user message. Must stay under 15 ms; no filesystem walk, only `os.path.exists` on candidate tokens capped at 40 candidates.

### F0.2 Kill switch

`CCGATE_DISABLE=1` short-circuits every hook before config is read, before state is opened, before any import beyond `os` and `sys`. Exit 0 immediately.

`/ccgate off` writes `{"disabled": true}` to session state for the remainder of the session. `pre_tool.py` checks this before rule evaluation.

Rationale: when this tool misbehaves it misbehaves inside someone's active debugging session. The escape must be instant, obvious, and not require editing JSON.

### F0.3 Denial reason format

Every `permissionDecisionReason` follows one shape:

```
ccgate/<rule>: <what> — <why> — <override>
```

Example:

```
ccgate/contextignore: package-lock.json matches "*-lock.json" — 
lockfiles cost ~14K tokens and are rarely read for content — 
override: /ccgate allow package-lock.json
```

Hard cap `denialReasonMaxChars` (default 200). The reason is context spend; a paragraph of explanation defeats the rule that produced it.

---

## F1 — `contextignoreEnabled`

### F1.1 Purpose

Deny reads of files whose content is predictably worthless in context: lockfiles, minified bundles, sourcemaps, snapshots, generated clients, vendored trees, coverage output.

### F1.2 Scope boundary — read this before building

Claude Code's native `permissions.deny` already does path-based read denial, including via `cat`/`grep` in Bash, at **zero hook cost**. If that covers your case, use it and leave `contextignoreEnabled` off.

F1 exists only for the three things native deny cannot do:

1. **Intent override** (F0.1) — native deny is absolute; a user who genuinely needs the lockfile has to edit settings and restart.
2. **Argument rewriting** rather than outright denial for `Grep` and `Glob` (F1.5).
3. **Attribution** — native deny reports nothing, so its savings never appear in the ledger.

If none of those three matter to you, F1 is strictly worse than `permissions.deny`. Say so in the README.

### F1.3 File format

`.contextignore` at project root, then `~/.claude/.contextignore`. Gitignore syntax subset:

- glob patterns, `**` supported
- `!` negation, evaluated last-match-wins
- `#` comments, blank lines ignored
- trailing `/` means directory

Normalisation before matching, in this order: strip CRLF, strip trailing whitespace, convert `\` to `/`, strip leading `./`. Skipping any of these makes the file silently inert on Windows — the single most common failure in comparable tools.

Shipped default (written to `~/.claude/.contextignore` on first run, never overwritten):

```
*-lock.json
*.lock
*.min.js
*.min.css
*.map
*.snap
dist/
build/
coverage/
vendor/
*.generated.*
*.pb.go
__snapshots__/
```

### F1.4 Read denial

`PreToolUse` matcher `Read`. On match, `permissionDecision: deny` with reason per F0.3, suggesting `Grep` as the alternative when the user likely wants a specific value out of the file.

Bash coverage (`cat`, `head`, `tail`, `less`, `more`) is **not** handled here. Route it through `permissions.deny`, which intercepts Bash reads natively and costs nothing. Duplicating that in a hook adds latency for no gain.

### F1.5 Grep and Glob argument rewriting

Do not deny these. They are cheap discovery calls and denying them breaks legitimate exploration. Instead return `updatedInput` adding exclusions to the tool's own `glob` parameter.

A `Grep` across a repo with a vendored tree returns hundreds of matches from files nobody will read. Rewriting the call to exclude those patterns is pure gain and, unlike denial, does not change what Claude can do — only how much noise comes back.

Cap: if rewriting would produce more than `maxGlobExclusions` (default 12) patterns, skip the rewrite. Long argument strings have their own cost.

### F1.6 Config

| Key | Default |
|---|---|
| `contextignoreEnabled` | `false` |
| `contextignorePaths` | `[".contextignore", "~/.claude/.contextignore"]` |
| `maxGlobExclusions` | `12` |
| `intentTtlTurns` | `3` |

### F1.7 Acceptance

- **G15** zero denials for paths named verbatim in the triggering prompt
- CRLF and backslash fixtures pass on Windows path corpus (extends G10)
- Negation patterns resolve last-match-wins against a 30-case table
- Ledger records `tokens_avoided` using the file's **real** size, read via `os.stat` — never an estimate

---

## F2 — `bashRewriteEnabled`

Highest-yield component in the tool. Also the only one that changes what executes on the machine.

### F2.1 Two rewrite classes, in preference order

**Class A — native quiet flags.** `pytest -q`, `npm test --silent`, `cargo test --quiet`, `go test` without `-v`, `terraform plan -compact-warnings`. Preferred because they do not touch the shell grammar, cannot break a redirect, and cannot mask an exit code.

**Class B — pipe filters.** `2>&1 | tail -n 40`. Used only where no native flag exists.

Always try Class A first. A rule may define both; the Class B filter applies only when the Class A flag is already present in the user's command or is unavailable for that tool.

### F2.2 Exit-code fidelity — mandatory

Appending `| tail -40` in bash makes the pipeline's exit status the exit status of `tail`, which is almost always 0. **Claude then believes failing tests passed.** This is a correctness bug that produces confidently wrong work, not a token-efficiency issue.

Every Class B rewrite must be emitted as:

```
set -o pipefail; <original> 2>&1 | tail -n 40
```

G13 verifies this across 40 fixture commands with known non-zero exits. A Class B rule that cannot be expressed with `pipefail` is not shipped.

### F2.3 Refusal set — hard

Never rewrite a command whose text contains any of:

```
&&   ||   ;   |   >   >>   <   <<   &   `   $(   $((
```

or that spans multiple lines. Compound commands are where rewriting breaks, and the breakage is silent. A command containing any of these is passed through untouched with no log entry.

This is a **refusal**, not a heuristic to be improved later. Widening it requires a new gate, not a config change.

### F2.4 Prefix allowlist, not pattern matching

Per `BUILD-SPEC.md` §12 Q3. A rule matches only when the command's first token — after stripping a leading `env VAR=x` or a package-manager runner prefix (`npx`, `pnpm exec`, `uv run`) — equals a listed prefix exactly.

Rule shape:

```json
{
  "prefix": "pytest",
  "quietFlag": "-q",
  "filter": "2>&1 | tail -n 40",
  "maxLines": 40,
  "notice": "output truncated to last 40 lines; rerun with CCGATE_DISABLE=1 for full output"
}
```

Five verified prefixes that always work beat twenty that mostly do. Ship: `pytest`, `jest`, `go`, `cargo`, `npm`. Everything else is user-added.

`git diff` and `git log` are deliberately excluded from defaults. Their output is frequently the actual subject of the request, and truncating it is the one case where the filter destroys the thing Claude was asked to look at.

### F2.5 Debug-loop escape

If the identical command string has been run within the last `rerunWindowTurns` (default 2) turns, **do not rewrite**. A repeated command means Claude is actively debugging and needs the full output. Rewriting during a debug loop turns a two-turn fix into a six-turn one, and the extra turns cost more than the filter saved.

Tracked in session state by command hash.

### F2.6 Visibility requirement

The rewrite must be disclosed. `permissionDecisionReason` carries the `notice` field so Claude knows output was truncated and how to get the rest. An undisclosed truncation makes Claude debug against a partial picture, which is worse than the original token cost.

### F2.7 Config

| Key | Default |
|---|---|
| `bashRewriteEnabled` | `false` |
| `bashRewriteRules` | five shipped prefixes |
| `rerunWindowTurns` | `2` |
| `defaultMaxLines` | `40` |

### F2.8 Acceptance

- **G12** zero rewrites on the adversarial compound-command corpus (minimum 60 cases)
- **G13** exit-code fidelity across 40 fixtures
- Class A preferred over Class B wherever both are defined
- Rerun escape verified: same command twice in 2 turns → second is untouched
- Ledger measures real `tool_response` length delta against an unfiltered control run

---

## F3 — `readCacheEnabled`

Ships last. Carries the only failure mode in this tool that makes Claude wrong rather than expensive.

### F3.1 Rule

Deny a `Read` when the same path **and** the same byte range has already been read in this session, and `st_mtime_ns` is unchanged since that read.

Allow unconditionally on: first read of the path, mtime change, a range not fully contained in a prior range, and reads originating from a subagent.

### F3.2 Subagent namespacing

Subagent reads are tracked under a separate key derived from `agent.name`. A subagent has its own context window and has not seen the parent's reads. Blocking a subagent's read because the parent already made it starves the subagent of content it genuinely lacks.

### F3.3 Staleness re-allow

Re-allow when **any** of:

- `staleTimeMs` elapsed since the prior read (default 600000)
- `staleFiles` other files read since (default 8, scaled to window size)
- `staleTokenRatio` of the window consumed since (default 0.10)

All three scale against the session's real `context_window_size`, never a hardcoded 200K.

### F3.4 Eviction hazard — the critical section

**A read cache is only valid while the content it is tracking is still in context.** Two events remove content without changing the file on disk:

1. **Compaction.** The summary replaces the transcript. The file contents are gone.
2. **Tool-result clearing.** Claude Code clears old tool results to reclaim space. The read result is gone.

If the cache denies a re-read after either, Claude cannot see the file, has been told it already read it, and will proceed on remembered or invented content. That is a hallucination caused directly by this tool.

Required mitigations, all three:

- **`PreCompact` hook flushes the entire read cache.** Non-optional. `pre_compact.py` truncates `reads` in session state before compaction proceeds.
- **`SessionStart` with `source: resume` or `compact` flushes the read cache.** Covers restarts and resumed sessions where `PreCompact` did not fire in this process.
- **Conservative `staleTokenRatio`.** Tool-result clearing has no hook. The token-ratio trigger is the only defence, which is why it defaults low.

**G14 is the highest-severity gate in the suite.** A failure here is not wasted tokens; it is incorrect output the user has no way to detect.

### F3.5 Denial reason

Must include the turn index of the earlier read, so Claude can locate the content rather than concluding it lacks the file:

```
ccgate/readcache: src/auth.py unchanged since turn 14 — 
content is already in context — override: /ccgate reread src/auth.py
```

### F3.6 Honest yield

Per `BUILD-SPEC.md` §6, a blocked re-read saves one cache write plus a cached-rate tail over the remaining turns — not the file's full token count at full price. Report raw `tokens_avoided` plainly; any dollar figure carries `~` and the recorded `turns_remaining_est`.

Realistically this is the smallest saver of the three Phase 2 components and the largest risk. Build it because it completes the set, not because the numbers demand it.

### F3.7 Config

| Key | Default |
|---|---|
| `readCacheEnabled` | `false` |
| `staleTimeMs` | `600000` |
| `staleFiles` | `8` (scaled) |
| `staleTokenRatio` | `0.10` |
| `subagentNamespacing` | `true` |

### F3.8 Acceptance

- **G14** zero denials for evicted content, across compaction and clearing fixtures
- **G15** intent override honoured
- Subagent reads never blocked by parent reads
- Edit/Write to a path invalidates its entry in the same turn

---

## F4 — Auto-compact

### F4.1 What is actually available

State this plainly in the README, because the obvious expectation is wrong: **a plugin cannot invoke `/compact`.** There is no hook output that runs a slash command. Claude Code compacts on its own schedule, governed by `autoCompactEnabled` and `autoCompactWindow`.

What ccgate can do, in ascending order of ambition:

| Mode | Mechanism | Available where |
|---|---|---|
| `advise` | status-line `→ /compact` at threshold | everywhere (current v1.0 behaviour) |
| `tune` | write `autoCompactWindow` based on measured session economics | everywhere |
| `drive` | own the agent loop and compact explicitly | Agent SDK / headless only |

`tune` is the real deliverable for interactive Claude Code. `drive` is specified for completeness and only applies if ccgate is later embedded in an SDK harness.

### F4.2 Compaction is not free

Compaction forces a full re-cache at write rate — classified `D2.compaction` and excluded from the hit-ratio gate for exactly this reason. It pays only when enough turns remain to amortise that cost against the smaller prefix that follows.

Compacting at 80% with three turns left is pure loss. This is the core insight of the component.

### F4.3 Decision rule

Compact when:

```
projected_savings > recache_cost

where
  recache_cost      = post_compact_tokens × rate_in × write_multiplier
  projected_savings = (current_tokens − post_compact_tokens)
                      × rate_in × read_multiplier
                      × turns_remaining_est
```

`post_compact_tokens` is estimated from the session's own compaction history where available (`transcript.py` can read prior compaction boundaries), otherwise from `compactionRatioDefault` (0.30).

This inherits the unresolved `turns_remaining_est` from §6 and §12 Q1. **Phase 3 does not start until Q1 is settled.** Every number above is downstream of it.

### F4.4 `tune` mode

Each session end, `session_end.py` recomputes the break-even threshold from the session's observed length distribution and writes `autoCompactWindow` into `~/.claude/settings.json`.

Guards, all required:

- Never write more than once per `tuneCooldownHours` (default 24) — thrashing the setting invalidates the system-prompt cache layer
- Never move the threshold by more than `tuneMaxDelta` (default 0.05) per adjustment
- Clamp to `[0.5, 0.92]`
- Write a `ccgate.tuned` marker alongside so a human can tell what changed the setting
- `tune` is opt-in; `advise` remains the default

### F4.5 `PreCompact` instruction injection

Independent of mode and worth shipping on its own. The `PreCompact` hook can supply custom compaction instructions, which is the difference between a summary that preserves the task and one that loses it.

Ship a default instruction set that preserves: the active plan or task list, file paths under edit, decisions already made and rejected, and the failing-test state if any. Configurable via `compactInstructions`.

### F4.6 Durable task state

Independent of compaction mechanics and arguably the highest-value piece of F4.

`state.py` maintains `sessions/<id>.task.md` on disk — the current plan, completed steps, open decisions. `SessionStart` with `source: compact` re-injects it via `additionalContext`.

This makes compaction lossless for the things that matter, which in turn makes earlier and more frequent compaction safe. Prior art (`claude-context-optimizer`) validates the pattern.

Cap the re-injection at `taskStateMaxTokens` (default 800). It is charged to the ledger like any other injection.

### F4.7 Config

| Key | Default |
|---|---|
| `autoCompactMode` | `"advise"` |
| `compactionRatioDefault` | `0.30` |
| `tuneCooldownHours` | `24` |
| `tuneMaxDelta` | `0.05` |
| `compactInstructions` | shipped default |
| `taskStateMaxTokens` | `800` |

### F4.8 Acceptance

- **G16** `drive` never fires below the amortisation threshold
- **G17** task state survives a compaction round-trip byte-identical
- `tune` respects cooldown, delta cap, and clamp across a 200-session replay
- `PreCompact` instructions appear in the resulting summary (fixture assertion)

---

## Build order within Phase 2

```
F0.1 intent override      ── required by F1 and F3, build first
F0.2 kill switch          ── required by everything
F0.3 reason format        ── required by everything
      ↓
F2 bash rewriting         ── highest yield, ship and measure
      ↓
F1 contextignore          ── verify it beats plain permissions.deny first
      ↓
F3 read cache             ── only after G14 fixtures exist
      ↓
F4.5 + F4.6               ── PreCompact instructions and task state
F4.4 tune                 ── only after §12 Q1 is resolved
```

F0 before anything. F3 only after its eviction fixtures are written — not after the feature works, after the fixtures exist.

---

## Open questions added by this spec

6. **F1 scope** — if `permissions.deny` covers your repo, does F1 justify its hook cost at all? Measure before building.
7. **F2 default prefix list** — five shipped, or zero shipped and fully user-defined? Zero is safer and slower to adopt.
8. **F4.6 task state** — is durable task state better shipped standalone in Phase 1, decoupled from compaction economics entirely? It has independent value and no dependency on Q1.
