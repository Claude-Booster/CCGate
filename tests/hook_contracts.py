# tests/hook_contracts.py
"""hook_contracts.py — static invariants on hook source files."""
from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "ccgate" / "hooks"


def test_g22_pre_compact_has_no_print_statements():
    """G22 static: pre_compact.py must not contain print() calls.

    additionalContext/systemMessage from PreCompact are discarded by CC.
    """
    source = (SRC / "pre_compact.py").read_text(encoding="utf-8")
    violations = [
        (i + 1, line)
        for i, line in enumerate(source.splitlines())
        if "print(" in line and not line.lstrip().startswith("#")
    ]
    assert not violations, (
        "G22: pre_compact.py has print() at lines: "
        + ", ".join(f"{ln}: {txt.strip()!r}" for ln, txt in violations)
    )
