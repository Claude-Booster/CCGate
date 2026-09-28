"""report.py — Track A honest reporting: bound guards, I7 ledger, rendering.

Pure functions. ledger.py (Track B's net model) is intentionally untouched.
"""
from __future__ import annotations


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
