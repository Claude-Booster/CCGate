# Track B / B1b — F3 Bash Output Truncation — Design

**Date:** 2026-09-29
**Status:** design, pending user review
**Parent:** `docs/superpowers/specs/2026-09-26-two-track-architecture-design.md` (§3 Track B);
`docs/superpowers/specs/2026-09-28-track-b-b0-run-harness-design.md` (B0, the harness this
extends). **Supersedes** `docs/BUILD-SPEC.md` §5.5 rule 3's *mechanism* for F3 (see §2).
**Reuses:** B0's `ccgate.run` package, `record.RunRecorder`, `transcript.read_transcript`,
`config.load_config`.

---

## 1. Why this doc / decomposition

"Full F1–F3" (parent §3) is three independent enforcement rules plus net-accounting — too
big for one spec. This spec covers **B1b only — F3, Bash output truncation** (BUILD-SPEC
§5.5 rule 3), chosen first because it is the spec's "single highest-yield rule" and is
self-contained. Siblings, each their own spec→plan:

- **B1a** — F1 full `.contextignore` (extend B0's thin slice: two locations, CRLF/`\`↔`/`, Grep hint).
- **B1c** — F2 read cache (stateful; carries the `turns_remaining_est` accounting).

## 2. Mechanism — PostToolUse output truncation (supersedes BUILD-SPEC §5.5 rule 3)

**Ruling:** F3 truncates the tool **output** via a `PostToolUse` hook returning
`updatedToolOutput`, **not** by rewriting the command via `PreToolUse` `updatedInput` as
§5.5 described. Rationale — and why this is strictly better than the written spec:

- The command executes **exactly as the model wrote it** — no shell-grammar parsing, so
  §5.5's Class-A/Class-B compound-command refusals and the G13 exit-code-fidelity problem
  **disappear** (we never touch the pipeline).
- Works for **any** command, not just pipe-friendly ones.
- **`chars_elided` is exact** — the hook sees the full output and the truncated output in
  the same call, so the saving is *measured*, not a `turns_remaining_est` counterfactual.

The SDK confirms `PostToolUseHookSpecificOutput.updatedToolOutput` exists and, for Bash,
must match the tool's output schema `{"stdout", "stderr", "interrupted"}`. BUILD-SPEC §5.5
gets a "superseded-by this doc for F3" note; its `bashRewriteEnabled`/`bashRewriteRules`
config keys (Approach A) are flagged for removal in a later B1 cleanup, not used here.

## 3. Shape isolation (the design is insensitive to an unverifiable unknown)

The **input** the PostToolUse hook receives (`input_data`'s `tool_response`) is **not
definitively documented, and cannot be verified on-desk** — the org sandbox blocks Bash
entirely (verified 2026-09-29: a probe got **zero** PostToolUse events; the model *faked*
the tool call as text, producing a plausible result with nothing behind it — the same
silent-wrong-answer class as the −114,073 total and the `__main__` guard). So the design is
made **insensitive** to the unknown rather than guessing:

- **`bashcap.truncate(stdout: str, head: int, tail: int) -> tuple[str, int]` is pure and
  str-based.** Its signature cannot be wrong regardless of what the probe eventually finds.
- The shape-dependent glue is a thin **adapter** that pulls the stdout string out of
  `input_data` and repacks the result into `updatedToolOutput`. It is **defensive** (§8) and
  its exact form is confirmed by a **CI probe that prints the raw `input_data`** (§7) — the
  probe *prints*, it does not assert on a parsed field, precisely because the sandbox showed
  that a wrong assumption fails silently rather than loudly.

## 4. Truncation logic — pure module `src/ccgate/run/bashcap.py`

- **`compile_prefixes(prefixes: list[str]) -> list[str]`** — validates at load: a prefix
  containing a regex metacharacter (`.` `*` `+` `?` `[` `(`) is **rejected with a clear
  error** (BUILD-SPEC §433 safety — literal-prefix matching only). Returns the clean list.
- **`command_matches(command: str, prefixes: list[str]) -> bool`** — the command string
  starts with any configured prefix.
- **`truncate(stdout: str, head: int, tail: int) -> tuple[str, int]`** — if
  `len(stdout) <= head + tail`, return `(stdout, 0)` (no change). Else return
  `(stdout[:head] + MARKER + stdout[-tail:], elided)` where `elided = len(stdout) - head - tail`
  and MARKER (§6) names what was cut and from where.

## 5. Debug-loop escape (per BUILD-SPEC-phase2 §F2.5)

If the **identical** command (compared by hash) ran within the last `bashCapDebugLoopCalls`
(default 3) **Bash tool-calls**, **do not truncate** — a repeat means active debugging and the
model needs full output; truncating a debug loop turns a 2-turn fix into 6 and costs more
than it saves. The window is counted in **Bash calls, not conversational turns** — a
test-fix loop can fire several Bash calls inside one turn, and call-indexing is both simpler
and the better fit (the config key is named `…Calls` so it does not claim otherwise). State
is per-run: `{command_hash: last_bash_call_index}`, held by the hook wrapper (not the pure
module). Each Bash call increments the index. **Count the skips:** a
per-run counter `debug_loop_skips`, reported alongside `chars_elided` (§6) — if the escape
fires often it is eating F3's value and the operator needs to see that.

## 6. Recording — `tokens_avoided` substrate + honesty

Each **truncation** appends one line to the run record:
`{"type":"ccgate_event","rule":"F3","command_prefix":p,"full_chars":F,"kept_chars":K,`
`"chars_elided":E,"tokens_elided_est":E//4}`. `read_transcript` ignores non-`assistant`
lines (proven in B0), so usage counts are untouched.

- **`tokens_elided_est` is a `~` estimate wherever surfaced** (I4 — it is `chars//4`, not a
  measured token count); `chars_elided` is exact and is the primary figure.
- The **MARKER's own characters are `tokens_injected`** — the honest net for F3 is
  `tokens_avoided (≈ chars_elided//4) − tokens_injected (marker)`, and per I3 a net that
  comes out small or negative is reported as such, never rounded toward favourable.
- At run end, the record also carries a summary line
  `{"type":"ccgate_event","rule":"F3","summary":true,"truncations":n,"debug_loop_skips":s,`
  `"total_chars_elided":T}` so a reader sees both what F3 saved and how often the escape suppressed it.

## 7. Wiring & verification

**Wiring (`cli.py`):** when `bashCapEnabled`, add `"Bash"` to `tools` + `allowed_tools` and
register the PostToolUse cap hook; B0's F1 Read-deny is unchanged. `--no-enforce` disables
**all** enforcement (F1 + F3) for the measurement baseline.

**In-process (agent-runnable, no SDK):**
- `bashcap` pure logic: prefix match; load-time regex-metachar rejection; `truncate`
  head/tail/marker with **exact `chars_elided`**; under-budget and non-matching passthrough.
- **`truncate` boundary cases** (cheap, and it has arithmetic): `head + tail >= len(stdout)`
  is the no-change path (`chars_elided == 0`, output identical); `head == 0` still produces
  valid output (tail + marker only, no negative slice); `tail == 0` symmetric.
- debug-loop escape: repeat within N calls → no truncation; skip counter increments.
- adapter unit tests over synthetic `input_data` dicts (dict-with-stdout → truncated;
  str `tool_response` → truncated; **dict-without-stdout → passthrough, no event**, §8).
- fake-client wiring: PostToolUse hook registered + Bash in tools **only when enabled**; a
  truncation event is recorded.

**Real (CI `workflow_dispatch` / user terminal — the required proof, §12.9 red→green):**
A **separate** `scripts/b1b_ci_probe.py` + its own `workflow_dispatch` workflow — **not** an
extension of B0's `b0_ci_probe.py`. B0's CI run is now the reference proof for the whole
harness; keeping F3's probe separate means B1b churn can't break B0's green.
- **First step prints the raw `input_data`** of one Bash call (shape confirmation — print,
  don't assert), then the adapter is confirmed/adjusted against it.
- A prefixed command emitting >budget stdout, run **without** the hook (record/model sees
  full) vs **with** it (truncated + marker); assert `chars_elided > 0` and the marker present.
- Uses `CLAUDE_CODE_OAUTH_TOKEN` (B0's proven CI pattern).

**Sequencing / honesty note (state plainly — do not let CI-green read as desk-green):**
On-desk, `ccgate run` **with Bash enabled is UNVERIFIED — not broken, unverified.** The org
sandbox blocks Bash there, so F3 (like B0's deny) is proven **nowhere but CI**. That is now
**two** Track-B features whose enforcement lives only behind the CI token principal
([[project_track_b_b0]], [[project_org_policy_blocks_hooks]]). Anyone reading a green CI run
must not conclude the feature works on an interactive-login desk; it is untested there by
construction.

## 8. Error handling (adapter fail-open, precisely)

The adapter **never truncates something it guessed at** — it passes the output through
untouched and records nothing unless it positively recognizes the stdout string:

- `tool_name != "Bash"` → passthrough (`{}`).
- `tool_response` is a **str** → treat it as stdout; truncate; repack (shape confirmed in CI).
- `tool_response` is a **dict with a string `stdout`** → truncate that; preserve `stderr` /
  `interrupted` verbatim.
- `tool_response` is a **dict without a recognizable `stdout`** (or any other shape) →
  **passthrough, record nothing.** An unrecognized shape is never truncated on a guess.
- Any exception in the adapter → passthrough (fail-open; never crash the run).
- Bad config prefix → rejected at **load** (fail-closed on config; never a wild runtime rewrite).

## 9. Config (off by default, independently toggleable — parent §405)

Add to `config.DEFAULTS` / `_RANGE`:

| Key | Default | Range | Meaning |
|---|---|---|---|
| `bashCapEnabled` | `False` | — | master toggle for F3 |
| `bashCapHeadChars` | `4000` | 200–200,000 | chars kept from the start |
| `bashCapTailChars` | `12000` | 200–200,000 | chars kept from the end (verdict/summary) |
| `bashCapDebugLoopCalls` | `3` | 0–10,000 | repeat-within-N-**Bash-calls** → skip truncation (0 disables the escape) |
| `bashCapPrefixes` | `[pytest, cargo test, jest, go test, npm test, mvn test]` | list (merged like `bashRewriteRules`) | commands to cap; **grep/find dropped** (uniform, head-useful output — see review) |

**Range floors/ceilings are load-bearing, not cosmetic** (same reasoning as `taskStateMaxTokens`'s
non-zero floor): a `0` head+tail truncates everything to just the marker, and a 10M head+tail
means the threshold (`head+tail`) never trips and F3 silently never fires — two ways a config
typo makes the feature useless in opposite directions. The `200–200,000` bounds keep the knob
in the range where it means something.

Truncation triggers when a matched command's stdout exceeds `head + tail` (16,000 default).
The 25/75 head:tail split favours the tail because every shipped prefix is a test runner
whose verdict is at the end; per-prefix splits are deferred (YAGNI).

## 10. Components

| File | Responsibility | SDK-free? |
|---|---|---|
| `src/ccgate/run/bashcap.py` (new) | pure: `compile_prefixes`, `command_matches`, `truncate`, `MARKER` | yes |
| `src/ccgate/run/cli.py` (modify) | build the PostToolUse hook (adapter + debug-loop state + recording); add Bash to tools when enabled | adapter is SDK-shape-facing but unit-tested over synthetic dicts |
| `src/ccgate/config.py` (modify) | add the `bashCap*` defaults + ranges + prefix list-merge | yes |
| `scripts/b1b_ci_probe.py` (new) + `.github/workflows/b1b-*.yml` | CI: print raw `input_data`, then red→green truncation check. Separate from B0's probe (keeps B0's reference proof stable) | n/a (CI) |
| `src/ccgate/run/record.py` (reuse) | `RunRecorder` gains an `append_event(dict)` for `ccgate_event` lines | yes |

## 11. Deferred
B1a (F1 full), B1c (F2 read cache), the I7 `tokens_avoided` ledger/report that sums F3
events (a Track A `audit` extension), per-prefix head/tail, and removing the legacy
`bashRewrite*` config keys.
