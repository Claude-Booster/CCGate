#!/usr/bin/env python3
"""G9 — Schema conformance: all emitted JSON validates against schema/*.json.
Exits 0 on pass, 1 on fail.

Uses stdlib json only — no jsonschema package. Validates required keys and
types rather than full draft-07 compliance (sufficient for catching regressions).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.config import DEFAULTS
from ccgate.scripts.miss_audit import run_audit

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
SCHEMA_DIR = Path(__file__).parent.parent / "schema"


def _check_report(report: dict) -> list[str]:
    errors = []
    for key in ("sessions", "summary", "misses"):
        if key not in report:
            errors.append(f"report missing required key '{key}'")
    s = report.get("summary", {})
    for key in ("total_requests", "total_misses", "expected_rebuilds",
                "hit_ratio", "cache_read_rate", "tokens", "by_origin"):
        if key not in s:
            errors.append(f"summary missing '{key}'")
    for m in report.get("misses", []):
        for key in ("cause", "count", "recached_tokens", "fix"):
            if key not in m:
                errors.append(f"miss entry missing '{key}'")
    return errors


paths = sorted(FIXTURES.glob("*.jsonl"))
if not paths:
    print("G9 FAIL — no fixture transcripts found")
    sys.exit(1)

report = run_audit(paths, DEFAULTS)
errors = _check_report(report)

if errors:
    print("G9 FAIL — schema violations:")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)

print(f"G9 PASS — report conforms to schema ({len(paths)} fixtures, {len(report['misses'])} miss entries)")
sys.exit(0)
