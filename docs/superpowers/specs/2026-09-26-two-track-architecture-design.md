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
- **A4 — compaction recovery.** *Relocated and upgraded.* Port `_extract_from_transcript`
  (`pre_compact.py:40`, hardened, still valid) unchanged. In the owned loop, ccgate
  calls `_extract` at a chosen compaction boundary and hands the result **directly to
  `/compact` as instructions**, rather than writing `sessions/<id>.task.md` to disk and
  depending on `SessionStart` re-injection that can no longer fire. Same extraction,
  better delivery.
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

## 4. What Track B costs — stated plainly

Enforcement applies **only to work done inside ccgate's owned loop** (`ccgate run`).
Your interactive Claude Code sessions — the ones you spend the day in — get **config
remediation (A1/A3) and post-hoc auditing (Track A), nothing more.** No tool-call
denial, no model redirection, no compaction action in those sessions. That is the real
trade the org policy imposed. The two-track structure must not be read to imply full
coverage of interactive work; it does not, and cannot, while the policy stands.

This is a genuine scope reduction versus the original all-in-the-surface vision, and
the README and reports must say so rather than let the architecture imply otherwise.

## 5. Ledger re-baseline (ground truth)

`chars // 4` was a workaround for hooks not seeing `usage` blocks. Track A reads them
directly, satisfying I4 properly for the first time. Consequently:

- All token accounting recomputes from `usage` blocks: `input_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, and the
  `ephemeral_1h`/`ephemeral_5m` split for real TTL.
- **Any net figure computed earlier in this project was derived from estimates and is
  discarded, not carried forward.** The first Track A run establishes the true
  baseline. Per I3, net stays net-of-ccgate's-own-injected-tokens; in Track A that
  injection is zero (read-only), so Track A net = gross savings observed.
- The character-count path remains only as the I4 labelled fallback, `~`-prefixed
  wherever surfaced.

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

## 7. A1/A3 ship now, independent of both tracks

A1 (config pin) and A3 (tool deferral) are pure config remediation via `shape --fix`,
need no execution channel, and are where the compounding savings are (A3's
`ENABLE_TOOL_SEARCH` check alone avoids 100K+ tokens of tool definitions per
MCP-heavy session). They are already implemented (53 config tests pass). Remaining
work is small: drop A1's dead assertion hook, fix the `datetime.utcnow()` deprecation
in `shape.py:856`, and confirm the remediation is applied to the live settings. This
does not wait for either track's plan.

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

## 10. Open questions for the plan phase

- Track B UX: how does the user launch `ccgate run`, and how does its output surface
  relate to a normal session transcript?
- Whether A5's *acting* (auto-triggering `/compact`) ships on or off by default, given
  the original A5 was advisory-only and blocking auto-compact risks a hard
  context-limit failure (`IMPL-SPEC-automation.md` §A5).
- Sequencing: Track A (measurement + re-baseline) almost certainly ships before Track B,
  so the baseline exists to judge Track B's A/B — mirroring the original
  "measure before enforce" ordering.
