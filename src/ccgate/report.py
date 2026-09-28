"""report.py — Track A honest reporting: bound guards, I7 ledger, rendering.

Pure functions. ledger.py (Track B's net model) is intentionally untouched.
"""
from __future__ import annotations

# D4 / ELIMINATE-headroom checks that run_shape actually emits today. bashOutputMaxChars
# (_check_output_caps) is a Phase-0 stub returning [], so it is not listed until it emits.
_D4_CHECKS = frozenset({"claudeMdLines", "claudeMdExcludes", "skillListing", "denyReads"})


def assert_bounds(summary: dict) -> None:
    """Raise ValueError if any by-construction bound is violated (spec §6, §12.9).

    Guards the shape of the original defect: a negative/impossible figure that no
    bound was watching. Called before any figure is printed.
    """
    reqs = summary["total_requests"]
    misses = summary["total_misses"]
    if reqs < 0 or misses < 0:
        raise ValueError(f"negative count: requests={reqs} misses={misses}")
    if misses > reqs:
        raise ValueError(f"misses {misses} > requests {reqs}")

    by = summary["by_origin"]
    origin_misses = by["main"]["misses"] + by["subagent"]["misses"]
    if origin_misses != misses:
        raise ValueError(f"origin misses {origin_misses} != total misses {misses}")

    for name in ("hit_ratio", "cache_read_rate"):
        v = summary[name]
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"{name}={v} not in [0,1]")

    t = summary["tokens"]
    for k, v in t.items():
        if v < 0:
            raise ValueError(f"token quantity {k}={v} < 0")
    if t["cache_read"] + t["cache_creation"] > t["grand_total_input"]:
        raise ValueError(
            f"cache_read+cache_creation ({t['cache_read'] + t['cache_creation']}) "
            f"> grand_total_input ({t['grand_total_input']})"
        )


def compute_ledger(shape_findings: list[dict]) -> dict:
    """Honest, per-cause I7 ledger (spec §3). No fabricated numbers.

    A1: unmeasurable (pin pre-applied). A3: 0 (driver absent). D4: shape findings,
    available-but-unapplied, listed WITHOUT token estimates (counterfactual). Track A
    denies/measures/injects nothing, so those categories are 0.
    """
    d4_findings = [f for f in shape_findings if f.get("check") in _D4_CHECKS]
    return {
        "tokens_prevented": {
            "a1": {"value": None, "status": "unmeasurable",
                   "reason": "1h pin already applied before baseline; no clean 'before' exists"},
            "a3": {"value": 0, "status": "not_applicable",
                   "reason": "driver absent — 0 D1.tools_changed in corpus"},
            "d4": {"value": None, "status": "available_unapplied",
                   "reason": "shape findings, not yet applied; no estimate (counterfactual)",
                   "findings": d4_findings},
        },
        "tokens_avoided": 0,   # Track A denies nothing
        "tokens_measured": 0,  # Track A measures no server-side compaction delta
        "tokens_injected": 0,  # interactive ccgate injects nothing (hooks blocked)
        "net": 0,              # reporter, not actor (I7)
    }
