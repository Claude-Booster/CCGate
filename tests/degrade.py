#!/usr/bin/env python3
"""G11 — Version floor degradation gate.

Band 1: prompt_cache absent -> statusline renders without traceback.
Band 2: prompt_cache present, miss_causes absent -> statusline renders; hit_ratio shown.
Both bands must not raise any exception.
Exits 0 on pass, 1 on fail.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.config import DEFAULTS
from ccgate.scripts.statusline import render
from ccgate.transcript import CapabilityBand, detect_band

FIXTURES = Path(__file__).parent / "fixtures" / "statusline"

failures: list[str] = []

for band_name, filename, expected_band in [
    ("Band 1", "band1_payload.json", CapabilityBand.BAND1_PRE_CACHE),
    ("Band 2", "band2_payload.json", CapabilityBand.BAND2_CACHE_NO_CAUSES),
]:
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))

    band = detect_band(payload)
    if band != expected_band:
        failures.append(f"{band_name}: detect_band returned {band}, expected {expected_band}")
        continue

    try:
        line = render(payload, DEFAULTS)
    except Exception:
        failures.append(f"{band_name}: render() raised:\n{traceback.format_exc()}")
        continue

    if not isinstance(line, str) or not line:
        failures.append(f"{band_name}: render() returned empty or non-string: {line!r}")
        continue

    if "\n" in line:
        failures.append(f"{band_name}: render() output contains newline")
        continue

    print(f"  {band_name}: OK -> {line!r}")

band2 = json.loads((FIXTURES / "band2_payload.json").read_text(encoding="utf-8"))
band2_line = render(band2, DEFAULTS)
if "%" not in band2_line:
    failures.append("Band 2: hit_ratio not reflected in rendered line")

if failures:
    print("G11 FAIL:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)

print("G11 PASS — Band 1 and Band 2 degrade gracefully without traceback")
sys.exit(0)
