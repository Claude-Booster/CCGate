#!/usr/bin/env python3
"""G8 — Determinism gate: same transcript set → byte-identical miss_audit output x3.
Exits 0 on pass, 1 on fail."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.config import DEFAULTS
from ccgate.scripts.miss_audit import run_audit

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
PATHS = sorted(FIXTURES.glob("*.jsonl"))

if not PATHS:
    print("G8 FAIL — no fixture transcripts found")
    sys.exit(1)

outputs = []
for _ in range(3):
    report = run_audit(PATHS, DEFAULTS)
    outputs.append(json.dumps(report, sort_keys=True))

if len(set(outputs)) != 1:
    print("G8 FAIL — miss_audit output is not deterministic across 3 runs")
    for i, o in enumerate(outputs):
        print(f"  run {i+1}: {o[:120]}...")
    sys.exit(1)

print(f"G8 PASS — output is byte-identical across 3 runs ({len(PATHS)} fixtures)")
sys.exit(0)
