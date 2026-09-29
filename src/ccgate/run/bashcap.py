"""bashcap.py — F3 Bash output truncation (Track B B1b). Pure primitives + stateful hook.

Mechanism: PostToolUse updatedToolOutput (spec §2). truncate() is pure str->str so its
signature never depends on the unverifiable SDK tool_response shape (spec §3)."""
from __future__ import annotations

_METACHARS = set(".*+?[(")


def build_marker(elided: int, head: int, tail: int) -> str:
    return (f"\n[ccgate F3: kept {head} head + {tail} tail; "
            f"elided {elided} chars (~{elided // 4} tokens) from the middle. "
            f"Narrow the command or raise bashCapTailChars to see more.]\n")


def compile_prefixes(prefixes: list[str]) -> list[str]:
    """Return the prefixes unchanged, or raise ValueError if any contains a regex metachar
    (literal-prefix matching only — BUILD-SPEC §433 safety)."""
    for p in prefixes:
        bad = _METACHARS & set(p)
        if bad:
            raise ValueError(f"bashCapPrefixes entry {p!r} contains regex metachar(s) {sorted(bad)}; "
                             "literal prefixes only")
    return list(prefixes)


def command_matches(command: str, prefixes: list[str]) -> bool:
    return any(command.startswith(p) for p in prefixes)


def truncate(stdout: str, head: int, tail: int) -> tuple[str, int]:
    """Keep head + tail chars with a marker in the middle when over budget; else unchanged.
    Returns (new_stdout, chars_elided). chars_elided is exact (spec §6)."""
    if len(stdout) <= head + tail:
        return stdout, 0
    elided = len(stdout) - head - tail
    kept_tail = stdout[len(stdout) - tail:] if tail > 0 else ""
    kept_head = stdout[:head] if head > 0 else ""
    return kept_head + build_marker(elided, head, tail) + kept_tail, elided
