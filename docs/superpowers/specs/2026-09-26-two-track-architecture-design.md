# ccgate Two-Track Architecture — Design

**Date:** 2026-09-26
**Status:** design, pending user review
**Supersedes:** the Track A/B definitions in `docs/IMPL-SPEC-automation.md` §6 and the
enforcement-via-hooks premise throughout `docs/BUILD-SPEC.md` §5, §10. The invariants
(I1–I6), the cost model (§2), the D-code taxonomy, and `docs/BUILD-SPEC.md` §12.6–12.9
remain authoritative and are referenced, not restated, here.

---

## 1. Why this document exists

ccgate was architected around Claude Code hooks. On the reference machine, an
enterprise org policy (`allowManagedHooksOnly`, `policy-limits.json.stamp.json`:
`kind="org"`) blocks **every settings-derived execution channel** — all five hook
events *and* `statusLine`. Verified 2026-09-26 by failure-first probe, not inference
(`docs/BUILD-SPEC.md` §12.9). Escalation to the org admin (Option D) runs in parallel
but is not assumed.

That single fact splits the product along a line the policy drew:

- **Measurement can stay in the interactive surface**, because it only needs to *read*
  files the policy does not govern (the transcript JSONL).
- **Enforcement cannot stay in the interactive surface at all.** Nothing there can
  intercept a tool call, redirect a model switch, or act at a compaction boundary once
  hooks are gone.

The old "Track A = A1–A5 / Track B = SDK runner" split assumed hooks worked. It no
longer holds. This document redefines the two tracks and relocates the automations
accordingly.

## 2. The two tracks (new definitions)

| | **Track A — Measurement** | **Track B — Enforcement** |
|---|---|---|
| Runs in | interactive Claude Code (your daily sessions) | a ccgate-owned Agent SDK loop |
| Mechanism | on-demand CLI reads of transcript JSONL | `claude-agent-sdk` 0.2.128 + programmatic hooks / client methods |
| Can intercept? | no — read-only, post-hoc | yes — verified deny enforced (`BUILD-SPEC.md` §12.8) |
| Freshness | ~15s transcript lag; on demand only | real-time within the loop |
| Blocked by org policy? | no (reads unmanaged files) | no (SDK hooks are code-supplied, not settings-derived) |
| Ledger source | `usage` blocks (ground truth, I4) | `usage` blocks + live `get_context_usage()` |

**A third, smaller body of work sits above both:** config remediation (A1, A3). It is
neither measurement nor an owned loop — it is static settings hygiene via
`shape --fix`. It is config-only, unaffected by the policy, already largely shipped,
and is the highest-ROI work in the project. It ships independently and waits for
neither track (§7).

## 3. What each track contains

### Track A — Measurement (interactive, read-only)

Ports the existing read-only components to run as on-demand CLI over the transcript,
with the ledger re-based on ground truth:

- `transcript.py` — JSONL reader, cwd→dir encoding, `usage`-block extraction. Already
  built and hardened (four review rounds against a real 24 MB transcript; still valid).
- `miss_audit.py` — cause attribution report. Unchanged in logic; now the primary
  Track A deliverable.
- Session report + `ledger.py` — **re-baselined on `usage` blocks** (§5).
- `shape.py` — config lint/report (also hosts A1/A3 remediation, §7).

**No resident watcher.** The transcript lags ~15s and, with `statusLine` blocked, no
live surface consumes sub-minute freshness. A daemon would add lifecycle ownership,
lock contention, and the G14 cross-session eviction hazard for no consumer. Track A is
on-demand CLI reads. (`BUILD-SPEC.md` §12.7.)

### Track B — Enforcement (owned Agent SDK loop)

A separate entry point — `ccgate run` — that hosts a task inside a ccgate-controlled
`ClaudeSDKClient`, where ccgate owns the tool-call boundary. Relocates the automations
that require interception, and hosts Phase 2 enforcement (F1–F3):

- **A2 — model economics.** *Relocated and upgraded.* In the interactive surface A2
  was a `PreModelSwitch` guard that could only **deny** (no `updatedModel` field,
  confirmed impossible — §8). In the owned loop, ccgate calls **`client.set_model()`**
  at task boundaries: it can *choose* the model, not merely veto a switch. Port the A2
  pricing logic (`IMPL-SPEC-automation.md` §A2 steps 1–7 verbatim, including step 6:
  never block/​downgrade a capability upgrade); drop the `PreModelSwitch` delivery.
- **A4 — compaction recovery.** *Relocated.* Port `_extract_from_transcript`
  (`pre_compact.py:40`, hardened, still valid) unchanged — the extraction logic is not
  in question. The **delivery** is the open risk. Two findings:
  - **Server-side compaction is not available in this SDK.** `claude-agent-sdk` 0.2.128
    exposes no `compact_20260112` beta and no compaction-instruction parameter — only
    `/compact` as a slash command and the `PreCompact` hook. The original spec's
    `sdkCompactStrategy: "server"` default (`IMPL-SPEC-automation.md` §3) is therefore
    **unreachable** on this SDK version. Revisit if a later SDK adds it.
  - **`/compact <instructions>` delivery — VERIFIED 2026-09-26 (§12.9, red→green).** A4's
    Track B delivery calls `_extract` at a boundary and passes the result to `/compact`
    as instructions. Verified by ground-truth transcript observation: a `/compact`
    carrying an instruction to begin the summary with a distinctive marker token produced
    a post-compaction summary that **began with that exact token** and preserved the
    instructed file path (marker×3, path×7 in the transcript; real compaction,
    `pre_tokens 17686 → post_tokens 1762`). An effect that cannot occur by default, so the
    instructions demonstrably reach the summarizer.
  - **Observability for Track B.** The compaction result surfaces as a `SystemMessage`
    subtype `compact_boundary` (carrying `compact_metadata`: pre/post tokens, dropped,
    duration) followed by a `UserMessage` whose content *is* the summary. The
    `ResultMessage` for a `/compact` turn is empty — do not read the summary from it.
    Track B reads the `compact_boundary` message (or the on-disk transcript entry of the
    same name) to confirm compaction and to ledger `tokens_measured` from the pre/post
    delta.
- **A5 — compaction economics.** The break-even rule (`IMPL-SPEC-automation.md` §A5)
  moves here from `statusline.py` (blocked). The loop can *act* on the break-even
  signal (trigger `/compact` when `projected_savings > recache_cost`), not just advise.
- **F1–F3 enforcement** (`.contextignore` denial, read cache, Bash rewriting —
  `BUILD-SPEC.md` §5.5) run as a programmatic `PreToolUse` hook returning
  `permissionDecision: "deny"` / `updatedInput`. Verified firing and enforced
  (§12.8).

**Shadowing rules that bind Track B's implementation** (`BUILD-SPEC.md` §12.8): naming
a tool in `allowed_tools` auto-approves it *before* `can_use_tool`; settings-file allow
rules shadow `can_use_tool` invisibly. Track B must pass `setting_sources=[]` and gate
via a `PreToolUse` hook, not `can_use_tool`, whenever it must see every call.

## 4. What interactive keeps and loses — in prevention-model terms

The right frame is the prevention model from `IMPL-SPEC-automation.md` §0.1/§1:
automation means **removing the possibility** of a defect, in one of three ways —
**ELIMINATE** (config, zero runtime cost), **PREVENT** (interception denies the
action), **RECOVER** (the defect is unavoidable; make it cheap). The org policy cuts
cleanly along that taxonomy.

**Interactive sessions keep ELIMINATE — in full.** ELIMINATE is config; config is not
a settings-*derived execution channel*, so the policy does not touch it. Every daily
session still gets:

| Driver | Eliminated by | Channel |
|---|---|---|
| `D1.ttl_expired` | `ENABLE_PROMPT_CACHING_1H` + `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` | config |
| `D1.tools_changed` | tool deferral; lint `alwaysLoad` out of `.mcp.json` (A3) | config |
| D4 startup overhead | `claudeMdExcludes`, deny rules | config |

That is **three of the four D1 drivers, automatic, every interactive session, with no
execution channel required.** This is not "auditing, nothing more" — it is real,
compounding, always-on savings that the policy cannot reach.

**Interactive sessions lose PREVENT and RECOVER.** Both need an execution channel the
policy blocks:

- **PREVENT** — `D1.model_switch` (was the `PreModelSwitch` guard). No interception in
  interactive → relocates to Track B (§3).
- **RECOVER** — `D1.compaction` timing (was `PreCompact`/`SessionStart`). No
  compaction action in interactive → relocates to Track B (§3).

So the honest one-line statement of the trade: **interactive keeps ELIMINATE; PREVENT
and RECOVER move to the owned loop and apply only to `ccgate run` work.** The README
and reports must state it in exactly these terms — it is both more accurate and more
actionable than implying either full coverage or mere auditing.

## 5. Ledger re-baseline (ground truth) and the I7 boundary

`chars // 4` was a workaround for hooks not seeing `usage` blocks. Track A reads them
directly, satisfying I4 properly for the first time. Consequently:

- All token accounting recomputes from `usage` blocks: `input_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, and the
  `ephemeral_1h`/`ephemeral_5m` split for real TTL.
- **Any net figure computed earlier in this project was derived from estimates and is
  discarded, not carried forward.** The first baseline capture (§7) establishes the
  true numbers.
- The character-count path remains only as the I4 labelled fallback, `~`-prefixed
  wherever surfaced.

**I7 boundary — Track A itself alters no behaviour, so it claims no savings of its
own.** Use the three ledger categories from `IMPL-SPEC-automation.md` §4, reported
separately (collapsing them is a G24 violation):

| Category | Who earns it | Track A's role |
|---|---|---|
| `tokens_prevented` | config remediation A1/A3 (ELIMINATE) | **reports** it — counterfactual, baseline vs post-fix startup cost, `~`-prefixed with baseline in `assumptions` |
| `tokens_avoided` | runtime denials A2/F1–F3 (Track B only) | **reports zero** — Track A denies nothing |
| `tokens_measured` | server-side compaction (Track B only) | **reports zero** — Track A measures no server delta |

Track A is a reporter, not an actor. The `tokens_prevented` it surfaces is earned by
the config remediation, not by Track A; I7 is satisfied because Track A never attributes
a saving to itself. `tokens_injected` is subtracted from all three; per I3 a negative
net says so in the headline.

## 6. Component disposition (reconciliation table)

| Item | Built as | Now | Track |
|---|---|---|---|
| A1 config pin | config remediation + `session_start` assertion hook | config part survives; **assertion hook is dead** — drop it or restate as a Track A audit check | config remediation / A |
| A3 tool deferral | `shape` lint + `shape --fix` | survives unchanged | config remediation / A |
| A4 compaction recovery | `PreCompact` + `SessionStart` hooks | hooks dead; **port `_extract`, deliver via `/compact` in loop** | B |
| A2 model guard | not yet built (planned `PreModelSwitch`) | build in loop via `set_model()` | B |
| A5 compaction economics | planned `statusline` change | statusline dead; move break-even rule to loop | B |
| F1–F3 enforcement | planned `pre_tool` hook | build as loop `PreToolUse` hook | B |
| miss_audit / report / transcript | read-only | ledger re-baselined on `usage` | A |

Dead code to remove or repurpose: the `session_start` A1 assertion hook, and the
`PreCompact`/`SessionStart` A4 hook wiring (the *logic* in `_extract_from_transcript`
is retained and ported).

## 7. A1/A3 ship now — but baseline must come first

A1 (config pin) and A3 (tool deferral) are pure config remediation via `shape --fix`,
need no execution channel, and are where the compounding savings are (A3's
`ENABLE_TOOL_SEARCH` check alone avoids 100K+ tokens of tool definitions per
MCP-heavy session). Already implemented (53 config tests pass). This does not wait for
either track's plan — **but it has a hard ordering constraint.**

**Capture the baseline before remediating.** `tokens_prevented` is counterfactual — it
needs a *before*. `miss_audit` already reads existing transcripts, so the baseline is a
**today task, not a Track A deliverable**: run `miss_audit` over current transcripts and
record the pre-remediation startup cost and D1 driver frequencies. If A1/A3 remediation
lands first, what they saved becomes unmeasurable forever.

**Then verify the remediation actually took effect — per §12.9, do not assume it.** The
policy has already silently killed two settings-derived channels; assuming the settings
`env` block survives is the exact inference pattern §12.9 forbids. It is red→green
testable without `statusLine`: in the transcript `usage` blocks, compare
`cache_creation.ephemeral_5m_input_tokens` vs `ephemeral_1h_input_tokens` before and
after remediation. If it stays 5m, A1's cache pin is **inert** and nothing else would
ever reveal it. A1 counts as working only once a session shows the 1h bucket populated.

Ordered steps (all pre-Track-A):

1. Baseline: `miss_audit` over existing transcripts → record startup cost + D1 freqs.
2. Apply A1/A3 remediation (`shape --fix`).
3. Verify: transcript shows `ephemeral_1h_input_tokens` populated (not 5m). Red→green.
4. Cleanup: drop A1's dead `session_start` assertion hook; fix the
   `datetime.utcnow()` deprecation at `shape.py:856`.

## 8. What remains impossible (preserved from IMPL-SPEC §7)

Document in README; do not attempt. Confirmed by upstream issues:

1. Triggering `/compact`, `/clear`, or a model switch from *inside an interactive
   session* — by hook, slash command, skill, MCP tool, or env var
   (anthropics/claude-code #58538, #37307, #66246; #65586 NOT_PLANNED). *Note: the
   owned loop sidesteps this — it is not "inside an interactive session."*
2. Redirecting a model switch via `PreModelSwitch` (block only, no `updatedModel`).
   *Track B sidesteps this via `set_model()`.*
3. Disabling native microcompact (#7176).
4. Keepalive pings to hold a cache warm from inside an interactive session.

## 9. Acceptance discipline (binds every gate below)

Per `BUILD-SPEC.md` §12.9: a channel, hook, or reader counts as "working" **only after
its failure mode has been produced and observed, then a fix shown to flip it.** A
passing test whose red state was never witnessed is not evidence. This applies to every
G-gate that asserts a channel works — Track B's `PreToolUse` enforcement gate above all,
since a silently-shadowed callback looks identical to a working one until you force a
denial and watch it hold.

## 10. Resolved decisions (were open questions)

- **Track B UX.** `ccgate run --task <file>`, streaming stdout, writes its own session
  record. It does **not** mimic an interactive session — it isn't one, and blurring that
  would confuse which sessions actually had enforcement. Its session records are
  distinguishable from interactive transcripts by construction.
- **A5 auto-acting — off by default.** Ship A5 *logging* the break-even decision without
  acting, compare its decisions against what native auto-compact actually did, then
  enable acting only on that evidence. Blocking auto-compact with no replacement risks a
  hard context-limit failure, and §12.9 forbids claiming it works before watching it
  fail first.
- **Sequencing (whole project).** baseline (today, existing transcripts) → A1/A3
  remediation → verify the `env` block took effect (§7 step 3) → Track A → Track B. This
  preserves the counterfactual baseline and mirrors the original "measure before enforce"
  order.

## 11. Plan split

Two plans, per the sequencing above:

- **Plan 1 — "A1/A3 finish + baseline + dead-hook cleanup."** Small, separable, and it
  unblocks the baseline work. Contains: baseline capture, remediation verification (§7),
  A1 assertion-hook removal, A4/`session_start` dead-hook removal (logic ported later),
  the `datetime.utcnow()` fix. Ships first.
- **Plan 2 — "Track A + Track B."** The measurement re-baseline and the owned loop. A
  different size; must not be held up by Plan 1.
