# Phase 2 Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `PreToolUse` hook (`pre_tool.py`) that independently toggles three enforcement rules — `.contextignore` glob deny, read-cache deny, and Bash output rewriting — and add G4/G5 gates to `miss_audit.py --assert`.

**Architecture:** Single hook file (`pre_tool.py`) runs three rule functions in order; the first non-None result wins. Read-cache state lives in a separate JSON file (`{session_id}-read-cache.json`) with its own lock, completely decoupled from Phase 1 session state. All rules default to `false` and are independently enabled via config or env var.

**Tech Stack:** Python 3.11+, stdlib only (`fnmatch`, `os`, `json`, `sys`, `time`, `pathlib`, `datetime`). No third-party packages. Uses `ccgate.state.acquire_lock` and `ccgate.config.load_config` from Phase 1.

**Spec:** `docs/superpowers/specs/2026-09-20-phase2-enforcement-design.md`

## Global Constraints

- Python 3.11+, stdlib only — no third-party packages
- `CCGATE_HOME` env var overrides `~/.ccgate` for all state functions (`ccgate.state.ccgate_home()`)
- `os.replace(str(tmp), str(path))` for all atomic writes (wrapping in `str()` for Windows compat)
- `acquire_lock(session_id)` from `ccgate.state` for all read-mutate-write on read-cache state; read-cache lock name = `f"{session_id}-read-cache"`
- Exit 0 always — all hook exceptions caught with `try/except Exception: pass` at top level
- Deny reason always states the override mechanism (config key to set)
- Every rule toggled independently by its own config boolean (default `false`)
- Config overrides via env var: `CCGATE_CONTEXTIGNORE_ENABLED=1`, `CCGATE_READ_CACHE_ENABLED=1`, `CCGATE_BASH_REWRITE_ENABLED=1` (uses `bool()` coercion — any non-empty string → True)
- `config.py` DEFAULTS must declare every new key before tests can override it via env var

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| CREATE | `tests/fixtures/g4g5_clean.jsonl` | 15-turn clean-cache fixture for G4/G5 pass |
| CREATE | `tests/fixtures/g4g5_dirty.jsonl` | 10-turn alternating-model fixture for G4/G5 fail |
| MODIFY | `src/ccgate/scripts/miss_audit.py` | Add G4/G5 checks to `--assert` mode |
| CREATE | `tests/test_miss_audit_g4g5.py` | G4/G5 gate tests |
| MODIFY | `src/ccgate/config.py` | Add `contextignoreEnabled`, `bashRewriteEnabled` to DEFAULTS |
| CREATE | `src/ccgate/hooks/pre_tool.py` | PreToolUse hook with all three rules |
| CREATE | `tests/test_pre_tool.py` | Subprocess integration tests for all three rules |
| MODIFY | `hooks/hooks.json` | Register PreToolUse hook |
| MODIFY | `.claude-plugin/plugin.json` | Register PreToolUse hook |
| CREATE | `tests/perf_pre_tool.py` | G7 latency gate for pre_tool.py |

---

## Task 1: G4/G5 fixtures + miss_audit.py gate additions + tests

**Files:**
- Create: `tests/fixtures/g4g5_clean.jsonl`
- Create: `tests/fixtures/g4g5_dirty.jsonl`
- Modify: `src/ccgate/scripts/miss_audit.py` (lines 207–233, the `--assert` section)
- Create: `tests/test_miss_audit_g4g5.py`

**Interfaces:**
- Consumes: `run_audit()` return dict from `miss_audit.py` — keys: `summary.hit_ratio`, `summary.total_requests`, `summary.total_misses`, `summary.avoidable_usd`, `summary.total_usd`, `misses` (list of `{"cause": str, "count": int, ...}`)
- Also consumes: `taxonomy.D1_MODEL_SWITCH`, `taxonomy.D1_TOOLS_CHANGED` — already imported at top of `miss_audit.py` as `from ccgate import taxonomy`
- Also consumes: config keys `hitRatioFloor` (default 0.85), `minRequestsForRatio` (default 10) — already in DEFAULTS
- Produces: `--assert` exits 0 on clean sessions, exits 1 (printing G4/G5 lines to stderr) on dirty ones

**Before you start:** Read `src/ccgate/scripts/miss_audit.py` lines 198–233 (the `main()` function) to understand the current `--assert` implementation. Read `src/ccgate/taxonomy.py` to see `D1_MODEL_SWITCH` and `D1_TOOLS_CHANGED` constants.

- [ ] **Step 1: Create `tests/fixtures/g4g5_clean.jsonl`**

15 assistant turns, consistent model, all cache hits after turn 1. Turn 1 creates cache (`cache_creation=5000, cache_read=0`); turns 2–15 read from cache (`cache_creation=0, cache_read=5000`). Under `classify_requests`, turn 1 is a HIT (expected_cache=0 → re_processed=0). Turns 2–15 are HITs. `hit_ratio = 1.0`, zero misses.

Write exactly:
```
{"type":"assistant","timestamp":"2026-09-20T10:00:00Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:00:10Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:00:20Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:00:30Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:00:40Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:00:50Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:01:00Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:01:10Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:01:20Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:01:30Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:01:40Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:01:50Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:02:00Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:02:10Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T10:02:20Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":0,"cache_read_input_tokens":5000,"output_tokens":100}}}
```

- [ ] **Step 2: Create `tests/fixtures/g4g5_dirty.jsonl`**

10 turns alternating between two models with no cache reads. Turn 1 (model A) → HIT (expected_cache=0, re_processed=0). Turns 2–10 alternate models; each has `cache_creation=5000, cache_read=0`, so `re_processed = max(0, 5000 - 0) = 5000` → MISS (5000/5000=100%>5%, 5000>2000). `attribute_miss` fires `D1_MODEL_SWITCH` for turns 2–10. `hit_ratio = 1/10 = 0.10 < 0.85`. G4 fails, G5 fails (9 D1.model_switch).

Write exactly:
```
{"type":"assistant","timestamp":"2026-09-20T11:00:00Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:00:10Z","message":{"model":"claude-opus-5","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:00:20Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:00:30Z","message":{"model":"claude-opus-5","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:00:40Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:00:50Z","message":{"model":"claude-opus-5","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:01:00Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:01:10Z","message":{"model":"claude-opus-5","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:01:20Z","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
{"type":"assistant","timestamp":"2026-09-20T11:01:30Z","message":{"model":"claude-opus-5","usage":{"input_tokens":5000,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":100}}}
```

- [ ] **Step 3: Write failing tests in `tests/test_miss_audit_g4g5.py`**

```python
"""test_miss_audit_g4g5.py — G4/G5 gate tests against synthesized fixtures."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")
FIXTURES = Path(__file__).parent / "fixtures"
CLEAN = FIXTURES / "g4g5_clean.jsonl"
DIRTY = FIXTURES / "g4g5_dirty.jsonl"


def _run(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    return subprocess.run(
        [PYTHON, "-m", "ccgate.scripts.miss_audit", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_clean_assert_exits_0():
    """g4g5_clean.jsonl: hit_ratio=1.0, zero D1 misses — --assert exits 0."""
    r = _run("--assert", str(CLEAN))
    assert r.returncode == 0, r.stderr


def test_dirty_assert_exits_1():
    """g4g5_dirty.jsonl: hit_ratio=0.1, 9 D1.model_switch — --assert exits 1."""
    r = _run("--assert", str(DIRTY))
    assert r.returncode == 1


def test_dirty_reports_g4():
    """G4 failure message appears in stderr."""
    r = _run("--assert", str(DIRTY))
    assert "G4" in r.stderr, f"stderr: {r.stderr!r}"


def test_dirty_reports_g5():
    """G5 failure message appears in stderr."""
    r = _run("--assert", str(DIRTY))
    assert "G5" in r.stderr, f"stderr: {r.stderr!r}"


def test_json_output_includes_hit_ratio():
    """--json output has summary.hit_ratio field."""
    import json
    r = _run("--json", str(CLEAN))
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "hit_ratio" in data["summary"]
    assert data["summary"]["hit_ratio"] == pytest.approx(1.0)
```

Run: `pytest tests/test_miss_audit_g4g5.py -v`
Expected: 5 FAILs (G4/G5 not implemented yet).

- [ ] **Step 4: Add G4/G5 to `miss_audit.py --assert`**

In `main()`, replace the current `--assert` block (the final `if args.assert_mode` check at the bottom) with:

```python
    if args.assert_mode:
        failures: list[str] = []
        s = report["summary"]
        # G4: hit ratio
        min_reqs = config.get("minRequestsForRatio", 10)
        floor = config.get("hitRatioFloor", 0.85)
        if s["total_requests"] >= min_reqs and s["hit_ratio"] < floor:
            failures.append(
                f"G4 FAIL: hit_ratio={s['hit_ratio']:.3f} < {floor}"
                f" ({s['total_requests'] - s['total_misses']}/{s['total_requests']} hits)"
            )
        # G5: zero D1.model_switch / D1.tools_changed
        cause_map = {m["cause"]: m["count"] for m in report["misses"]}
        g5_count = (cause_map.get(taxonomy.D1_MODEL_SWITCH, 0)
                    + cause_map.get(taxonomy.D1_TOOLS_CHANGED, 0))
        if g5_count > 0:
            failures.append(
                f"G5 FAIL: {g5_count} miss(es) of type D1.model_switch or D1.tools_changed"
            )
        # existing gate: any avoidable spend
        if s["avoidable_usd"] > 0:
            failures.append(
                f"avoidable: ${s['avoidable_usd']:.4f} of ${s['total_usd']:.4f} session spend"
            )
        if failures:
            for msg in failures:
                print(msg, file=sys.stderr)
            sys.exit(1)
```

The `taxonomy` import is already at the top of `miss_audit.py` (`from ccgate import taxonomy`) — no new import needed.

- [ ] **Step 5: Verify tests pass**

Run: `pytest tests/test_miss_audit_g4g5.py -v`
Expected: 5 passed.

Run: `python -m pytest --tb=short -q`
Expected: all prior tests still pass + 5 new ones.

- [ ] **Step 6: Manual gate verification**

Run: `python -m ccgate.scripts.miss_audit --assert tests/fixtures/g4g5_clean.jsonl`
Expected: exits 0, no output to stderr.

Run: `python -m ccgate.scripts.miss_audit --assert tests/fixtures/g4g5_dirty.jsonl`
Expected: exits 1, stderr contains "G4 FAIL" and "G5 FAIL".

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/g4g5_clean.jsonl tests/fixtures/g4g5_dirty.jsonl \
        src/ccgate/scripts/miss_audit.py tests/test_miss_audit_g4g5.py
git commit -m "feat: G4/G5 gates — hit-ratio and avoidable-miss assertions in miss_audit --assert"
```

---

## Task 2: `config.py` additions + `pre_tool.py` skeleton + contextignore rule + tests

**Files:**
- Modify: `src/ccgate/config.py` (add two new DEFAULTS keys)
- Create: `src/ccgate/hooks/pre_tool.py` (structure + `_check_contextignore` + `main`)
- Create: `tests/test_pre_tool.py` (contextignore tests only)

**Interfaces:**
- Consumes from `ccgate.config.load_config()`: `config.get("contextignoreEnabled", False)` (new key)
- Consumes from `ccgate.state`: `ccgate_home()` (for later tasks; import now)
- Produces: `src/ccgate/hooks/pre_tool.py` with `main()` entry point and `_check_contextignore(tool_name, tool_input, config) -> Optional[dict]`

**Before you start:** Read `src/ccgate/hooks/post_tool.py` to see the Phase 1 hook structure (stdin parsing, try/except guard, json.dumps output). Your hook must follow the same pattern.

**How config env-var overrides work:** `load_config()` reads `~/.ccgate/config.json` first, then project `.ccgate/config.json`. For tests, set env var `CCGATE_CONTEXTIGNORE_ENABLED=1` — `load_config()` detects `CCGATE_` + `_to_upper_snake("contextignoreEnabled")` = `CCGATE_CONTEXTIGNORE_ENABLED`, coerces with `bool("1")` = True. This only works if the key is in `DEFAULTS`.

**How contextignore subprocess cwd works:** `_load_contextignore_patterns()` reads `Path.cwd() / ".contextignore"`. In subprocess tests, pass `cwd=tmp_path` to `subprocess.run()` and write `.contextignore` there.

- [ ] **Step 1: Add new config DEFAULTS in `config.py`**

In `src/ccgate/config.py`, inside the `DEFAULTS` dict, add two new keys. Insert them after `"readCacheEnabled": False,`:

```python
    "contextignoreEnabled": False,
    "bashRewriteEnabled": False,
```

No other changes to `config.py`.

- [ ] **Step 2: Write failing tests for contextignore in `tests/test_pre_tool.py`**

```python
"""test_pre_tool.py — subprocess integration tests for pre_tool.py hook."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def _run(payload: dict, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.pre_tool"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _base_env(tmp_path: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    env["CCGATE_HOME"] = str(tmp_path)
    if extra:
        env.update(extra)
    return env


# ── contextignore ──────────────────────────────────────────────────────────


def test_contextignore_denies_matching_path(tmp_path):
    """Read on a *.json path denied when .contextignore has *.json and rule is enabled."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"
    assert "contextignore" in out["permissionDecisionReason"]
    assert "*.json" in out["permissionDecisionReason"]


def test_contextignore_allows_non_matching_path(tmp_path):
    """Read on a .py path allowed even when .contextignore has *.json."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/main.py"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_contextignore_disabled_allows_matching_path(tmp_path):
    """Rule off by default — matching path passes through."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path)  # no CCGATE_CONTEXTIGNORE_ENABLED
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_contextignore_applies_to_edit(tmp_path):
    """Rule applies to Edit tool as well."""
    (tmp_path / ".contextignore").write_text("secrets.txt\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Edit",
                "tool_input": {"file_path": "/home/user/secrets.txt"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_does_not_apply_to_grep(tmp_path):
    """Grep is not subject to contextignore (no file_path — pattern only)."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Grep",
                "tool_input": {"pattern": "foo", "path": "/project"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_contextignore_backslash_normalized(tmp_path):
    """Windows backslash in file_path is normalized before matching."""
    (tmp_path / ".contextignore").write_text("*.json\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "C:\\project\\config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_crlf_stripped(tmp_path):
    """CRLF line endings in .contextignore are handled correctly."""
    (tmp_path / ".contextignore").write_bytes(b"*.log\r\n")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/var/app.log"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_comment_lines_skipped(tmp_path):
    """Lines starting with # are skipped in .contextignore."""
    (tmp_path / ".contextignore").write_text("# this is a comment\n*.log\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/var/app.log"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"


def test_contextignore_absent_file_passes_through(tmp_path):
    """No .contextignore → allow everything even with rule enabled."""
    env = _base_env(tmp_path, {"CCGATE_CONTEXTIGNORE_ENABLED": "1"})
    payload = {"session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": "/project/config.json"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_hook_exit_0_on_empty_stdin(tmp_path):
    """Hook exits 0 even when stdin is empty (no payload)."""
    env = _base_env(tmp_path)
    r = subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.pre_tool"],
        input="",
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0
```

Run: `pytest tests/test_pre_tool.py -v`
Expected: 9 FAILs (module doesn't exist yet).

- [ ] **Step 3: Implement `src/ccgate/hooks/pre_tool.py` (contextignore rule only)**

```python
"""pre_tool.py — PreToolUse hook: contextignore, read-cache, bash rewriting (Phase 2)."""
from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ccgate.config import load_config
from ccgate.state import acquire_lock, ccgate_home


# ── read-cache helpers (used by Task 3) ───────────────────────────────────────

def _rc_path(session_id: str) -> Path:
    return ccgate_home() / "sessions" / f"{session_id}-read-cache.json"


def _read_rc(session_id: str) -> dict:
    path = _rc_path(session_id)
    if not path.exists():
        return {"reads": {}, "file_log": [], "seq": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"reads": {}, "file_log": [], "seq": 0}


def _write_rc(session_id: str, data: dict) -> None:
    path = _rc_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


# ── rule 1: contextignore ─────────────────────────────────────────────────────

def _load_contextignore_patterns() -> list[str]:
    patterns: list[str] = []
    for candidate in [
        Path.cwd() / ".contextignore",
        Path.home() / ".claude" / ".contextignore",
    ]:
        if not candidate.exists():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.replace("\r", "").strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        except OSError:
            pass
    return patterns


def _check_contextignore(
    tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    if not config.get("contextignoreEnabled", False):
        return None
    if tool_name not in ("Read", "Edit", "Write"):
        return None
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    name = Path(file_path).name
    for pattern in _load_contextignore_patterns():
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"blocked by .contextignore pattern {pattern!r}"
                    " — use Grep for targeted search."
                    " To override: remove the pattern or set"
                    " contextignoreEnabled: false in .claude/settings.json."
                ),
            }
    return None


# ── rule 2: read-cache (stub — implemented in Task 3) ─────────────────────────

def _check_read_cache(
    session_id: str, tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    return None  # implemented in Task 3


# ── rule 3: bash rewriting (stub — implemented in Task 4) ─────────────────────

def _apply_bash_rewrite(
    tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    return None  # implemented in Task 4


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    result: Optional[dict] = None
    try:
        config = load_config()
        result = _check_contextignore(tool_name, tool_input, config)
        if result is None:
            result = _check_read_cache(session_id, tool_name, tool_input, config)
        if result is None:
            result = _apply_bash_rewrite(tool_name, tool_input, config)
    except Exception:
        pass

    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify contextignore tests pass**

Run: `pytest tests/test_pre_tool.py -v`
Expected: 9 passed.

Run: `python -m pytest --tb=short -q`
Expected: all prior tests pass + 9 new ones (total count increases by 9).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/config.py src/ccgate/hooks/pre_tool.py tests/test_pre_tool.py
git commit -m "feat: pre_tool.py skeleton + contextignore rule; add contextignoreEnabled/bashRewriteEnabled defaults"
```

---

## Task 3: Read-cache rule

**Files:**
- Modify: `src/ccgate/hooks/pre_tool.py` (replace `_check_read_cache` stub with full implementation)
- Modify: `tests/test_pre_tool.py` (append read-cache tests)

**Interfaces:**
- Consumes: `acquire_lock(session_id)` from `ccgate.state` — context manager, uses `{CCGATE_HOME}/locks/{session_id}.lock`; lock name for read-cache = `f"{session_id}-read-cache"` (note: NOT the bare session_id — use the `-read-cache` suffix to avoid lock contention with Phase 1's post_tool)
- Consumes config keys: `readCacheEnabled` (bool, default False), `staleTimeMs` (int ms, default 600000), `staleFiles` (int, default 8)
- Produces: `_check_read_cache(session_id, tool_name, tool_input, config)` — fully functional

**Algorithm summary:**
1. Return None immediately if `readCacheEnabled` is False or `tool_name != "Read"`
2. Build key: `f"{file_path}:{offset}:{limit}"` where offset/limit come from `tool_input.get("offset")` and `tool_input.get("limit")` (may be None)
3. Inside `acquire_lock(f"{session_id}-read-cache")`:
   - Load read-cache JSON (empty if absent)
   - **Key absent**: record entry, append to file_log, increment seq, write, return None (allow)
   - **Key present, mtime changed** (including OSError → treat as changed): update entry, append to file_log, write, return None (allow)
   - **Key present, mtime unchanged**: check staleness — if stale (either condition), evict + re-record + write + return None; if not stale, return deny (do NOT write — nothing changed)

**Staleness formula:** `effective_staleFiles = round(config["staleFiles"] * actual_window / 200_000)` where `actual_window` is read from `{session_id}-statusline.json` (written by Phase 1); defaults to 200,000 if absent/unparseable. Stale if: `elapsed_ms > staleTimeMs` OR `len(set(file_log[entry_idx + 1:])) > effective_staleFiles`.

- [ ] **Step 1: Append read-cache tests to `tests/test_pre_tool.py`**

Add this at the bottom of the file:

```python
# ── read cache ────────────────────────────────────────────────────────────────


def test_read_cache_first_read_allowed(tmp_path):
    """First Read of a file is always allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload = {"session_id": "rc1", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_identical_repeat_denied(tmp_path):
    """Second Read with same (path, offset, limit) + unchanged mtime → denied."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload = {"session_id": "rc2", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read — populates cache
    r = _run(payload, env)  # second read — should deny
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("permissionDecision") == "deny"
    assert "read cache" in out["permissionDecisionReason"]
    assert "readCacheEnabled" in out["permissionDecisionReason"]


def test_read_cache_changed_mtime_allowed(tmp_path):
    """Re-read after file modification is allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload = {"session_id": "rc3", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read
    # Modify file (and ensure mtime changes — write new content)
    target.write_text("changed", encoding="utf-8")
    # Force mtime change on Windows (resolution may be coarse)
    import time as _time
    _time.sleep(0.01)
    target.write_text("changed2", encoding="utf-8")
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_different_offset_allowed(tmp_path):
    """Different offset on same file is a distinct cache key — allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello\nworld\n", encoding="utf-8")
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    payload1 = {"session_id": "rc4", "tool_name": "Read",
                 "tool_input": {"file_path": str(target), "offset": 0, "limit": 5}}
    payload2 = {"session_id": "rc4", "tool_name": "Read",
                 "tool_input": {"file_path": str(target), "offset": 5, "limit": 5}}
    _run(payload1, env)
    r = _run(payload2, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_staletime_eviction_allows(tmp_path):
    """After staleTimeMs, the cache entry is evicted and re-read is allowed."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    # Set staleTimeMs to 0 via env var (immediate staleness)
    env = _base_env(tmp_path, {
        "CCGATE_READ_CACHE_ENABLED": "1",
        "CCGATE_STALE_TIME_MS": "0",
    })
    payload = {"session_id": "rc5", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read
    r = _run(payload, env)  # second read — staleTimeMs=0 → already stale → allow
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_stalefiles_eviction_allows(tmp_path):
    """After staleFiles distinct files are read, cache entry is stale — re-read allowed."""
    target = tmp_path / "main.py"
    target.write_text("hello", encoding="utf-8")
    # Set staleFiles to 0 via env var so ANY subsequent read makes entry stale
    env = _base_env(tmp_path, {
        "CCGATE_READ_CACHE_ENABLED": "1",
        "CCGATE_STALE_FILES": "0",
    })
    payload = {"session_id": "rc6", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)  # first read of target
    # Read a different file (adds to file_log after target's idx)
    other = tmp_path / "other.py"
    other.write_text("world", encoding="utf-8")
    _run({"session_id": "rc6", "tool_name": "Read",
          "tool_input": {"file_path": str(other)}}, env)
    # Now re-read target — staleFiles=0 → effective=0, distinct_after=1 > 0 → stale → allow
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_disabled_passes_through(tmp_path):
    """readCacheEnabled=false (default) → rule inactive, no deny."""
    target = tmp_path / "file.py"
    target.write_text("hello", encoding="utf-8")
    env = _base_env(tmp_path)  # no CCGATE_READ_CACHE_ENABLED
    payload = {"session_id": "rc7", "tool_name": "Read",
                "tool_input": {"file_path": str(target)}}
    _run(payload, env)
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_read_cache_missing_file_allowed(tmp_path):
    """OSError on os.stat (file missing) → treat as changed → allow."""
    env = _base_env(tmp_path, {"CCGATE_READ_CACHE_ENABLED": "1"})
    ghost = str(tmp_path / "does_not_exist.py")
    payload = {"session_id": "rc8", "tool_name": "Read",
                "tool_input": {"file_path": ghost}}
    _run(payload, env)  # first read (mtime=None recorded)
    r = _run(payload, env)  # second read — mtime still None → no change? 
    # mtime=None on first, None on second → treat as "unchanged" → deny.
    # Wait: spec says "OSError → treat as mtime changed → allow". So each call
    # returns OSError → each call treats as changed → always allow.
    assert r.returncode == 0
    assert r.stdout.strip() == ""
```

Run: `pytest tests/test_pre_tool.py -v -k "read_cache"`
Expected: FAILs (stub returns None).

- [ ] **Step 2: Implement `_check_read_cache` in `pre_tool.py`**

Replace the stub:

```python
def _window_size(session_id: str) -> int:
    snap = ccgate_home() / "sessions" / f"{session_id}-statusline.json"
    if not snap.exists():
        return 200_000
    try:
        data = json.loads(snap.read_text(encoding="utf-8"))
        return int(data.get("window_tokens", 200_000)) or 200_000
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 200_000


def _is_stale(entry: dict, file_log: list, config: dict, session_id: str) -> bool:
    try:
        ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
        elapsed_ms = (datetime.now(timezone.utc) - ts).total_seconds() * 1000
    except (KeyError, ValueError):
        return True  # unparseable ts → treat as stale
    if elapsed_ms > config.get("staleTimeMs", 600_000):
        return True
    stale_files = config.get("staleFiles", 8)
    actual_window = _window_size(session_id)
    effective = round(stale_files * actual_window / 200_000)
    idx = entry.get("idx", 0)
    distinct_after = len(set(file_log[idx + 1:]))
    return distinct_after > effective


def _check_read_cache(
    session_id: str, tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    if not config.get("readCacheEnabled", False):
        return None
    if tool_name != "Read":
        return None
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    key = f"{file_path}:{offset}:{limit}"

    try:
        current_mtime: float | None = os.stat(file_path).st_mtime
    except OSError:
        current_mtime = None

    with acquire_lock(f"{session_id}-read-cache"):
        rc = _read_rc(session_id)
        reads: dict = rc.setdefault("reads", {})
        file_log: list = rc.setdefault("file_log", [])

        if key not in reads:
            idx = len(file_log)
            reads[key] = {
                "mtime": current_mtime,
                "ts": datetime.now(timezone.utc).isoformat(),
                "idx": idx,
            }
            file_log.append(file_path)
            rc["seq"] = rc.get("seq", 0) + 1
            _write_rc(session_id, rc)
            return None

        entry = reads[key]
        stored_mtime = entry.get("mtime")
        if current_mtime is None or stored_mtime is None or current_mtime != stored_mtime:
            idx = len(file_log)
            reads[key] = {
                "mtime": current_mtime,
                "ts": datetime.now(timezone.utc).isoformat(),
                "idx": idx,
            }
            file_log.append(file_path)
            rc["seq"] = rc.get("seq", 0) + 1
            _write_rc(session_id, rc)
            return None

        if _is_stale(entry, file_log, config, session_id):
            idx = len(file_log)
            reads[key] = {
                "mtime": current_mtime,
                "ts": datetime.now(timezone.utc).isoformat(),
                "idx": idx,
            }
            file_log.append(file_path)
            rc["seq"] = rc.get("seq", 0) + 1
            _write_rc(session_id, rc)
            return None

        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"read cache: {file_path!r} (lines {offset}–{limit}, unchanged)"
                " was already read this session — use the earlier result."
                " To override: set readCacheEnabled: false."
            ),
        }
```

Note on `test_read_cache_missing_file_allowed`: when `current_mtime=None` on the first call, the stored mtime is also None. On the second call, `current_mtime=None` and `stored_mtime=None`. The condition `current_mtime is None or stored_mtime is None or current_mtime != stored_mtime` → `True` (because `current_mtime is None`). So every OSError call is treated as changed and allowed. The test is correct.

- [ ] **Step 3: Verify read-cache tests pass**

Run: `pytest tests/test_pre_tool.py -v -k "read_cache"`
Expected: 8 passed.

Run: `python -m pytest --tb=short -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/ccgate/hooks/pre_tool.py tests/test_pre_tool.py
git commit -m "feat: read-cache rule in pre_tool.py — deny unchanged re-reads within session"
```

---

## Task 4: Bash rewriting + hook registration + tests

**Files:**
- Modify: `src/ccgate/hooks/pre_tool.py` (replace `_apply_bash_rewrite` stub)
- Modify: `hooks/hooks.json` (add PreToolUse entry)
- Modify: `.claude-plugin/plugin.json` (add PreToolUse entry)
- Modify: `tests/test_pre_tool.py` (append bash rewrite tests)

**Interfaces:**
- Consumes config keys: `bashRewriteEnabled` (bool, default False), `bashRewriteRules` (list, default [])
- Produces: `_apply_bash_rewrite(tool_name, tool_input, config)` — fully functional

**Built-in rules (exact prefix → filter mappings):**
| Prefix | Filter appended |
|---|---|
| `pytest` | `2>&1 \| tail -100` |
| `cargo test` | `2>&1 \| tail -100` |
| `jest` | `2>&1 \| tail -100` |
| `go test` | `2>&1 \| tail -100` |
| `npm test` | `2>&1 \| tail -100` |
| `mvn test` | `2>&1 \| tail -100` |
| `grep` | `\| head -100` |
| `find` | `\| head -100` |

**Matching:** `command.lstrip().startswith(prefix)` — literal, case-sensitive, first match wins. Output: `f"{command} {filter_suffix}"` (filter appended, not replacing).

**Config extension:** `bashRewriteRules` list of `{"prefix": str, "filter": str}`. User rules appended after built-ins. A rule whose prefix contains `. * + ? [ (` is rejected with a logged message to stderr but does NOT exit non-zero.

- [ ] **Step 1: Append bash rewrite tests to `tests/test_pre_tool.py`**

Add at the bottom:

```python
# ── bash rewriting ────────────────────────────────────────────────────────────


def test_bash_rewrite_pytest_gets_tail(tmp_path):
    """'pytest' command gets '2>&1 | tail -100' appended."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b1", "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/ -v"}}
    r = _run(payload, env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert "updatedInput" in out
    assert out["updatedInput"]["command"] == "pytest tests/ -v 2>&1 | tail -100"


def test_bash_rewrite_grep_gets_head(tmp_path):
    """'grep' command gets '| head -100' appended."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b2", "tool_name": "Bash",
                "tool_input": {"command": "grep -r foo ."}}
    r = _run(payload, env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["updatedInput"]["command"] == "grep -r foo . | head -100"


def test_bash_rewrite_no_match_passes_through(tmp_path):
    """Non-matching command passes through unchanged."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b3", "tool_name": "Bash",
                "tool_input": {"command": "ls -la"}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_bash_rewrite_disabled_passes_through(tmp_path):
    """Rule inactive by default — pytest command not rewritten."""
    env = _base_env(tmp_path)  # no CCGATE_BASH_REWRITE_ENABLED
    payload = {"session_id": "b4", "tool_name": "Bash",
                "tool_input": {"command": "pytest"}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_bash_rewrite_user_rule_applied(tmp_path):
    """User rule in bashRewriteRules config is applied after built-ins."""
    config_dir = tmp_path / ".ccgate"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"bashRewriteRules": [{"prefix": "mytest", "filter": "| tail -50"}]}),
        encoding="utf-8",
    )
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b5", "tool_name": "Bash",
                "tool_input": {"command": "mytest run"}}
    # subprocess cwd=tmp_path so project .ccgate/config.json is found
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["updatedInput"]["command"] == "mytest run | tail -50"


def test_bash_rewrite_metacharacter_prefix_rejected(tmp_path):
    """Prefix with '.' metacharacter is skipped; other rules still apply."""
    config_dir = tmp_path / ".ccgate"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"bashRewriteRules": [
            {"prefix": "bad.prefix", "filter": "| head -10"},  # rejected
            {"prefix": "goodcmd", "filter": "| tail -20"},     # applied
        ]}),
        encoding="utf-8",
    )
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b6", "tool_name": "Bash",
                "tool_input": {"command": "goodcmd run"}}
    r = _run(payload, env, cwd=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["updatedInput"]["command"] == "goodcmd run | tail -20"


def test_bash_rewrite_not_applied_to_read(tmp_path):
    """Bash rewrite rule only applies to Bash tool."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b7", "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/file.py", "command": "pytest"}}
    r = _run(payload, env)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_bash_rewrite_leading_whitespace_stripped_for_match(tmp_path):
    """Leading whitespace before command is stripped before prefix matching."""
    env = _base_env(tmp_path, {"CCGATE_BASH_REWRITE_ENABLED": "1"})
    payload = {"session_id": "b8", "tool_name": "Bash",
                "tool_input": {"command": "  pytest tests/"}}
    r = _run(payload, env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    # Original command preserved; filter appended to original (with leading spaces)
    assert out["updatedInput"]["command"] == "  pytest tests/ 2>&1 | tail -100"
```

Run: `pytest tests/test_pre_tool.py -v -k "bash_rewrite"`
Expected: 8 FAILs (stub returns None).

- [ ] **Step 2: Implement `_apply_bash_rewrite` in `pre_tool.py`**

Replace the stub:

```python
_BUILTIN_RULES: list[tuple[str, str]] = [
    ("pytest",      "2>&1 | tail -100"),
    ("cargo test",  "2>&1 | tail -100"),
    ("jest",        "2>&1 | tail -100"),
    ("go test",     "2>&1 | tail -100"),
    ("npm test",    "2>&1 | tail -100"),
    ("mvn test",    "2>&1 | tail -100"),
    ("grep",        "| head -100"),
    ("find",        "| head -100"),
]

_METACHARACTERS = frozenset(".*+?[(")


def _apply_bash_rewrite(
    tool_name: str, tool_input: dict, config: dict
) -> Optional[dict]:
    if not config.get("bashRewriteEnabled", False):
        return None
    if tool_name != "Bash":
        return None
    command: str = tool_input.get("command", "")

    user_rules: list[tuple[str, str]] = []
    for rule in config.get("bashRewriteRules", []):
        prefix = rule.get("prefix", "")
        if any(c in prefix for c in _METACHARACTERS):
            print(
                f"ccgate: bashRewriteRules entry rejected — prefix {prefix!r}"
                " contains regex metacharacters",
                file=sys.stderr,
            )
            continue
        user_rules.append((prefix, rule.get("filter", "")))

    for prefix, filter_suffix in _BUILTIN_RULES + user_rules:
        if command.lstrip().startswith(prefix):
            return {"updatedInput": {**tool_input, "command": f"{command} {filter_suffix}"}}
    return None
```

- [ ] **Step 3: Update `hooks/hooks.json`**

Current content:
```json
{
  "PostToolUse": [...],
  "SessionEnd": [...]
}
```

Add `PreToolUse` entry. Final content:
```json
{
  "PreToolUse": [
    {
      "matcher": "Read|Edit|Write|Bash",
      "hooks": [
        {"type": "command", "command": "python -m ccgate.hooks.pre_tool"}
      ]
    }
  ],
  "PostToolUse": [
    {
      "matcher": "Read|Edit|Write|Glob|Grep|Bash|Agent|mcp__.*",
      "hooks": [{"type": "command", "command": "python -m ccgate.hooks.post_tool"}]
    }
  ],
  "SessionEnd": [
    {
      "hooks": [{"type": "command", "command": "python -m ccgate.hooks.session_end"}]
    }
  ]
}
```

- [ ] **Step 4: Update `.claude-plugin/plugin.json`**

Add PreToolUse entry to the `hooks` array. Final content:
```json
{
  "name": "ccgate",
  "version": "0.1.0",
  "description": "Deterministic context and prompt-cache gate for Claude Code",
  "hooks": [
    {
      "event": "PreToolUse",
      "matcher": "Read|Edit|Write|Bash",
      "command": "python -m ccgate.hooks.pre_tool"
    },
    {
      "event": "PostToolUse",
      "matcher": "Read|Edit|Write|Glob|Grep|Bash|Agent|mcp__.*",
      "command": "python -m ccgate.hooks.post_tool"
    },
    {
      "event": "SessionEnd",
      "command": "python -m ccgate.hooks.session_end"
    }
  ]
}
```

- [ ] **Step 5: Verify all tests pass**

Run: `pytest tests/test_pre_tool.py -v`
Expected: all 25 tests pass (9 contextignore + 8 read-cache + 8 bash rewrite).

Run: `python -m pytest --tb=short -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/hooks/pre_tool.py hooks/hooks.json .claude-plugin/plugin.json \
        tests/test_pre_tool.py
git commit -m "feat: bash rewriting rule + PreToolUse hook registration in hooks.json and plugin.json"
```

---

## Task 5: G7 perf gate for `pre_tool.py`

**Files:**
- Create: `tests/perf_pre_tool.py`

**Interfaces:**
- Consumes: `ccgate.hooks.pre_tool.main()` in-process via stdin/stdout mocking
- All rules disabled (defaults) — measures pass-through overhead, not rule logic

**Spec:** G7 — p95 < 50ms per hook invocation over 500 payloads (standalone script, not pytest-collected).

- [ ] **Step 1: Create `tests/perf_pre_tool.py`**

```python
"""perf_pre_tool.py — G7 gate: pre_tool computation p95 < 50 ms over 500 payloads.

Run as a standalone script: python tests/perf_pre_tool.py
Measures in-process computation time only (excludes Python startup overhead).
All three rules are disabled (defaults) to measure pass-through overhead.
"""
import io
import json
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Must set CCGATE_HOME before importing pre_tool so state.py resolves correctly
_tmpdir = tempfile.mkdtemp(prefix="ccgate_perf_pre_")
os.environ["CCGATE_HOME"] = _tmpdir

import ccgate.hooks.pre_tool as pre_tool  # noqa: E402

N = 500
SEED = 42
TOOLS = ["Read", "Edit", "Bash"]
P95_LIMIT_MS = 50.0


def _run_one(payload: dict) -> float:
    raw = json.dumps(payload)
    out_buf = io.StringIO()
    t0 = time.perf_counter()
    with patch("sys.stdin", io.StringIO(raw)), patch("sys.stdout", out_buf):
        pre_tool.main()
    return (time.perf_counter() - t0) * 1000


def main() -> int:
    rng = random.Random(SEED)
    latencies: list[float] = []
    for _ in range(N):
        tool = rng.choice(TOOLS)
        if tool == "Read":
            tool_input = {"file_path": f"/tmp/file_{rng.randint(0, 50)}.py"}
        elif tool == "Edit":
            tool_input = {"file_path": f"/tmp/edit_{rng.randint(0, 50)}.py",
                          "old_string": "x", "new_string": "y"}
        else:
            tool_input = {"command": rng.choice(["ls -la", "cat file.txt", "echo hello"])}
        payload = {
            "session_id": "perf_pre_session",
            "tool_name": tool,
            "tool_input": tool_input,
        }
        latencies.append(_run_one(payload))

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(0.95 * N)]
    p99 = latencies[int(0.99 * N)]
    p_max = latencies[-1]
    print(
        f"G7 pre_tool latency ({N} payloads) — "
        f"p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms  max={p_max:.1f}ms"
    )
    if p95 < P95_LIMIT_MS:
        print(f"G7 PASS: p95={p95:.1f}ms < {P95_LIMIT_MS}ms")
        return 0
    print(f"G7 FAIL: p95={p95:.1f}ms >= {P95_LIMIT_MS}ms", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the perf gate**

```bash
python tests/perf_pre_tool.py
```

Expected output includes: `G7 PASS: p95=<N>ms < 50.0ms`

If G7 FAILS: the bottleneck is likely `load_config()` doing file I/O on every call (reading `~/.ccgate/config.json`). Investigate — do not optimize prematurely without profiling.

- [ ] **Step 3: Verify pytest count unchanged**

Run: `python -m pytest --tb=short -q`
Expected: same count as after Task 4 (perf_pre_tool.py is NOT pytest-collected — it has no `test_` functions).

- [ ] **Step 4: Commit**

```bash
git add tests/perf_pre_tool.py
git commit -m "feat: perf_pre_tool.py — G7 latency gate for pre_tool.py (p95 < 50ms)"
```

---

## Self-Review Checklist

**Spec coverage scan:**
- §4 contextignore rule → Task 2 ✓ (applicability, pattern loading, matching, deny output, config)
- §5 read-cache rule → Task 3 ✓ (algorithm, staleness, state file, lock, deny output, config)
- §6 bash rewriting → Task 4 ✓ (built-in rules, user rules, metacharacter rejection, config)
- §7 hook registration → Task 4 ✓ (hooks.json + plugin.json)
- §8 G4/G5 gates → Task 1 ✓ (fixture, miss_audit additions, tests)
- §9 G7 gate → Task 5 ✓ (perf_pre_tool.py)
- §10 file map → all files covered across tasks ✓
- §11 global constraints → exit-0 guard in main(), os.replace() in _write_rc(), acquire_lock() for read-cache, stdlib only ✓

**Placeholder scan:** No TBDs. All code is complete. Stubs (_check_read_cache, _apply_bash_rewrite) are intentional placeholders with a comment naming their task.

**Type consistency:**
- `_check_contextignore(tool_name: str, tool_input: dict, config: dict) -> Optional[dict]` — Task 2 defines, Task 2 calls ✓
- `_check_read_cache(session_id: str, tool_name: str, tool_input: dict, config: dict) -> Optional[dict]` — Task 2 stub defines signature, Task 3 implements, Task 2 calls with same args ✓
- `_apply_bash_rewrite(tool_name: str, tool_input: dict, config: dict) -> Optional[dict]` — Task 2 stub defines, Task 4 implements ✓
- `acquire_lock(f"{session_id}-read-cache")` — lock name string matches `_lock_path()` pattern in state.py ✓
- `_write_rc` uses `os.replace(str(tmp), str(path))` — matches global constraint ✓

**Edge case coverage:**
- Exit-0 on empty stdin → `test_hook_exit_0_on_empty_stdin` ✓
- OSError from os.stat (missing file) → `test_read_cache_missing_file_allowed` ✓
- `current_mtime=None` stored and recalled: both None → treated as "changed" (allow) on re-read because `current_mtime is None` condition fires ✓
- Metacharacter prefix rejection → `test_bash_rewrite_metacharacter_prefix_rejected` ✓
- CRLF stripping → `test_contextignore_crlf_stripped` ✓
- Windows backslash normalization → `test_contextignore_backslash_normalized` ✓

**One gap fixed:** The dirty fixture uses `claude-opus-5` as the alternating model. `get_model_spec("claude-opus-5")` may return a default spec with `rate_in=0`. If `miss_cost=0`, `avoidable_usd=0`, so the existing `avoidable_usd > 0` check won't fire on the dirty fixture — but G4 and G5 checks fire based on counts, not USD. The tests assert G4/G5 in stderr (not avoidable_usd), so this is fine.
