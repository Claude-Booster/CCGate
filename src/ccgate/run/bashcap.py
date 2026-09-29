"""bashcap.py — F3 Bash output truncation (Track B B1b). Pure primitives + stateful hook.

Mechanism: PostToolUse updatedToolOutput (spec §2). truncate() is pure str->str so its
signature never depends on the unverifiable SDK tool_response shape (spec §3)."""
from __future__ import annotations

import hashlib

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
    candidate = kept_head + build_marker(elided, head, tail) + kept_tail
    # Never grow the output: if the marker overhead cancels the saving (small overages,
    # tiny budgets), leave stdout untouched — F3 must not make output larger (spec §6 honesty).
    if len(candidate) >= len(stdout):
        return stdout, 0
    return candidate, elided


def extract_stdout(tool_response) -> str | None:
    """Defensive: return the stdout string, or None for any unrecognized shape (spec §8).
    Never guesses — an unrecognized shape passes through untouched upstream."""
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        s = tool_response.get("stdout")
        if isinstance(s, str):
            return s
    return None


def repack(tool_response, new_stdout: str):
    """Rebuild the tool output in the same shape, preserving siblings for a dict."""
    if isinstance(tool_response, dict):
        return {**tool_response, "stdout": new_stdout}
    return new_stdout


class BashCapHook:
    """Stateful PostToolUse callback: truncate over-budget stdout of matched Bash commands.
    Holds per-run state (debug-loop window, counters). No SDK import — testable directly."""

    def __init__(self, prefixes: list[str], head: int, tail: int, debug_calls: int, recorder):
        self.prefixes = prefixes
        self.head = head
        self.tail = tail
        self.debug_calls = debug_calls
        self.recorder = recorder
        self._last_seen: dict[str, int] = {}
        self._call_index = 0
        self._truncations = 0
        self._skips = 0
        self._total_elided = 0

    async def __call__(self, input_data: dict, tool_use_id, context) -> dict:
        try:
            if input_data.get("tool_name") != "Bash":
                return {}
            command = (input_data.get("tool_input") or {}).get("command", "")
            if not command_matches(command, self.prefixes):
                return {}
            self._call_index += 1
            h = hashlib.sha256(command.encode("utf-8")).hexdigest()
            last = self._last_seen.get(h)
            self._last_seen[h] = self._call_index
            stdout = extract_stdout(input_data.get("tool_response"))
            if stdout is None:
                return {}                                  # unrecognized shape → never guess
            if len(stdout) <= self.head + self.tail:
                return {}                                  # nothing to elide
            if last is not None and (self._call_index - last) <= self.debug_calls:
                self._skips += 1                           # active debug loop → full output
                return {}
            new_stdout, elided = truncate(stdout, self.head, self.tail)
            if elided == 0:
                return {}                                  # never-grow guard tripped → leave as-is
            self._truncations += 1
            self._total_elided += elided
            self.recorder.append_event({
                "type": "ccgate_event", "rule": "F3", "command_prefix": command[:40],
                "full_chars": len(stdout), "kept_chars": len(new_stdout),
                "chars_elided": elided, "tokens_elided_est": elided // 4,
            })
            updated = repack(input_data.get("tool_response"), new_stdout)
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": updated}}
        except Exception:
            return {}                                      # fail-open: never crash the run

    def summary(self) -> dict:
        return {"type": "ccgate_event", "rule": "F3", "summary": True,
                "truncations": self._truncations, "debug_loop_skips": self._skips,
                "total_chars_elided": self._total_elided}
