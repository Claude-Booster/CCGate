# DECISION-BRIEF — ccgate architecture

**Date:** 21 September 2026
**Status:** open — blocks Phase 2 continuation
**Reference:** `docs/research/2026-09-21-automation-surfaces.md` (read on demand, not up front)

---

## The question

**Should ccgate remain an interactive Claude Code plugin, given that the interactive surface structurally cannot reach the cost drivers it exists to fix?**

Not "how do we automate the four drivers." That question has been researched and partly answered. This is a choice between three architectures with different reachability.

---

## What changed

ccgate v1.0 assumed diagnosis plus hook-based enforcement would add up to automation. Research on 21 Sep disproved the second half. The four largest cost drivers in ccgate's own defect taxonomy — model switch, MCP tool-set change, TTL expiry, compaction timing — are not tool calls, and no hook output can initiate any of them.

This makes invariant **I1** (rules live in scripts, never in prose) unsatisfiable for D1 in interactive mode. `miss_audit` diagnoses, then a human must change a habit. That is the compliance loop I1 was written to forbid.

---

## Hard constraints — do not relitigate these

Verified against primary sources. Treat as settled facts, not design space.

1. **No hook output invokes a slash command.** No `triggerCompact`, no `action:"compact"`, on any hook event. Confirmed in anthropics/claude-code #58538, #37307, #66246; #65586 closed NOT_PLANNED.
2. **`PreModelSwitch` can block but not redirect.** No `updatedModel` field. It can deny an externally-requested switch; it cannot cause or retarget one.
3. **`PreCompact` can block but not start compaction.** Its top-level `systemMessage` is discarded — checkpoint to disk and re-inject via `SessionStart` with `source: compact`.
4. **The Agent SDK can do all of it.** `/compact` and `/clear` are accepted as the prompt string; `setModel()` changes model mid-conversation; `canUseTool` and callback hooks gate tools programmatically.
5. **The raw API adds server-side context management.** `clear_tool_uses_20250919` (with `clear_at_least` as the anti-thrash guard), `clear_thinking_20251015`, `compact_20260112`. Anthropic reports 84% token reduction on a 100-turn eval — internal, not independently replicated.
6. **A proxy disables MCP tool-search deferral.** A non-first-party `ANTHROPIC_BASE_URL` turns off deferral by default; issue #40314 measured 120K tool-definition tokens loaded upfront. Recoverable with `ENABLE_TOOL_SEARCH=true`, but this is a real cost the proxy option must carry.
7. **Claude Code already does microcompact.** Native tool-result clearing, cache-aware, not user-disableable (#7176). Any ccgate feature that duplicates it is dead weight.

---

## The three candidates

| | Reaches D1 drivers | Works on interactive sessions | Cost |
|---|---|---|---|
| **A. Interactive plugin** (status quo) | no — detect and advise only | yes | already built |
| **B. Agent SDK loop** | yes — all four | no, unattended/CI only | rebuild the harness |
| **C. Proxy** | yes — TTL, compaction, subagent routing | yes, transparently | breaks tool deferral; ops burden |

Hybrids are permitted. A and B are not mutually exclusive — the honest reading is that they serve different session types.

---

## Decision criteria, in priority order

1. **Reachability.** Does it close the D1 gap, or only describe it?
2. **Net savings, measured.** Positive on replayed A/B tasks, cache-create separated from cache-read. Gross "tokens avoided" is not evidence.
3. **Invariant survival.** Does I1 hold, or does the design still depend on a human reading a report and complying?
4. **Blast radius.** What breaks when it misfires, and how fast can the user escape?
5. **Cost to reach parity** with what is already implemented.

---

## Non-negotiables

- **I1** — deterministic enforcement, never prose instructions.
- **I3** — net accounting; a component with negative net auto-disables.
- **I7** — diagnostics are labelled as diagnostics and never contribute to a savings figure.
- Any claimed savings figure traces to a measured A/B, not a vendor or community number.

---

## Out of scope for this session

- The five open questions in `BUILD-SPEC.md` §12 and the three in `BUILD-SPEC-phase2.md`. Architecture first; those are downstream.
- Reimplementing `/doctor`, `/skill-doctor`, or microcompact.
- Feature work on the existing Phase 2 rules.

---

## Required output

A written recommendation containing:

1. Chosen architecture, or a named hybrid with the split stated explicitly (which session types go where).
2. For each of the four D1 drivers: the specific mechanism that automates it under the chosen design, or an explicit statement that it stays diagnostic.
3. What happens to the existing implementation — kept, migrated, or retired, component by component.
4. The A/B methodology that will decide whether the change worked, defined before any code is written.
5. Anything that remains impossible and needs an Anthropic feature request.

---

## The uncomfortable part

A brainstorm will route around this unless it is named, so it is named here:

**The honest answer may be that the interactive plugin keeps only its diagnostics and config linter, and the actual optimizer moves to a loop ccgate owns.** That means a significant fraction of what is already built becomes a reporting layer rather than an optimizer.

Argue against that if it is wrong. Do not avoid it because it is expensive.
