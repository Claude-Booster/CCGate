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


def render_report(summary: dict, ledger: dict) -> str:
    """Human-readable Track A report: honest ledger + accurate prevention framing.

    Does not overstate live ELIMINATE: A1 is applied, A3 absent, D4 unapplied (spec §4).
    """
    s, by, t = summary, summary["by_origin"], summary["tokens"]
    prevented = ledger["tokens_prevented"]
    lines: list[str] = []

    lines.append("\nTRACK A — MEASUREMENT REPORT")
    lines.append("=" * 60)
    lines.append(
        "ELIMINATE remains available to interactive sessions (config savings the "
        "org policy cannot reach). On this machine A1 (1h pin) is applied; A3's "
        "driver is absent; the D4 items are unapplied headroom — run `ccgate shape` "
        "to see what is not yet applied. PREVENT and RECOVER move to the owned loop "
        "(`ccgate run`) and apply only to Track B work."
    )

    lines.append("\nI7 ledger (categories reported separately):")
    lines.append(f"  tokens_prevented / A1: unmeasurable ({prevented['a1']['reason']})")
    lines.append(f"  tokens_prevented / A3: 0 ({prevented['a3']['reason']})")
    d4 = prevented["d4"]["findings"]
    if d4:
        lines.append("  tokens_prevented / D4: available, unapplied (no estimate) —")
        for f in d4:
            lines.append(f"      - {f['check']} [{f.get('severity', '?')}]")
    else:
        lines.append("  tokens_prevented / D4: none found")
    lines.append(f"  tokens_avoided:  {ledger['tokens_avoided']}  (Track A denies nothing)")
    lines.append(f"  tokens_measured: {ledger['tokens_measured']}  (no server-side delta)")
    lines.append(f"  tokens_injected: {ledger['tokens_injected']}  (interactive injects nothing)")
    lines.append(f"  net:             {ledger['net']}  (reporter, not actor — I7)")

    lines.append("\nGround-truth facts (this corpus):")
    lines.append(f"  total misses:    {s['total_misses']}  "
                 f"(main {by['main']['misses']} / subagent {by['subagent']['misses']})")
    lines.append(f"  cache-read rate: {s['cache_read_rate'] * 100:.1f}%")
    lines.append(f"  hit ratio:       {s['hit_ratio'] * 100:.1f}%  "
                 f"({s['total_requests'] - s['total_misses']}/{s['total_requests']})")
    lines.append("\nRun `ccgate audit` (no --report) for the full cause-attribution table.")
    return "\n".join(lines)
