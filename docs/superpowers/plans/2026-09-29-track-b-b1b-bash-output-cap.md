# Track B / B1b — F3 Bash Output Truncation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add F3 — a `PostToolUse` hook in the `ccgate run` loop that truncates over-budget stdout of prefix-matched Bash commands (head+tail+marker), off by default, recording exact `chars_elided`.

**Architecture:** A pure module `src/ccgate/run/bashcap.py` (prefix match, `truncate`, shape-defensive `extract_stdout`/`repack`, and a stateful `BashCapHook` callback) plus wiring in `cli.py` (register the PostToolUse hook and add Bash to tools only when enabled) and config keys. The pure primitives and the hook are unit-tested in-process over synthetic `input_data`; the real deny+truncation is proven only in a separate CI probe (on-desk Bash is org-sandboxed — unverified, not broken).

**Tech Stack:** Python 3.11+, `claude-agent-sdk` (optional `run` extra, from B0), pytest, stdlib `hashlib`.

**Spec:** `docs/superpowers/specs/2026-09-29-track-b-b1b-bash-output-cap-design.md`

## Global Constraints

- **Mechanism is PostToolUse `updatedToolOutput`** (truncate output), never PreToolUse command rewrite (spec §2, supersedes BUILD-SPEC §5.5 rule 3).
- **`truncate(stdout: str, head: int, tail: int) -> tuple[str, int]` is pure and str-based** — its signature does not depend on the SDK shape (spec §3). All SDK-shape handling is isolated in `extract_stdout`/`repack`, confirmed by a CI probe that **prints** raw `input_data`, never asserts on a parsed field.
- **Off by default:** `bashCapEnabled=False`. F3 only wires when enabled (spec §9, parent §405).
- **Adapter never truncates a guessed field:** unrecognized `tool_response` shape → passthrough, record nothing (spec §8).
- **Config ranges are load-bearing:** `bashCapHeadChars`/`bashCapTailChars` ∈ [200, 200000]; a `0` or 10M value must fall back to default, not silently disable/never-fire F3 (spec §9).
- **Honesty:** `chars_elided` exact and primary; `tokens_elided_est = chars_elided // 4` carries `~` wherever surfaced (I4); the marker's own chars are `tokens_injected`; the debug-loop-skip count is reported alongside (spec §6).
- **No git hook bypass; no hardcoded abs paths; TDD.** The agent cannot run the SDK loop or full suite to green — in-process tests are agent-runnable; the real proof is CI (spec §7).

## Review Focus

- **`tool_response` is a dict without a string `stdout`** (or any unrecognized shape) → passthrough, no event, no crash — never truncate a guessed field. → Task 4 test.
- **stdout length exactly `head + tail`** (boundary) → no truncation, `chars_elided == 0`, output identical. → Task 1 test.
- **`head == 0` (or `tail == 0`)** → valid output (no negative/`[:0]` slice surprises), marker present. → Task 1 test.
- **Same command repeated within the debug window** → later runs not truncated; `debug_loop_skips` increments. → Task 4 test.
- **`bashCapHeadChars`/`bashCapTailChars` out of range (0 or 10,000,000)** → falls back to default, F3 still fires meaningfully. → Task 2 test.

---

### Task 1: `bashcap.py` pure primitives

**Files:**
- Create: `src/ccgate/run/bashcap.py`
- Test: `tests/test_bashcap.py`

**Interfaces:**
- Produces: `MARKER_RE` (n/a); `build_marker(elided:int, head:int, tail:int) -> str`; `compile_prefixes(prefixes:list[str]) -> list[str]` (raises `ValueError` on regex metachar); `command_matches(command:str, prefixes:list[str]) -> bool`; `truncate(stdout:str, head:int, tail:int) -> tuple[str,int]`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from ccgate.run.bashcap import build_marker, compile_prefixes, command_matches, truncate


def test_compile_prefixes_rejects_regex_metachars():
    with pytest.raises(ValueError):
        compile_prefixes(["pytest", "grep.*"])   # '.' and '*' are metachars
    assert compile_prefixes(["pytest", "cargo test"]) == ["pytest", "cargo test"]


def test_command_matches_prefix():
    pats = ["pytest", "cargo test"]
    assert command_matches("pytest -q tests/", pats) is True
    assert command_matches("cargo test --all", pats) is True
    assert command_matches("ls -la", pats) is False


def test_truncate_over_budget_keeps_head_tail_and_marker():
    stdout = "H" * 100 + "M" * 500 + "T" * 100  # 700 chars
    out, elided = truncate(stdout, head=100, tail=100)
    assert elided == 500
    assert out.startswith("H" * 100)
    assert out.endswith("T" * 100)
    assert "elided" in out and "500" in out


def test_truncate_at_exact_budget_is_no_change():
    stdout = "X" * 200
    out, elided = truncate(stdout, head=100, tail=100)   # len == head+tail
    assert elided == 0
    assert out == stdout


def test_truncate_head_zero_is_valid():
    stdout = "A" * 300
    out, elided = truncate(stdout, head=0, tail=100)
    assert elided == 200
    assert out.endswith("A" * 100)
    assert not out.startswith("A" * 101)   # no accidental full slice from head=0


def test_truncate_under_budget_unchanged():
    assert truncate("small", head=100, tail=100) == ("small", 0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_bashcap.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccgate.run.bashcap'`.

- [ ] **Step 3: Implement**

Create `src/ccgate/run/bashcap.py`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_bashcap.py -q`
Expected: PASS (6).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/bashcap.py tests/test_bashcap.py
git commit -m "feat(run): bashcap pure primitives (prefix match, truncate, marker)"
```

---

### Task 2: config `bashCap*` keys + ranges + prefix list-merge

**Files:**
- Modify: `src/ccgate/config.py`
- Test: `tests/test_config.py` (append; create if absent)

**Interfaces:**
- Consumes: `load_config`.
- Produces: config keys `bashCapEnabled`(bool), `bashCapHeadChars`(int), `bashCapTailChars`(int), `bashCapDebugLoopCalls`(int), `bashCapPrefixes`(list[str]).

- [ ] **Step 1: Write the failing tests**

```python
from ccgate.config import load_config, DEFAULTS


def test_bashcap_defaults_present():
    assert DEFAULTS["bashCapEnabled"] is False
    assert DEFAULTS["bashCapHeadChars"] == 4000
    assert DEFAULTS["bashCapTailChars"] == 12000
    assert DEFAULTS["bashCapDebugLoopCalls"] == 3
    assert "pytest" in DEFAULTS["bashCapPrefixes"]
    assert "grep" not in DEFAULTS["bashCapPrefixes"]   # dropped (spec §9)


def test_bashcap_headchars_out_of_range_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapHeadChars": 0}', encoding="utf-8")
    assert load_config()["bashCapHeadChars"] == 4000   # 0 rejected → default


def test_bashcap_prefixes_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapPrefixes": ["deno test"]}', encoding="utf-8")
    prefixes = load_config()["bashCapPrefixes"]
    assert "pytest" in prefixes and "deno test" in prefixes   # merged, not replaced
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_config.py -k bashcap -q`
Expected: FAIL — `KeyError: 'bashCapEnabled'`.

- [ ] **Step 3: Implement**

In `src/ccgate/config.py` `DEFAULTS`, add:

```python
    "bashCapEnabled": False,
    "bashCapHeadChars": 4000,
    "bashCapTailChars": 12000,
    "bashCapDebugLoopCalls": 3,
    "bashCapPrefixes": ["pytest", "cargo test", "jest", "go test", "npm test", "mvn test"],
```

In `_RANGE`, add:

```python
    "bashCapHeadChars":       (200, 200_000),
    "bashCapTailChars":       (200, 200_000),
    "bashCapDebugLoopCalls":  (0, 10_000),
```

Generalize the list-merge in `_merge_file` — replace the `bashRewriteRules`-only check:

```python
        if key in ("bashRewriteRules", "bashCapPrefixes") and isinstance(val, list):
            cfg[key] = cfg[key] + val
            continue
```

And in `load_config`'s env-override loop, skip the new list key alongside `bashRewriteRules`:

```python
        if key in ("bashRewriteRules", "bashCapPrefixes"):
            continue  # list type not parseable from a single env var
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_config.py -k bashcap -q`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/config.py tests/test_config.py
git commit -m "feat(config): bashCap* keys, ranges, prefix list-merge"
```

---

### Task 3: `RunRecorder.append_event`

**Files:**
- Modify: `src/ccgate/run/record.py`
- Test: `tests/test_run_record.py` (append)

**Interfaces:**
- Consumes: `RunRecorder` (B0).
- Produces: `RunRecorder.append_event(event: dict) -> None` — appends one JSONL line verbatim.

- [ ] **Step 1: Write the failing test**

```python
def test_append_event_writes_verbatim_line(tmp_path, monkeypatch):
    from ccgate.run.record import RunRecorder
    import json
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-evt")
    rec.append_event({"type": "ccgate_event", "rule": "F3", "chars_elided": 500})
    line = json.loads(rec.path.read_text(encoding="utf-8").splitlines()[-1])
    assert line["rule"] == "F3" and line["chars_elided"] == 500


def test_event_does_not_perturb_token_counts(tmp_path, monkeypatch):
    """read_transcript/run_audit ignore non-assistant lines (spec §6)."""
    from ccgate.run.record import RunRecorder
    from ccgate.scripts.miss_audit import run_audit
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-evt2")
    rec.append_assistant("claude-opus-4-8", {"input_tokens": 5, "cache_read_input_tokens": 50,
                                             "cache_creation_input_tokens": 0, "output_tokens": 3})
    before = run_audit([rec.path], {})["summary"]["tokens"]
    rec.append_event({"type": "ccgate_event", "rule": "F3", "chars_elided": 999})
    after = run_audit([rec.path], {})["summary"]["tokens"]
    assert before == after
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_run_record.py -k event -q`
Expected: FAIL — `AttributeError: 'RunRecorder' object has no attribute 'append_event'`.

- [ ] **Step 3: Implement**

In `src/ccgate/run/record.py`, add to `RunRecorder`:

```python
    def append_event(self, event: dict) -> None:
        """Append a ccgate_event line (F3 truncation / summary). read_transcript ignores
        non-'assistant' lines, so this never perturbs token counts (spec §6)."""
        self._append(event)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_run_record.py -q`
Expected: PASS (all record tests).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/record.py tests/test_run_record.py
git commit -m "feat(run): RunRecorder.append_event for F3 events"
```

---

### Task 4: shape adapter + `BashCapHook` (stateful) in `bashcap.py`

**Files:**
- Modify: `src/ccgate/run/bashcap.py`
- Test: `tests/test_bashcap.py`

**Interfaces:**
- Consumes: `truncate`, `command_matches`, `build_marker`; `RunRecorder.append_event`.
- Produces: `extract_stdout(tool_response) -> str | None`; `repack(tool_response, new_stdout) -> Any`; `class BashCapHook` with `async __call__(input_data, tool_use_id, context) -> dict` and `summary() -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio
from ccgate.run.bashcap import extract_stdout, repack, BashCapHook
from ccgate.run.record import RunRecorder


def test_extract_stdout_shapes():
    assert extract_stdout("plain string") == "plain string"
    assert extract_stdout({"stdout": "s", "stderr": "e", "interrupted": False}) == "s"
    assert extract_stdout({"no_stdout_here": 1}) is None      # unrecognized dict → None
    assert extract_stdout(12345) is None


def test_repack_preserves_dict_siblings():
    out = repack({"stdout": "old", "stderr": "e", "interrupted": False}, "new")
    assert out == {"stdout": "new", "stderr": "e", "interrupted": False}
    assert repack("old", "new") == "new"


def _hook(tmp_path, monkeypatch, prefixes=("pytest",), head=10, tail=10, debug=3):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-hook")
    return BashCapHook(list(prefixes), head, tail, debug, rec), rec


def _call(hook, command, stdout):
    inp = {"tool_name": "Bash", "tool_input": {"command": command},
           "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False}}
    return asyncio.run(hook(inp, "tuid", None))


def test_truncates_matched_over_budget(tmp_path, monkeypatch):
    hook, rec = _hook(tmp_path, monkeypatch)
    out = _call(hook, "pytest -q", "X" * 100)
    new = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert "elided" in new and len(new) < 100
    assert hook.summary()["truncations"] == 1
    assert hook.summary()["total_chars_elided"] == 80


def test_non_matching_command_passthrough(tmp_path, monkeypatch):
    hook, _ = _hook(tmp_path, monkeypatch)
    assert _call(hook, "ls -la", "X" * 100) == {}


def test_dict_without_stdout_passthrough_no_event(tmp_path, monkeypatch):
    hook, rec = _hook(tmp_path, monkeypatch)
    inp = {"tool_name": "Bash", "tool_input": {"command": "pytest"},
           "tool_response": {"weird": "shape"}}
    assert asyncio.run(hook(inp, "t", None)) == {}
    assert hook.summary()["truncations"] == 0


def test_non_bash_passthrough(tmp_path, monkeypatch):
    hook, _ = _hook(tmp_path, monkeypatch)
    inp = {"tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": {"stdout": "Y"*100}}
    assert asyncio.run(hook(inp, "t", None)) == {}


def test_debug_loop_repeat_not_truncated(tmp_path, monkeypatch):
    hook, _ = _hook(tmp_path, monkeypatch, debug=3)
    _call(hook, "pytest -q", "X" * 100)          # 1st: truncated
    out2 = _call(hook, "pytest -q", "X" * 100)   # 2nd identical within window: skipped
    assert out2 == {}
    assert hook.summary()["debug_loop_skips"] == 1
    assert hook.summary()["truncations"] == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_bashcap.py -k "extract or repack or truncat or passthrough or debug" -q`
Expected: FAIL — `ImportError: cannot import name 'extract_stdout'`.

- [ ] **Step 3: Implement**

Append to `src/ccgate/run/bashcap.py`:

```python
import hashlib


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
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_bashcap.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/bashcap.py tests/test_bashcap.py
git commit -m "feat(run): BashCapHook — shape-defensive PostToolUse truncation + debug-loop escape"
```

---

### Task 5: Wire F3 into `cli.py`

**Files:**
- Modify: `src/ccgate/run/cli.py`
- Test: `tests/test_run_cli.py`

**Interfaces:**
- Consumes: `bashcap.BashCapHook`, `bashcap.compile_prefixes`, `config.load_config`.
- Produces: when `bashCapEnabled`, `run_task` registers a PostToolUse Bash hook and adds `"Bash"` to `tools`/`allowed_tools`; writes the hook's `summary()` event at finish. `_build_options(patterns, enforce, config, bashcap_hook)` and `_factory(options)` translate both PreToolUse and PostToolUse.

- [ ] **Step 1: Write the failing tests**

```python
def test_bashcap_disabled_by_default_no_bash_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))       # no config → bashCapEnabled False
    asyncio.run(run_task("t", enforce=True, cwd=tmp_path, client_factory=_FakeClient))
    opts = _FakeClient.last_options
    assert "Bash" not in opts["allowed_tools"]
    assert opts["hooks"].get("PostToolUse", []) == []


def test_bashcap_enabled_adds_bash_and_posttool_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapEnabled": true}', encoding="utf-8")
    asyncio.run(run_task("t", enforce=True, cwd=tmp_path, client_factory=_FakeClient))
    opts = _FakeClient.last_options
    assert "Bash" in opts["allowed_tools"] and "Bash" in opts["tools"]
    assert opts["hooks"]["PostToolUse"]       # hook present
```

(Add near B0's `_FakeClient` tests. Update `test_b0_restricts_tools_to_read` to the new
`_build_options(patterns, enforce, config, bashcap_hook)` signature: call it with
`load_config()` and `bashcap_hook=None`, asserting `tools == ["Read"]` when bashCap disabled.)

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_run_cli.py -k bashcap -q`
Expected: FAIL — PostToolUse key absent / Bash not added.

- [ ] **Step 3: Implement**

In `src/ccgate/run/cli.py`:

```python
from ccgate.config import load_config
from ccgate.run.bashcap import BashCapHook, compile_prefixes


def _build_options(patterns, enforce: bool, config: dict, bashcap_hook):
    pre = [make_read_deny_hook(patterns)] if enforce else []
    post = [bashcap_hook] if (enforce and bashcap_hook is not None) else []
    tools = ["Read"] + (["Bash"] if post else [])
    return {
        "hooks": {"PreToolUse": pre, "PostToolUse": post},
        "setting_sources": [],
        "tools": tools,
        "allowed_tools": list(tools),
    }


def _factory(options):
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
    pre = options["hooks"]["PreToolUse"]
    post = options["hooks"]["PostToolUse"]
    hooks = {}
    if pre:
        hooks["PreToolUse"] = [HookMatcher(matcher="Read", hooks=pre)]
    if post:
        hooks["PostToolUse"] = [HookMatcher(matcher="Bash", hooks=post)]
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        hooks=hooks, setting_sources=options["setting_sources"],
        tools=options["tools"], allowed_tools=options["allowed_tools"]))


async def run_task(task_prompt: str, *, enforce: bool, cwd: Path, client_factory,
                   config: dict | None = None) -> Path:
    if config is None:
        config = load_config(str(cwd))
    patterns = load_contextignore(cwd)
    recorder = RunRecorder(make_run_id(str(cwd)))
    bashcap_hook = None
    if enforce and config.get("bashCapEnabled"):
        bashcap_hook = BashCapHook(
            compile_prefixes(config["bashCapPrefixes"]),
            config["bashCapHeadChars"], config["bashCapTailChars"],
            config["bashCapDebugLoopCalls"], recorder)
    options = _build_options(patterns, enforce, config, bashcap_hook)
    async with client_factory(options=options) as client:
        await client.query(task_prompt)
        async for msg in client.receive_response():
            for block in (getattr(msg, "content", None) or []):
                text = getattr(block, "text", None)
                if text:
                    print(text)
            model = getattr(msg, "model", None)
            usage = getattr(msg, "usage", None)
            if model is not None and usage is not None:
                recorder.append_assistant(model, usage)
    if bashcap_hook is not None:
        recorder.append_event(bashcap_hook.summary())
    recorder.finish()
    return recorder.path
```

`main()` is unchanged (it already calls `run_task(..., client_factory=_factory)`; `config`
defaults to `load_config`). Remove the now-unused `options_builder=_build_options` param and
the old `_build_options(patterns, enforce)` signature.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_run_cli.py -q`
Expected: PASS (B0 tests + the two new bashcap wiring tests; `_factory` SDK-gated test still
passes with the PreToolUse-only path when Bash disabled).

- [ ] **Step 5: Run the full run-package suite (agent-runnable)**

Run: `python -m pytest tests/test_run_cli.py tests/test_bashcap.py tests/test_run_record.py tests/test_run_policy.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/run/cli.py tests/test_run_cli.py
git commit -m "feat(run): wire F3 BashCapHook into ccgate run (Bash tool + PostToolUse when enabled)"
```

---

### Task 6: CI probe — real truncation red→green (user/CI verified)

**Files:**
- Create: `scripts/b1b_ci_probe.py`
- Create: `.github/workflows/b1b-bash-cap-integration.yml`
- Create: `tests/fixtures/run_b1b/task.txt`
- Test: `tests/test_b1b_probe.py` (pure `evaluate`/`parse` logic, agent-runnable)

**Interfaces:** none (integration). `evaluate(...)` + `parse_record_path(...)` mirror B0's probe, unit-tested.

- [ ] **Step 1: Create the fixture task**

`tests/fixtures/run_b1b/task.txt`:
```
Run exactly this bash command and then stop: python -c "print('L'*50000)"
```
(50,000 chars of stdout — well over the 16,000 default budget. `python -c` is not a matched
prefix, so the probe config adds it: see workflow env / config below.)

- [ ] **Step 2: Write the probe's pure decision logic + tests**

`tests/test_b1b_probe.py`:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from b1b_ci_probe import evaluate  # noqa: E402

MARK = "ccgate F3"


def test_green_when_baseline_full_and_treatment_truncated():
    assert evaluate(0, "L"*50000, 0, "LLL" + MARK + "LLL") == 0


def test_fails_if_treatment_not_truncated():
    assert evaluate(0, "L"*50000, 0, "L"*50000) != 0        # marker absent → no truncation


def test_treatment_crash_is_not_success():
    assert evaluate(0, "L"*50000, 1, "traceback") != 0
```

- [ ] **Step 3: Run to verify they fail, then implement the probe**

Run: `python -m pytest tests/test_b1b_probe.py -q` → FAIL (`No module named 'b1b_ci_probe'`).

Create `scripts/b1b_ci_probe.py`:
```python
"""b1b_ci_probe.py — CI-only: prove F3 truncates over-budget matched Bash output.

on-desk this cannot run — the org sandbox blocks Bash (spec §3/§7). Baseline (bashCap off)
sees full output; treatment (bashCap on) sees the ccgate F3 marker and less output. Step 0
prints raw PostToolUse input_data (print, not assert — the sandbox fakes calls silently)."""
import subprocess, sys
from pathlib import Path

MARK = "ccgate F3"
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "run_b1b"


def evaluate(base_rc: int, base_out: str, treat_rc: int, treat_out: str) -> int:
    both = base_rc == 0 and treat_rc == 0
    ok = both and (MARK not in base_out) and (MARK in treat_out)   # positive: marker present in treatment
    print(f"PROBE_BOTH_CLEAN={both}")
    print(f"PROBE_TREATMENT_TRUNCATED={MARK in treat_out}")
    return 0 if ok else 1


def _run(enabled: bool) -> tuple[int, str]:
    # bashCapEnabled is config-only (no bool env override); write/remove a project config
    # around each run. Prefix must match the fixture command so the 50k-char output is capped.
    import json
    cfgdir = FIX / ".ccgate"
    cfgfile = cfgdir / "config.json"
    if enabled:
        cfgdir.mkdir(exist_ok=True)
        cfgfile.write_text(json.dumps({"bashCapEnabled": True, "bashCapPrefixes": ["python -c"]}),
                           encoding="utf-8")
    elif cfgfile.exists():
        cfgfile.unlink()
    try:
        r = subprocess.run([sys.executable, "-m", "ccgate.dispatch", "run", "--task", "task.txt"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=str(FIX))
        return r.returncode, r.stdout + r.stderr
    finally:
        if cfgfile.exists():
            cfgfile.unlink()


def main() -> int:
    b_rc, b_out = _run(enabled=False)
    t_rc, t_out = _run(enabled=True)
    return evaluate(b_rc, b_out, t_rc, t_out)


if __name__ == "__main__":
    sys.exit(main())
```

Note: `load_config(cwd)` reads `<cwd>/.ccgate/config.json` (project override), so writing it in
the fixture dir (the probe's cwd) enables F3 for the treatment run only. The `.ccgate/` dir is
git-ignored scratch; the probe cleans it up in `finally`.

- [ ] **Step 4: Verify probe logic passes**

Run: `python -m pytest tests/test_b1b_probe.py -q`
Expected: PASS (3).

- [ ] **Step 5: Write the workflow**

`.github/workflows/b1b-bash-cap-integration.yml`:
```yaml
name: b1b-bash-cap-integration
on: workflow_dispatch
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: npm install -g @anthropic-ai/claude-code
      - run: pip install -e ".[run,dev]"
      - name: F3 truncation probe
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        run: python scripts/b1b_ci_probe.py
```

- [ ] **Step 6: Commit**

```bash
git add scripts/b1b_ci_probe.py .github/workflows/b1b-bash-cap-integration.yml tests/fixtures/run_b1b tests/test_b1b_probe.py
git commit -m "test(run): B1b CI probe — real F3 truncation red->green (separate from B0)"
```

- [ ] **Step 7: USER/CI VERIFICATION (agent cannot run this)**

On-desk Bash is org-sandboxed (spec §7) — the agent cannot prove F3. Land the workflow on a
dispatchable branch/`main` and trigger, or run `python scripts/b1b_ci_probe.py` in the user's
terminal with `CLAUDE_CODE_OAUTH_TOKEN` set and `claude` + `pip install -e '.[run]'` present.
Expected: `PROBE_BOTH_CLEAN=True`, `PROBE_TREATMENT_TRUNCATED=True`. **Step 0 of the run must
print the raw PostToolUse `input_data`** — confirm `extract_stdout`/`repack` match the real
shape; if `tool_response` differs from `{stdout,stderr,interrupted}`, adjust those two
functions only (spec §3). Record the output here:

Evidence log (fill in): `PROBE_BOTH_CLEAN=____  PROBE_TREATMENT_TRUNCATED=____  raw input_data shape=____`

---

## Sequencing note

Tasks 1–5 are agent-runnable in-process (TDD; pure primitives, config, recorder, the async
hook over synthetic `input_data`, fake-client wiring). Task 6's real truncation proof is
CI-only, like B0 — on-desk `ccgate run` with Bash enabled remains **unverified, not broken**
(spec §7). Order is strict 1→2→3→4→5→6.
