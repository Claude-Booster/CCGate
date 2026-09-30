"""shellcmd.py — shared, pure shell-command helpers for Track B matchers.

Relocated prefix helpers (were in bashcap.py) live here so the shared module does not
depend on a feature module. strip_runner_prefixes peels a leading runner (sudo/env/
time/command/nice) so a reader hidden behind it is still seen — best-effort, no shell
grammar parsing (spec §4)."""
from __future__ import annotations

_METACHARS = set(".*+?[(")
_RUNNERS = {"sudo", "env", "time", "command", "nice"}
_SUDO_VALUE_FLAGS = {"-u", "-g", "-U", "-C", "-p", "-r", "-t", "-h", "-R", "-D"}


def compile_prefixes(prefixes: list[str]) -> list[str]:
    """Return prefixes unchanged, or raise ValueError if any contains a regex metachar
    (literal-prefix matching only)."""
    for p in prefixes:
        bad = _METACHARS & set(p)
        if bad:
            raise ValueError(f"prefix entry {p!r} contains regex metachar(s) {sorted(bad)}; "
                             "literal prefixes only")
    return list(prefixes)


def command_matches(command: str, prefixes: list[str]) -> bool:
    """True if command starts with any prefix (multi-word prefixes like 'cargo test')."""
    return any(command.startswith(p) for p in prefixes)


def first_token(command: str) -> str:
    """The first whitespace-delimited token, or '' for an empty command."""
    toks = command.split()
    return toks[0] if toks else ""


def strip_runner_prefixes(command: str, max_iter: int = 6) -> str:
    """Peel leading runner prefixes so a reader behind them is visible.

    Handles: bare sudo/time/command/nice; sudo value-flags (-u foo); env with KEY=VAL
    assignments and bare env; simple leading flags. Recurses (sudo env FOO=1 cat x) under
    a small iteration cap. Deeper handling would be shell-grammar parsing — a non-goal."""
    for _ in range(max_iter):
        toks = command.split()
        if not toks or toks[0] not in _RUNNERS:
            return command
        runner = toks[0]
        rest = toks[1:]
        i = 0
        while i < len(rest):
            t = rest[i]
            if runner == "env" and "=" in t and not t.startswith("-"):
                i += 1
                continue
            if t.startswith("-"):
                i += 1
                if runner == "sudo" and t in _SUDO_VALUE_FLAGS and i < len(rest):
                    i += 1  # also skip the flag's value (sudo -u foo)
                continue
            break
        command = " ".join(rest[i:])
    return command
