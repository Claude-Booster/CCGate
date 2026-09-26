"""miss_audit.py — cache miss attribution report (Phase 0).

Phase 0 limitation: cause attribution uses transcript-observable signals only
(model_id changes, timestamp gaps vs inferred TTL). Status-line miss_causes
snapshots are not available until Phase 1 hooks ship.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

from ccgate import taxonomy
from ccgate.transcript import (
    Classification,
    Request,
    classify_requests,
    grand_total_input,
    infer_ttl_from_usage,
    read_transcript,
)


def attribute_miss(curr: Request, prev: Request | None, ttl: int) -> str:
    """Return the most likely D1 code for a miss, using transcript-only signals."""
    if prev is not None and prev.model_id != curr.model_id:
        return taxonomy.D1_MODEL_SWITCH

    if prev is not None and curr.timestamp and prev.timestamp:
        try:
            from datetime import datetime
            t_curr = datetime.fromisoformat(curr.timestamp.replace("Z", "+00:00"))
            t_prev = datetime.fromisoformat(prev.timestamp.replace("Z", "+00:00"))
            gap_s = (t_curr - t_prev).total_seconds()
            if gap_s > ttl:
                return taxonomy.D1_TTL_EXPIRED
        except ValueError:
            pass

    return taxonomy.D1_UNCLASSIFIED


def run_audit(paths: list[Path], config: dict) -> dict:
    """Run miss audit over a list of transcript paths; return report dict."""
    all_sessions: list[str] = []
    cause_counts: dict[str, int] = defaultdict(int)
    cause_tokens: dict[str, int] = defaultdict(int)

    total_requests = 0
    total_misses = 0
    total_rebuilds = 0

    tokens = {"grand_total_input": 0, "cache_read": 0,
              "cache_creation": 0, "input": 0, "output": 0}

    for path in sorted(paths):
        all_sessions.append(str(path))
        requests = read_transcript(path)
        if not requests:
            continue

        ttl = infer_ttl_from_usage(requests)
        classified = classify_requests(requests, transcript_path=path)

        prev_req: Request | None = None
        expected_cache = 0

        for req, cls in classified:
            u = req.usage
            tokens["grand_total_input"] += grand_total_input(u)
            tokens["cache_read"]        += u.cache_read_input_tokens
            tokens["cache_creation"]    += u.cache_creation_input_tokens
            tokens["input"]             += u.input_tokens
            tokens["output"]            += u.output_tokens

            if cls == Classification.EXPECTED_REBUILD:
                total_rebuilds += 1
                cause_counts[taxonomy.D2_COMPACTION] += 1
                re_processed = max(0, expected_cache - req.usage.cache_read_input_tokens)
                cause_tokens[taxonomy.D2_COMPACTION] += re_processed
                expected_cache = req.usage.cache_creation_input_tokens

            elif cls == Classification.MISS:
                total_misses += 1
                re_processed = max(0, expected_cache - req.usage.cache_read_input_tokens)
                cause = attribute_miss(req, prev_req, ttl)
                cause_counts[cause] += 1
                cause_tokens[cause] += re_processed
                expected_cache = req.usage.cache_creation_input_tokens

            else:  # HIT
                expected_cache = (req.usage.cache_read_input_tokens
                                  + req.usage.cache_creation_input_tokens)

            total_requests += 1
            prev_req = req

    hit_ratio = (
        (total_requests - total_misses) / total_requests if total_requests > 0 else 1.0
    )

    denom = tokens["cache_read"] + tokens["cache_creation"]
    cache_read_rate = (tokens["cache_read"] / denom) if denom > 0 else 0.0

    misses_list = []
    for cause in sorted(cause_counts.keys()):
        misses_list.append({
            "cause":           cause,
            "count":           cause_counts[cause],
            "recached_tokens": cause_tokens[cause],
            "fix":             taxonomy.FIX_HINTS.get(cause, ""),
        })
    misses_list.sort(key=lambda m: (-m["count"], m["cause"]))

    return {
        "sessions": all_sessions,
        "summary": {
            "total_requests":    total_requests,
            "total_misses":      total_misses,
            "expected_rebuilds": total_rebuilds,
            "hit_ratio":         round(hit_ratio, 6),
            "cache_read_rate":   round(cache_read_rate, 6),
            "tokens":            tokens,
        },
        "misses": misses_list,
    }


def _render_table(report: dict) -> str:
    lines = []
    s = report["summary"]
    session_count = len(report["sessions"])
    lines.append(f"\nMISS AUDIT — {session_count} session(s)\n")
    lines.append(f"  {'cause':<30} {'misses':>6}  {'re-cached':>10}  fix")
    lines.append("  " + "-" * 60)
    for m in report["misses"]:
        tokens = m["recached_tokens"]
        tok_str = f"{tokens/1e6:.1f}M" if tokens >= 1e6 else f"{tokens/1e3:.0f}K"
        lines.append(f"  {m['cause']:<30} {m['count']:>6}  {tok_str:>10}  {m['fix']}")
    lines.append("")
    t = s["tokens"]
    lines.append(
        f"  cache-read rate: {s['cache_read_rate']*100:.1f}%  "
        f"(read {t['cache_read']/1e6:.0f}M / created {t['cache_creation']/1e6:.0f}M)"
    )
    lines.append(f"  hit ratio: {s['hit_ratio']*100:.1f}%  "
                 f"({s['total_misses']} misses / {s['total_requests']} requests)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse
    from ccgate.config import load_config

    parser = argparse.ArgumentParser(prog="ccgate audit")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--session", metavar="ID")
    parser.add_argument("--since", metavar="DURATION")
    parser.add_argument("--json", action="store_true", dest="emit_json")
    parser.add_argument("--assert", action="store_true", dest="assert_mode",
                        help="Exit 1 if avoidable misses exist")
    args = parser.parse_args(argv)

    config = load_config()
    paths: list[Path] = list(args.paths)

    since_dt = None
    if args.since and not paths:
        from datetime import datetime, timedelta, timezone
        import re as _re
        m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])", args.since.strip())
        if m:
            value, unit = float(m.group(1)), m.group(2)
            seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * value
            since_dt = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
        else:
            print(f"ccgate audit: unrecognised --since value {args.since!r} (use e.g. 1d, 6h)", file=sys.stderr)
            sys.exit(1)

    if not paths and args.session:
        from ccgate.transcript import find_transcripts
        paths = find_transcripts(session_id=args.session)
    if not paths:
        from ccgate.transcript import find_transcripts
        paths = find_transcripts(since_dt=since_dt)

    if not paths:
        print("ccgate audit: no transcripts found", file=sys.stderr)
        sys.exit(0)

    report = run_audit(paths, config)

    if args.emit_json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_table(report))

    if args.assert_mode:
        failures: list[str] = []
        s = report["summary"]
        # G4: hit ratio
        min_reqs = config.get("minRequestsForRatio", 10)
        floor = config.get("hitRatioFloor", 0.85)
        if s["total_requests"] >= min_reqs and s["hit_ratio"] < floor:
            failures.append(
                f"G4 FAIL: hit_ratio={s['hit_ratio']:.3f} < {floor}"
                f" ({s['total_requests'] - s['total_misses']}/{s['total_requests']} hits)"
            )
        # G5: zero D1.model_switch / D1.tools_changed
        cause_map = {m["cause"]: m["count"] for m in report["misses"]}
        g5_count = (cause_map.get(taxonomy.D1_MODEL_SWITCH, 0)
                    + cause_map.get(taxonomy.D1_TOOLS_CHANGED, 0))
        if g5_count > 0:
            failures.append(
                f"G5 FAIL: {g5_count} miss(es) of type D1.model_switch or D1.tools_changed"
            )
        if failures:
            for msg in failures:
                print(msg, file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
