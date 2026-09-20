#!/usr/bin/env python3
"""G10 — Cross-platform path encoding round-trip gate. Exits 0 on pass, 1 on fail."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.transcript import encode_cwd

FIXTURES: list[tuple[str, str]] = [
    ("C:\\Users\\fred\\project",                       "c--Users-fred-project"),
    ("C:\\Users\\developer\\CCGate",            "c--Users-developer-CCGate"),
    ("C:\\Users\\fred\\OneDrive - Corp\\Docs",    "c--Users-fred-OneDrive---Corp-Docs"),
    ("/home/user/projects/ccgate",                     "-home-user-projects-ccgate"),
    ("/Users/alice/work/my.project",                   "-Users-alice-work-my-project"),
]

failures = []
for cwd, expected in FIXTURES:
    got = encode_cwd(cwd)
    if got != expected:
        failures.append(f"  encode_cwd({cwd!r})\n    got:      {got!r}\n    expected: {expected!r}")

if failures:
    print("G10 FAIL — path encoding mismatches:")
    print("\n".join(failures))
    sys.exit(1)

print(f"G10 PASS — {len(FIXTURES)} path fixtures verified")
sys.exit(0)
