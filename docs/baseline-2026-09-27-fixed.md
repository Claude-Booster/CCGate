# ccgate baseline — 2026-09-27 (fixed tool, like-for-like re-run)

Re-run of `miss_audit` over the same local corpus as `baseline-2026-09-26.md`, but
with the **fixed** tool (Plan 1, through commit `30736e8`). The 2026-09-26 baseline's
token counts were sound; its **miss classification was not** — the subagent split did
not exist and the dollar path was broken. This is the pre-Track-B reference: compare
Track A's future re-baselines against *this*, not the broken-tool numbers.

## Corpus
- Transcripts: 1,104 (grew from 1,087 as this session added session data)
- Requests with usage: 116,211

## Summary (no dollar figures — see Plan 1 / §12.9)
| Metric | Value |
|---|---|
| Total misses | 11,918 |
| Expected rebuilds (D2, excluded) | 351 |
| Hit ratio (request-level) | 89.7% |
| **Cache-read rate (token-weighted)** | **96.4%** |

## The finding that the broken tool hid: main vs subagent
| Origin | Requests | Misses |
|---|---|---|
| **main-thread** | 45,991 | **2,865** |
| subagent | 70,220 | 9,053 |

**9,053 of 11,918 misses (76%) are subagent cold-starts** — structural and unavoidable
by design, not waste. The 2026-09-26 headline of "~11,330 unclassified avoidable" was
roughly three-quarters noise. **2,865 main-thread misses is the actionable number.**

## Miss causes (transcript-signal attribution only; statusline attribution is policy-blocked)
| Cause | Count |
|---|---|
| D1.unclassified | 11,405 |
| D1.ttl_expired | 407 |
| D2.compaction (expected) | 351 |
| D1.model_switch | 106 |
| D1.tools_changed | 0 |

`D1.unclassified` dominates because transcript-only signals (model-id change, TTL gap)
explain few misses and most misses are subagent cold-starts; richer attribution needs
the statusline `miss_causes` payload, which org policy blocks (not a version issue).

## Token totals (ground truth, pricing-free)
| Quantity | Tokens |
|---|---|
| grand_total_input | 16,850,262,647 |
| cache_read | 16,244,733,595 |
| cache_creation | 602,166,777 |
| input (fresh) | 3,362,275 |
| output | 100,016,139 |
