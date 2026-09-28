# Track B / B0 — `ccgate run` Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `ccgate run --task <file>` walking skeleton — a ccgate-owned `ClaudeSDKClient` loop that owns the `PreToolUse` boundary, denies one real thing (`Read` of a `.contextignore`'d path), and writes a Track-A-readable session record — proving the enforcement boundary and the measurement substrate.

**Architecture:** A small `src/ccgate/run/` package: `policy.py` (pure `.contextignore` match + `PreToolUse` deny callback), `record.py` (SDK-message → Track A JSONL, append-per-turn + terminal marker, repo-encoding run-id), `cli.py` (async orchestrator, dependency-injectable client for testing). `claude-agent-sdk` is an optional extra, lazy-imported only by `ccgate run`. Pure units are unit-tested in-process with a fake client; the real deny + `allowed_tools` shadowing are proven in a `workflow_dispatch` CI job.

**Tech Stack:** Python 3.11+, `claude-agent-sdk` (optional extra `run`), pytest, stdlib `asyncio`/`fnmatch`/`uuid`.

**Spec:** `docs/superpowers/specs/2026-09-28-track-b-b0-run-harness-design.md`

## Global Constraints

- **`claude-agent-sdk` is an optional extra**, not a base dependency: `[project.optional-dependencies] run = ["claude-agent-sdk>=0.2.128"]`. `ccgate audit`/`shape` must import and run without it; `ccgate run` lazy-imports it and errors clearly if absent.
- **Deny via a programmatic `PreToolUse` hook, never `can_use_tool`** (spec §3, parent §12.8). Options must set **`setting_sources=[]`** (disables all filesystem settings so no allow rule shadows the hook) and include `"Read"` in `allowed_tools` (so the deny is proven against a pre-approved tool).
- **Deny return shape:** `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": <str>}}`; allow is `{}`.
- **Session record:** Track A JSONL shape (`{"type":"assistant","timestamp","message":{"model","usage"}}`), written to `<ccgate_home>/runs/<run-id>.jsonl`; **run-id encodes the repo** via `transcript.encode_cwd`; **append-per-turn**, with a terminal `{"type":"ccgate_run_end","status":"complete",...}` marker on clean exit (absence = crashed).
- **`--no-enforce`** produces the measurement baseline; it omits the hook and therefore **genuinely performs the reads the treatment denies** — a measurement tool, not a safe default; its `--help` says so (spec §6).
- **No git hook bypass**; **no hardcoded absolute paths** (use `Path.home()`, `ccgate.state.ccgate_home`, `tmp_path`); **TDD** throughout.
- **The agent cannot run the SDK loop or the full suite to green** (auth, network, `DuplicateHandle`). In-process fake-client tests are agent-runnable; the real deny + shadowing verify only in **CI (`workflow_dispatch`) or the user's terminal** — stated here at plan level (spec §7, §9).

## Review Focus

- **`AssistantMessage.usage` shape differs from the transcript usage block** (e.g. camelCase or nested differently) → `run_audit` reads zeros and every measured run looks miss-free. Pinned by Task 3's roundtrip test (transcript-shaped usage → `read_transcript` parses it) and **must be validated against a real message in Task 7's CI run**; a mismatch there means adding a normalizer in `record.py`.
- **The `Read` tool input key is not `file_path`** → the deny never matches and silently allows. Task 2 tests `file_path`; Task 7's CI deny run is the real proof.
- **Crash mid-run** → the append-per-turn file lacks the terminal marker; it must still parse and read as *incomplete*, not corrupt. Task 4 test.
- **`.contextignore` absent or empty** → allow all, no exception. Task 1 test.
- **Missing/unreadable `--task` file** → clean non-zero exit, no client started, no record written. Task 6 test.

---

### Task 1: `.contextignore` matcher (pure)

**Files:**
- Create: `src/ccgate/run/__init__.py` (empty)
- Create: `src/ccgate/run/policy.py`
- Test: `tests/test_run_policy.py`

**Interfaces:**
- Produces: `load_contextignore(root: Path) -> list[str]` (patterns, `[]` if file absent); `path_is_ignored(path: str, patterns: list[str]) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from ccgate.run.policy import load_contextignore, path_is_ignored


def test_absent_contextignore_returns_empty(tmp_path):
    assert load_contextignore(tmp_path) == []


def test_blank_and_comment_lines_ignored(tmp_path):
    (tmp_path / ".contextignore").write_text("# comment\n\nsecrets/*.txt\n", encoding="utf-8")
    assert load_contextignore(tmp_path) == ["secrets/*.txt"]


def test_glob_matches_full_path_and_basename(tmp_path):
    pats = ["secrets/*.txt", "*.key"]
    assert path_is_ignored("secrets/a.txt", pats) is True
    assert path_is_ignored("/repo/private.key", pats) is True     # basename match
    assert path_is_ignored("src/main.py", pats) is False


def test_empty_patterns_never_match():
    assert path_is_ignored("anything.txt", []) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccgate.run'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ccgate/run/__init__.py` (empty). Create `src/ccgate/run/policy.py`:

```python
"""policy.py — Track B B0 enforcement: .contextignore matching + PreToolUse deny callback."""
from __future__ import annotations

import fnmatch
from pathlib import Path


def load_contextignore(root: Path) -> list[str]:
    """Return non-blank, non-comment patterns from <root>/.contextignore, or [] if absent."""
    f = root / ".contextignore"
    if not f.exists():
        return []
    patterns: list[str] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def path_is_ignored(path: str, patterns: list[str]) -> bool:
    """True if path matches any pattern (glob on the full path or its basename). B0 semantics;
    full gitignore semantics are B1."""
    name = Path(path).name
    for pat in patterns:
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_policy.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/__init__.py src/ccgate/run/policy.py tests/test_run_policy.py
git commit -m "feat(run): .contextignore matcher (pure)"
```

---

### Task 2: `PreToolUse` deny callback

**Files:**
- Modify: `src/ccgate/run/policy.py`
- Test: `tests/test_run_policy.py`

**Interfaces:**
- Consumes: `path_is_ignored`, `load_contextignore`.
- Produces: `make_read_deny_hook(patterns: list[str])` → an async callback `(input_data: dict, tool_use_id, context) -> dict` returning the deny dict for an ignored `Read`, else `{}`.

- [ ] **Step 1: Write the failing test**

```python
import asyncio
from ccgate.run.policy import make_read_deny_hook


def _call(hook, tool_name, tool_input):
    return asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input}, "tuid", None))


def test_denies_ignored_read():
    hook = make_read_deny_hook(["secrets/*.txt"])
    out = _call(hook, "Read", {"file_path": "secrets/a.txt"})
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "secrets/a.txt" in hso["permissionDecisionReason"]


def test_allows_non_ignored_read():
    hook = make_read_deny_hook(["secrets/*.txt"])
    assert _call(hook, "Read", {"file_path": "src/main.py"}) == {}


def test_ignores_non_read_tools():
    hook = make_read_deny_hook(["secrets/*.txt"])
    assert _call(hook, "Bash", {"command": "cat secrets/a.txt"}) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_policy.py -k deny -q`
Expected: FAIL — `ImportError: cannot import name 'make_read_deny_hook'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ccgate/run/policy.py`:

```python
def make_read_deny_hook(patterns: list[str]):
    """Build an async PreToolUse callback that denies Read of a .contextignore'd path.

    Signature matches claude-agent-sdk hooks: (input_data, tool_use_id, context) -> dict.
    Deny dict shape is the SDK's hookSpecificOutput; {} means allow (spec §3).
    """
    async def _hook(input_data: dict, tool_use_id, context) -> dict:
        if input_data.get("tool_name") != "Read":
            return {}
        target = (input_data.get("tool_input") or {}).get("file_path", "")
        if target and path_is_ignored(target, patterns):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{target} is listed in .contextignore",
                }
            }
        return {}
    return _hook
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_policy.py -q`
Expected: PASS (7 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/policy.py tests/test_run_policy.py
git commit -m "feat(run): PreToolUse deny callback for .contextignore'd Read"
```

---

### Task 3: Record mapping + repo-encoding run-id

**Files:**
- Create: `src/ccgate/run/record.py`
- Test: `tests/test_run_record.py`

**Interfaces:**
- Consumes: `ccgate.transcript.encode_cwd`, `ccgate.state.ccgate_home`.
- Produces: `make_run_id(cwd: str) -> str`; `runs_dir() -> Path`; `assistant_entry(model: str, usage: dict, timestamp: str | None = None) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from ccgate.run.record import make_run_id, assistant_entry, runs_dir
from ccgate.transcript import encode_cwd, _parse_usage


def test_run_id_encodes_repo():
    cwd = "C:/proj/CCGate"
    rid = make_run_id(cwd)
    assert rid.startswith(encode_cwd(cwd))     # repo-bucketable prefix
    assert rid != make_run_id(cwd)             # unique per call (timestamp+uuid)


def test_assistant_entry_is_transcript_shaped():
    usage = {"input_tokens": 10, "cache_read_input_tokens": 90,
             "cache_creation_input_tokens": 0, "output_tokens": 5}
    e = assistant_entry("claude-opus-4-8", usage, timestamp="2026-09-28T00:00:00.000Z")
    assert e["type"] == "assistant"
    assert e["timestamp"] == "2026-09-28T00:00:00.000Z"
    assert e["message"]["model"] == "claude-opus-4-8"
    # read_transcript's own parser must accept the usage verbatim:
    parsed = _parse_usage(e["message"])
    assert parsed.input_tokens == 10 and parsed.cache_read_input_tokens == 90


def test_runs_dir_under_ccgate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    assert runs_dir() == tmp_path / "runs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_record.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccgate.run.record'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ccgate/run/record.py`:

```python
"""record.py — Track B run records in Track A's JSONL shape (spec §5)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from ccgate.state import ccgate_home
from ccgate.transcript import encode_cwd


def runs_dir() -> Path:
    return ccgate_home() / "runs"


def make_run_id(cwd: str) -> str:
    """Repo-encoding, unique run id: <encoded-cwd>-<utc-stamp>-<short-uuid>."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{encode_cwd(cwd)}-{stamp}-{uuid.uuid4().hex[:8]}"


def assistant_entry(model: str, usage: dict, timestamp: str | None = None) -> dict:
    """One transcript-shaped assistant entry; usage passed through verbatim (spec §5)."""
    ts = timestamp or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {"type": "assistant", "timestamp": ts, "message": {"model": model, "usage": usage}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_record.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/record.py tests/test_run_record.py
git commit -m "feat(run): record mapping + repo-encoding run-id"
```

---

### Task 4: Append-per-turn writer + terminal marker + completeness (with marker-harmlessness)

**Files:**
- Modify: `src/ccgate/run/record.py`
- Test: `tests/test_run_record.py`

**Interfaces:**
- Consumes: `assistant_entry`, `runs_dir`.
- Produces: `RunRecorder(run_id: str)` with `.path: Path`, `.append_assistant(model, usage)`, `.finish()`; `is_complete(path: Path) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path
from ccgate.run.record import RunRecorder, is_complete
from ccgate.scripts.miss_audit import run_audit


def test_append_then_finish_writes_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-abc")
    rec.append_assistant("claude-opus-4-8", {"input_tokens": 1, "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0, "output_tokens": 1})
    assert is_complete(rec.path) is False      # no marker yet → crashed/incomplete
    rec.finish()
    assert is_complete(rec.path) is True
    lines = rec.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["type"] == "ccgate_run_end"


def test_marker_is_harmless_to_measurement(tmp_path, monkeypatch):
    """run_audit token counts identical with and without the terminal marker (spec §5)."""
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-xyz")
    usage = {"input_tokens": 5, "cache_read_input_tokens": 50,
             "cache_creation_input_tokens": 0, "output_tokens": 3}
    rec.append_assistant("claude-opus-4-8", usage)
    without_marker = run_audit([rec.path], {})["summary"]["tokens"]
    rec.finish()
    with_marker = run_audit([rec.path], {})["summary"]["tokens"]
    assert with_marker == without_marker
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_record.py -k "append or harmless" -q`
Expected: FAIL — `ImportError: cannot import name 'RunRecorder'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ccgate/run/record.py`:

```python
import json


class RunRecorder:
    """Append-per-turn writer: survives a crash with partial-but-valid data (spec §5)."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        d = runs_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{run_id}.jsonl"

    def _append(self, obj: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")

    def append_assistant(self, model: str, usage: dict) -> None:
        self._append(assistant_entry(model, usage))

    def finish(self) -> None:
        self._append({"type": "ccgate_run_end", "status": "complete", "run_id": self.run_id})


def is_complete(path: Path) -> bool:
    """True iff the record ends with a ccgate_run_end marker (else crashed/incomplete)."""
    if not path.exists():
        return False
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return False
    try:
        return json.loads(lines[-1]).get("type") == "ccgate_run_end"
    except json.JSONDecodeError:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_record.py -q`
Expected: PASS (5 tests). The harmlessness test confirms `read_transcript` ignores the marker line.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/record.py tests/test_run_record.py
git commit -m "feat(run): append-per-turn recorder + terminal marker + completeness"
```

---

### Task 5: `cli.py` orchestrator + fake-client end-to-end test

**Files:**
- Create: `src/ccgate/run/cli.py`
- Test: `tests/test_run_cli.py`

**Interfaces:**
- Consumes: `policy.load_contextignore`, `policy.make_read_deny_hook`, `record.RunRecorder`, `record.make_run_id`.
- Produces: `async run_task(task_prompt, *, enforce, cwd, client_factory, options_builder=_build_options) -> Path`; `main(argv) -> None` (parses `--task`, `--no-enforce`); `_build_options(patterns, enforce) -> dict`; `_factory(options)` (real-SDK translation, built in `main`).

- [ ] **Step 1: Write the failing test (fake client captures options; asserts record + enforce wiring)**

```python
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ccgate.run.cli import run_task


@dataclass
class _FakeAssistant:
    model: str
    usage: dict
    content: list = None


class _FakeClient:
    """Stands in for ClaudeSDKClient: records the options it was built with, yields one turn."""
    last_options = None

    def __init__(self, options=None):
        _FakeClient.last_options = options

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def query(self, prompt): self._prompt = prompt

    async def receive_response(self):
        yield _FakeAssistant("claude-opus-4-8",
                             {"input_tokens": 2, "cache_read_input_tokens": 8,
                              "cache_creation_input_tokens": 0, "output_tokens": 1})


def test_enforced_run_wires_hook_and_writes_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / ".contextignore").write_text("secrets/*.txt\n", encoding="utf-8")
    path = asyncio.run(run_task("do the task", enforce=True, cwd=tmp_path,
                                client_factory=_FakeClient))
    opts = _FakeClient.last_options
    assert opts["setting_sources"] == []
    assert "Read" in opts["allowed_tools"]
    assert opts["hooks"]["PreToolUse"]           # hook present when enforcing
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    assert lines[0]["message"]["model"] == "claude-opus-4-8"
    assert lines[-1]["type"] == "ccgate_run_end"


def test_no_enforce_omits_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    asyncio.run(run_task("do the task", enforce=False, cwd=tmp_path, client_factory=_FakeClient))
    assert not _FakeClient.last_options["hooks"]["PreToolUse"]   # empty → no enforcement


def test_missing_task_file_exits_nonzero(tmp_path, capsys):
    import pytest
    from ccgate.run.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["--task", str(tmp_path / "nope.txt")])
    assert ei.value.code == 1
    assert "not found" in capsys.readouterr().err


def _sdk_installed():
    import importlib.util
    return importlib.util.find_spec("claude_agent_sdk") is not None


@pytest.mark.skipif(not _sdk_installed(), reason="claude-agent-sdk not installed")
def test_factory_builds_real_sdk_options():
    """The translation seam (raw dict -> ClaudeAgentOptions/HookMatcher) must not drift from the
    SDK signatures. Constructing options needs no auth/network — catch drift here, not in CI."""
    from ccgate.run.cli import _build_options, _factory
    # Both enforce states must build a real client without raising:
    assert _factory(_build_options(["secrets/*.txt"], enforce=True)) is not None
    assert _factory(_build_options([], enforce=False)) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccgate.run.cli'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ccgate/run/cli.py`:

```python
"""cli.py — `ccgate run`: ccgate-owned ClaudeSDKClient loop (Track B B0)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ccgate.run.policy import load_contextignore, make_read_deny_hook
from ccgate.run.record import RunRecorder, make_run_id


def _build_options(patterns, enforce: bool):
    """Dict-like config the client_factory consumes. NO SDK import here — keeps run_task
    and the in-process fake-client tests SDK-free. PreToolUse holds the RAW async callback;
    main()'s real factory wraps it in a HookMatcher (spec §7 / Task 6 note)."""
    hooks_list = []
    if enforce:
        hooks_list = [make_read_deny_hook(patterns)]   # raw callback; wrapped for the real SDK in _factory
    return {
        "hooks": {"PreToolUse": hooks_list},
        "setting_sources": [],
        "allowed_tools": ["Read", "Bash", "Glob", "Grep"],
    }


def _factory(options):
    """Real-SDK client factory: translate the raw-callback dict → ClaudeAgentOptions/HookMatcher.
    All SDK imports are confined here so run_task/_build_options stay SDK-free and testable."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
    raw = options["hooks"]["PreToolUse"]
    hooks = {"PreToolUse": [HookMatcher(matcher="Read", hooks=raw)]} if raw else {}
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        hooks=hooks,
        setting_sources=options["setting_sources"],
        allowed_tools=options["allowed_tools"],
    ))


async def run_task(task_prompt: str, *, enforce: bool, cwd: Path, client_factory,
                   options_builder=_build_options) -> Path:
    patterns = load_contextignore(cwd)
    options = options_builder(patterns, enforce)
    recorder = RunRecorder(make_run_id(str(cwd)))
    try:
        async with client_factory(options=options) as client:
            await client.query(task_prompt)
            async for msg in client.receive_response():
                model = getattr(msg, "model", None)
                usage = getattr(msg, "usage", None)
                if model is not None and usage is not None:
                    recorder.append_assistant(model, usage)
                    print(f"[turn] {model}")
        recorder.finish()
    except Exception:
        # append-per-turn already persisted completed turns; absence of marker = incomplete
        raise
    return recorder.path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ccgate run")
    parser.add_argument("--task", required=True, type=Path, help="File containing the task prompt")
    parser.add_argument("--no-enforce", action="store_true",
                        help="MEASUREMENT BASELINE ONLY: omit enforcement — the agent will "
                             "genuinely perform reads that enforcement would deny. Not a safe default.")
    args = parser.parse_args(argv)
    if not args.task.exists():
        print(f"ccgate run: task file not found: {args.task}", file=sys.stderr)
        sys.exit(1)
    prompt = args.task.read_text(encoding="utf-8")
    path = asyncio.run(run_task(prompt, enforce=not args.no_enforce, cwd=Path.cwd(),
                                client_factory=_factory))
    print(f"run record: {path}")
```

> **Note for the implementer:** `_build_options` returns a plain dict with the **raw async callback** under `PreToolUse` and imports no SDK — that keeps `run_task` and its fake-client tests runnable without `claude-agent-sdk` installed. The module-level `_factory` is the only place that imports the SDK; it wraps the raw callback in `HookMatcher(matcher="Read", ...)`. `test_factory_builds_real_sdk_options` exercises `_factory` directly (SDK-gated) so signature drift is caught locally, not in CI; if a keyword differs, fix it in `_factory` only, never in the dict `run_task` asserts on.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_cli.py -q`
Expected: PASS — `test_enforced_run_wires_hook_and_writes_record`, `test_no_enforce_omits_hook`, `test_missing_task_file_exits_nonzero`, and (if the SDK is installed) `test_factory_builds_real_sdk_options`; otherwise the last is skipped.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/cli.py tests/test_run_cli.py
git commit -m "feat(run): cli orchestrator with injectable client (fake-tested end-to-end)"
```

---

### Task 6: Optional `run` extra + `dispatch` wiring (lazy import)

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/ccgate/dispatch.py`
- Test: `tests/test_run_dispatch.py`

**Interfaces:**
- Consumes: `ccgate.run.cli.main` (Task 5).
- Produces: `ccgate run ...` routes to `ccgate.run.cli.main(args)`; a clear error if `claude-agent-sdk` is not installed.

- [ ] **Step 1: Write the failing test**

```python
import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).parent.parent


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "ccgate.dispatch", *args],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        cwd=str(WORKTREE),
        env={**__import__("os").environ, "PYTHONPATH": str(WORKTREE / "src")},
    )


def test_run_appears_in_usage():
    r = _run_cli("help")
    assert "run" in r.stdout


def test_unknown_subcommand_still_errors():
    r = _run_cli("bogus")
    assert r.returncode == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_dispatch.py -q`
Expected: FAIL — `run` not present in usage output.

- [ ] **Step 3: Write minimal implementation**

In `pyproject.toml`, add under `[project.optional-dependencies]`:

```toml
run = ["claude-agent-sdk>=0.2.128"]
```

In `src/ccgate/dispatch.py`, add the branch (before the `else`):

```python
    elif subcmd == "run":
        try:
            from ccgate.run.cli import main as _main
        except ImportError:
            print("ccgate run requires the 'run' extra: pip install -e '.[run]'", file=sys.stderr)
            sys.exit(1)
        _main(args[1:])
```

And add to `_usage()`'s subcommands block:

```python
        "  run --task F      Owned SDK loop with enforcement (Track B)\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_dispatch.py -q`
Expected: PASS — `run` shows in usage; unknown subcommand still exits 1. (`cli.py` already exists from Task 5, so the lazy import resolves.)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ccgate/dispatch.py tests/test_run_dispatch.py
git commit -m "feat(run): optional run extra + dispatch wiring (lazy import)"
```

---

### Task 7: CI integration — real deny + `allowed_tools` shadowing (user/CI verified)

**Files:**
- Create: `.github/workflows/b0-run-integration.yml`
- Create: `tests/fixtures/run/target_secret.txt` (committed, safe content)
- Create: `tests/fixtures/run/.contextignore`
- Create: `tests/fixtures/run/task.txt`
- Create: `scripts/b0_ci_probe.py` (driver invoked by the workflow)

**Interfaces:** none (integration).

- [ ] **Step 1: Create the pinned, public-repo-safe fixtures**

`tests/fixtures/run/target_secret.txt`:
```
FIXTURE-ONLY marker content: SENTINEL_B0_READ_OK. Nothing real here.
```
`tests/fixtures/run/.contextignore`:
```
target_secret.txt
```
`tests/fixtures/run/task.txt`:
```
Read the file tests/fixtures/run/target_secret.txt and print its exact contents, then stop.
```

- [ ] **Step 2: Write the CI driver `scripts/b0_ci_probe.py`**

Drives two real runs and asserts the shadowing red→green (spec §7). Pinned to the fixture; never touches a real path.

```python
"""b0_ci_probe.py — CI-only: prove the PreToolUse deny fires even for a Read in allowed_tools.

Red state: --no-enforce (hook absent, Read allowed) → the fixture content IS read (SENTINEL appears).
Green state: enforced (hook present, Read allowed, same config) → the read is DENIED (SENTINEL absent,
deny reason present). Proves the hook overrides allowed_tools, not just that some deny exists.
"""
import subprocess, sys
from pathlib import Path

SENTINEL = "SENTINEL_B0_READ_OK"
TASK = "tests/fixtures/run/task.txt"


def _run(no_enforce: bool) -> str:
    args = [sys.executable, "-m", "ccgate.dispatch", "run", "--task", TASK]
    if no_enforce:
        args.append("--no-enforce")
    r = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       cwd=str(Path(__file__).parent.parent))
    return r.stdout + r.stderr


def main() -> int:
    baseline = _run(no_enforce=True)
    treatment = _run(no_enforce=False)
    ok_red = SENTINEL in baseline          # without the hook, the read happened
    ok_green = SENTINEL not in treatment   # with the hook, it did not
    print(f"PROBE_RED_READ_HAPPENED={ok_red}")
    print(f"PROBE_GREEN_READ_DENIED={ok_green}")
    return 0 if (ok_red and ok_green) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Write the workflow `.github/workflows/b0-run-integration.yml`**

```yaml
name: b0-run-integration
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
      - name: B0 deny + shadowing probe
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        run: python scripts/b0_ci_probe.py
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/b0-run-integration.yml scripts/b0_ci_probe.py tests/fixtures/run
git commit -m "test(run): CI integration probe — deny + allowed_tools shadowing red->green"
```

- [ ] **Step 5: USER/CI VERIFICATION (agent cannot run this)**

The agent cannot run the real SDK loop (auth/network/`DuplicateHandle`). Merge the workflow to a dispatchable branch (or `main`) and trigger it, or run `python scripts/b0_ci_probe.py` in the user's terminal with `CLAUDE_CODE_OAUTH_TOKEN` set and `claude` + `pip install -e '.[run]'` present.
Expected: `PROBE_RED_READ_HAPPENED=True` and `PROBE_GREEN_READ_DENIED=True` (exit 0). Record the output here as evidence:

Evidence log (fill in): `PROBE_RED_READ_HAPPENED=____  PROBE_GREEN_READ_DENIED=____`

**Also validate the record shape (Review Focus #1):** after a real run, `ccgate audit ~/.ccgate/runs/` should show non-zero token totals for the run (proving `AssistantMessage.usage` mapped into a shape `run_audit` reads). If totals are zero, add a usage-key normalizer in `record.py` and re-verify.

---

## Sequencing note

Tasks 1–6 run in strict numeric order (1→2→3→4→5→6→7) — file order now matches execution
order, no out-of-order steps. Tasks 1–5 are agent-runnable in-process (TDD; pure units +
fake client; `test_factory_builds_real_sdk_options` runs too when the SDK is pip-installed,
else skips). Task 6 (dispatch) depends on Task 5's `cli.py`, which precedes it. Task 7 is the
only real-SDK verification and runs in CI / the user's terminal.
