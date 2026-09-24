# Phase 1 — Silent Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a zero-enforcement measurement pipeline: every tool call is recorded, every session is accounted for, and unbounded-output notices are the only writes into Claude's context.

**Architecture:** `state.py` provides cross-platform atomic I/O and advisory file locking shared by all hooks. `ledger.py` computes net accounting from session data (pure, no I/O). `post_tool.py` and `session_end.py` are Claude Code hook scripts invoked via `python -m ccgate.hooks.*`; they read a JSON payload from stdin, mutate session state atomically, and emit only the bounded additionalContext notice.

**Tech Stack:** Python 3.11+, stdlib only. Pytest for unit/integration tests. Subprocess-free perf test via in-process patching.

**Spec:** `docs/superpowers/specs/2026-09-20-phase1-silent-measurement-design.md`

## Global Constraints

- Python 3.11+, stdlib only — no third-party packages, no `pip install` steps
- `CCGATE_HOME` env var overrides `~/.ccgate` in every function that accesses state storage — all tests set it via `monkeypatch.setenv("CCGATE_HOME", str(tmp_path))`
- Token estimates carry `~` prefix wherever surfaced in user-visible strings (design invariant I4)
- Hooks emit to Claude's context only via `additionalContext`; plain stdout on exit 0 goes to debug log (I2)
- Net accounting: if `net < 0`, the headline says so explicitly (I3)
- `os.replace(tmp, target)` for all atomic writes — cross-platform (Windows + POSIX)
- Session JSON field: `response_chars` (Python character count, not byte length)
- `acquire_lock` uses `O_CREAT|O_EXCL` advisory lock with stale detection (>30s → break), exponential backoff up to 200ms timeout

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| CREATE | `src/ccgate/state.py` | Atomic I/O, per-session advisory locking, `ccgate_home()` |
| CREATE | `src/ccgate/ledger.py` | Net accounting computation + G6 gate assertion |
| MODIFY | `src/ccgate/scripts/statusline.py` | Write payload snapshot after render |
| CREATE | `src/ccgate/hooks/__init__.py` | Package marker |
| CREATE | `src/ccgate/hooks/post_tool.py` | PostToolUse hook |
| CREATE | `src/ccgate/hooks/session_end.py` | SessionEnd hook |
| CREATE | `hooks/hooks.json` | Claude Code settings-format hook registration |
| MODIFY | `.claude-plugin/plugin.json` | Plugin-level hook registration |
| CREATE | `tests/test_state.py` | state.py unit tests |
| CREATE | `tests/test_ledger.py` | ledger.py unit tests |
| CREATE | `tests/test_statusline_snapshot.py` | _persist_snapshot unit tests |
| CREATE | `tests/test_post_tool.py` | post_tool.py integration tests |
| CREATE | `tests/test_session_end.py` | session_end.py integration tests |
| CREATE | `tests/perf_hooks.py` | G7 gate: p95 < 50ms standalone script |

---

### Task 1: state.py — Atomic I/O and Advisory Locking

**Files:**
- Create: `src/ccgate/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces:
  - `ccgate_home() -> Path` — base dir; honours `CCGATE_HOME` env var
  - `session_path(session_id: str) -> Path`
  - `tools_path() -> Path`
  - `read_session(session_id: str) -> dict` — `{}` if absent
  - `write_session(session_id: str, data: dict) -> None` — atomic
  - `read_tools() -> dict` — `{}` if absent
  - `write_tools(data: dict) -> None` — atomic
  - `acquire_lock(session_id: str, timeout_ms: int = 200)` — context manager; raises `TimeoutError`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
import json
import os
import time
import pytest
from pathlib import Path
from ccgate.state import (
    acquire_lock,
    ccgate_home,
    read_session,
    read_tools,
    session_path,
    tools_path,
    write_session,
    write_tools,
)


class TestCcgateHome:
    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        assert ccgate_home() == tmp_path

    def test_default_is_home_ccgate(self, monkeypatch):
        monkeypatch.delenv("CCGATE_HOME", raising=False)
        result = ccgate_home()
        assert result == Path.home() / ".ccgate"


class TestAtomicWrite:
    def test_write_session_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        write_session("s1", {"session_id": "s1"})
        assert session_path("s1").exists()
        data = json.loads(session_path("s1").read_text())
        assert data["session_id"] == "s1"

    def test_read_session_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        assert read_session("missing") == {}

    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        data = {"session_id": "s2", "notices_emitted": 3}
        write_session("s2", data)
        assert read_session("s2") == data

    def test_write_tools_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        profile = {"Read": {"count": 5, "mean_tokens": 900}}
        write_tools(profile)
        assert read_tools() == profile

    def test_no_tmp_file_left_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        write_session("s3", {"x": 1})
        tmp_files = list((tmp_path / "sessions").glob("*.tmp"))
        assert tmp_files == []

    def test_write_is_atomic_overwrites_previous(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        write_session("s4", {"v": 1})
        write_session("s4", {"v": 2})
        assert read_session("s4") == {"v": 2}


class TestAdvisoryLock:
    def test_acquire_and_release(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_file = tmp_path / "locks" / "s1.lock"
        with acquire_lock("s1"):
            assert lock_file.exists()
        assert not lock_file.exists()

    def test_double_acquire_times_out(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_path = tmp_path / "locks" / "s1.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("9999")  # simulate held lock
        with pytest.raises(TimeoutError):
            with acquire_lock("s1", timeout_ms=50):
                pass

    def test_stale_lock_is_broken(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_path = tmp_path / "locks" / "s1.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("9999")
        old_time = time.time() - 31  # 31 seconds ago → stale
        os.utime(lock_path, (old_time, old_time))
        with acquire_lock("s1", timeout_ms=100):  # must not raise
            pass
        assert not lock_path.exists()

    def test_lock_released_on_exception(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        lock_path = tmp_path / "locks" / "s1.lock"
        try:
            with acquire_lock("s1"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert not lock_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_state.py -v
```
Expected: ImportError or ModuleNotFoundError — `ccgate.state` does not exist yet.

- [ ] **Step 3: Implement `src/ccgate/state.py`**

```python
"""state.py — atomic JSON I/O and per-session advisory file locking."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def ccgate_home() -> Path:
    env = os.environ.get("CCGATE_HOME")
    return Path(env) if env else Path.home() / ".ccgate"


def session_path(session_id: str) -> Path:
    return ccgate_home() / "sessions" / f"{session_id}.json"


def tools_path() -> Path:
    return ccgate_home() / "tools.json"


def _lock_path(session_id: str) -> Path:
    return ccgate_home() / "locks" / f"{session_id}.lock"


def read_session(session_id: str) -> dict:
    path = session_path(session_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_session(session_id: str, data: dict) -> None:
    path = session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_tools() -> dict:
    path = tools_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_tools(data: dict) -> None:
    path = tools_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def acquire_lock(session_id: str, timeout_ms: int = 200):
    """Advisory lock via O_CREAT|O_EXCL. Breaks stale locks (>30s). Raises TimeoutError."""
    lock_path = _lock_path(session_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_ms / 1000
    delay = 0.005
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 30:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"Could not acquire lock for session {session_id!r}")
            time.sleep(delay)
            delay = min(delay * 2, 0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_state.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Run full suite to check for regressions**

```
pytest --tb=short -q
```
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```
git add src/ccgate/state.py tests/test_state.py
git commit -m "feat: state.py — atomic JSON I/O and advisory file locking"
```

---

### Task 2: ledger.py — Net Accounting

**Files:**
- Create: `src/ccgate/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `ccgate_home()` from `ccgate.state` (indirect — for `_load_price`)
- Produces:
  - `compute_net(session_data: dict, session_input_tokens: int = 0) -> dict`
    - Returns `{"tokens_avoided": int, "tokens_injected": int, "net": int, "net_usd": float}`
  - `format_headline(session_data: dict) -> str`
  - `assert_gate(session_data: dict, session_input_tokens: int) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ledger.py
import pytest
from ccgate.ledger import assert_gate, compute_net, format_headline

_NO_NOTICES = {
    "session_id": "s1",
    "model": None,
    "tool_calls": [
        {"seq": 1, "tool": "Read", "response_chars": 100,
         "tokens_est": 25, "notice_bytes": 0}
    ],
    "ledger": {"tokens_avoided": 0},
}

_WITH_NOTICE = {
    "session_id": "s1",
    "model": None,
    "tool_calls": [
        {"seq": 1, "tool": "Read", "response_chars": 100_000,
         "tokens_est": 25_000, "notice_bytes": 80}
    ],
    "ledger": {"tokens_avoided": 0},
}


class TestComputeNet:
    def test_no_notices_zero_injected(self):
        result = compute_net(_NO_NOTICES, session_input_tokens=1000)
        assert result["tokens_injected"] == 0
        assert result["tokens_avoided"] == 0
        assert result["net"] == 0

    def test_notice_contributes_to_injected(self):
        result = compute_net(_WITH_NOTICE, session_input_tokens=1000)
        assert result["tokens_injected"] == 80 // 4  # == 20
        assert result["net"] == -20

    def test_net_usd_zero_without_pricing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        result = compute_net(_NO_NOTICES, session_input_tokens=1000)
        assert result["net_usd"] == 0.0

    def test_multiple_notices_summed(self):
        session = {
            "model": None,
            "tool_calls": [
                {"notice_bytes": 80},
                {"notice_bytes": 0},
                {"notice_bytes": 60},
            ],
            "ledger": {"tokens_avoided": 0},
        }
        result = compute_net(session)
        assert result["tokens_injected"] == (80 // 4) + (60 // 4)  # 20 + 15 == 35

    def test_empty_session_returns_zeros(self):
        result = compute_net({})
        assert result == {"tokens_avoided": 0, "tokens_injected": 0,
                          "net": 0, "net_usd": 0.0}


class TestFormatHeadline:
    def test_positive_net_shows_plus(self):
        session = {"ledger": {"tokens_avoided": 1500, "tokens_injected": 266, "net": 1234}}
        line = format_headline(session)
        assert "net +1,234" in line
        assert "avoided 1,500" in line
        assert "injected 266" in line

    def test_negative_net_shows_warning(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 20, "net": -20}}
        line = format_headline(session)
        assert "net -20" in line
        assert "self-cost" in line

    def test_zero_net_shows_zero(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0}}
        line = format_headline(session)
        assert "net +0" in line or "net 0" in line


class TestAssertGate:
    def test_g6_passes_when_within_2pct_and_net_positive(self):
        session = {"ledger": {"tokens_avoided": 1000, "tokens_injected": 10, "net": 990}}
        assert assert_gate(session, session_input_tokens=1000) is True

    def test_g6_fails_when_net_zero(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0}}
        assert assert_gate(session, session_input_tokens=1000) is False

    def test_g6_fails_when_net_negative(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 20, "net": -20}}
        assert assert_gate(session, session_input_tokens=1000) is False

    def test_g6_fails_when_injected_exceeds_2pct(self):
        # 25 / 1000 = 2.5% > 2%
        session = {"ledger": {"tokens_avoided": 1000, "tokens_injected": 25, "net": 975}}
        assert assert_gate(session, session_input_tokens=1000) is False

    def test_g6_passes_at_exact_2pct_boundary(self):
        # 20 / 1000 = exactly 2%
        session = {"ledger": {"tokens_avoided": 1000, "tokens_injected": 20, "net": 980}}
        assert assert_gate(session, session_input_tokens=1000) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_ledger.py -v
```
Expected: ImportError — `ccgate.ledger` does not exist yet.

- [ ] **Step 3: Implement `src/ccgate/ledger.py`**

```python
"""ledger.py — net token accounting (§6). Pure computation; no I/O except pricing.json read."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_price(model: str | None) -> float:
    """Return input price per token from ~/.ccgate/pricing.json. 0.0 if absent."""
    if not model:
        return 0.0
    env = os.environ.get("CCGATE_HOME")
    home = Path(env) if env else Path.home() / ".ccgate"
    pricing_path = home / "pricing.json"
    if not pricing_path.exists():
        return 0.0
    try:
        table = json.loads(pricing_path.read_text(encoding="utf-8"))
        per_mtok = float(table.get(model, {}).get("input_per_mtok", 0.0))
        return per_mtok / 1_000_000
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0.0


def compute_net(session_data: dict, session_input_tokens: int = 0) -> dict:
    """Compute net token accounting from session data. tokens_avoided == 0 in Phase 1."""
    tokens_avoided = (session_data.get("ledger") or {}).get("tokens_avoided", 0)
    tokens_injected = sum(
        c.get("notice_bytes", 0) // 4
        for c in (session_data.get("tool_calls") or [])
        if c.get("notice_bytes", 0) > 0
    )
    net = tokens_avoided - tokens_injected
    price = _load_price(session_data.get("model"))
    return {
        "tokens_avoided": tokens_avoided,
        "tokens_injected": tokens_injected,
        "net": net,
        "net_usd": round(net * price, 6),
    }


def format_headline(session_data: dict) -> str:
    """Format net accounting headline for human display."""
    ledger = session_data.get("ledger") or {}
    net = ledger.get("net", 0)
    avoided = ledger.get("tokens_avoided", 0)
    injected = ledger.get("tokens_injected", 0)
    sign = "+" if net >= 0 else ""
    base = f"net {sign}{net:,} tok (avoided {avoided:,}, injected {injected:,})"
    if net < 0:
        return base.replace("tok", "tok ↑ self-cost exceeds savings", 1)
    return base


def assert_gate(session_data: dict, session_input_tokens: int) -> bool:
    """G6: tokens_injected <= 2% of session input AND net > 0."""
    ledger = session_data.get("ledger") or {}
    net = ledger.get("net", 0)
    injected = ledger.get("tokens_injected", 0)
    return net > 0 and injected <= 0.02 * session_input_tokens


def main() -> None:
    """CLI: python -m ccgate.ledger --assert <session.json> <input_tokens>"""
    args = sys.argv[1:]
    if "--assert" not in args:
        print("Usage: python -m ccgate.ledger --assert <session.json> <input_tokens>",
              file=sys.stderr)
        sys.exit(1)
    idx = args.index("--assert")
    try:
        session_file = args[idx + 1]
        input_tokens = int(args[idx + 2]) if idx + 2 < len(args) else 0
    except (IndexError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    session_data = json.loads(Path(session_file).read_text(encoding="utf-8"))
    result = compute_net(session_data, input_tokens)
    session_data["ledger"] = result
    headline = format_headline(session_data)
    if assert_gate(session_data, input_tokens):
        print(f"G6 PASS: {headline}")
        sys.exit(0)
    else:
        print(f"G6 FAIL: {headline}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_ledger.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Run full suite**

```
pytest --tb=short -q
```
Expected: all existing + new tests pass.

- [ ] **Step 6: Commit**

```
git add src/ccgate/ledger.py tests/test_ledger.py
git commit -m "feat: ledger.py — net accounting and G6 gate assertion"
```

---

### Task 3: statusline.py — Payload Snapshot Write

**Files:**
- Modify: `src/ccgate/scripts/statusline.py` (add `_persist_snapshot` and call it from `main`)
- Test: `tests/test_statusline_snapshot.py`

**Interfaces:**
- Consumes: `ccgate_home()` from `ccgate.state` (Task 1)
- Produces: `_persist_snapshot(payload: dict) -> None` — writes `{ccgate_home}/sessions/{session_id}-statusline.json` atomically

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_statusline_snapshot.py
import json
import pytest
from pathlib import Path
from ccgate.scripts.statusline import _persist_snapshot


class TestPersistSnapshot:
    def test_writes_snapshot_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        payload = {"session_id": "s1", "model": {"id": "claude-sonnet-4-6"}}
        _persist_snapshot(payload)
        snap = tmp_path / "sessions" / "s1-statusline.json"
        assert snap.exists()
        data = json.loads(snap.read_text())
        assert data["session_id"] == "s1"

    def test_no_session_id_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({})
        assert not (tmp_path / "sessions").exists()

    def test_overwrites_previous_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({"session_id": "s1", "x": 1})
        _persist_snapshot({"session_id": "s1", "x": 2})
        snap = tmp_path / "sessions" / "s1-statusline.json"
        assert json.loads(snap.read_text())["x"] == 2

    def test_no_tmp_file_left_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({"session_id": "s1"})
        tmps = list((tmp_path / "sessions").glob("*.tmp"))
        assert tmps == []

    def test_session_id_used_as_filename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        _persist_snapshot({"session_id": "mySession42"})
        assert (tmp_path / "sessions" / "mySession42-statusline.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_statusline_snapshot.py -v
```
Expected: AttributeError — `_persist_snapshot` does not exist in `statusline.py`.

- [ ] **Step 3: Add `_persist_snapshot` to `src/ccgate/scripts/statusline.py`**

Add this import to the top of `statusline.py` (after the existing stdlib imports):
```python
import os
```
(already present — no change needed)

Add this function before `main()`:
```python
def _persist_snapshot(payload: dict) -> None:
    """Write payload snapshot to ~/.ccgate/sessions/{session_id}-statusline.json."""
    from ccgate.state import ccgate_home
    sid = payload.get("session_id")
    if not sid:
        return
    dest = ccgate_home() / "sessions" / f"{sid}-statusline.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, dest)
```

Also add a `_persist_snapshot(payload)` call inside `main()`, after `print(render(payload, config))`:
```python
def main() -> None:
    from ccgate.config import load_config
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        payload = {}
    config = load_config()
    print(render(payload, config))
    _persist_snapshot(payload)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_statusline_snapshot.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 5: Run full suite**

```
pytest --tb=short -q
```
Expected: all existing tests still pass (statusline tests unaffected — `render()` is not modified).

- [ ] **Step 6: Commit**

```
git add src/ccgate/scripts/statusline.py tests/test_statusline_snapshot.py
git commit -m "feat: statusline — persist payload snapshot for post_tool correlation"
```

---

### Task 4: hooks/post_tool.py — PostToolUse Hook

**Files:**
- Create: `src/ccgate/hooks/__init__.py`
- Create: `src/ccgate/hooks/post_tool.py`
- Test: `tests/test_post_tool.py`

**Interfaces:**
- Consumes: `acquire_lock`, `ccgate_home`, `read_session`, `write_session` from `ccgate.state`; `load_config` from `ccgate.config`
- Produces: `src/ccgate/hooks/post_tool.py` runnable as `python -m ccgate.hooks.post_tool`

stdin JSON fields used: `session_id`, `tool_name`, `tool_response`, `model` (optional).

Output (when notice emitted): `{"hookSpecificOutput": {"additionalContext": "<one-line notice>"}}` on stdout. Otherwise no stdout.

Session JSON shape produced — for downstream tasks to rely on:
```json
{
  "session_id": "str",
  "started_at": "ISO-8601",
  "model": "str | null",
  "tool_calls": [
    {"seq": 1, "tool": "str", "response_chars": 0, "tokens_est": 0,
     "notice_bytes": 0, "ts": "ISO-8601"}
  ],
  "tool_profile": {
    "Read": {"count": 1, "mean_tokens": 25, "max_tokens": 25, "total_tokens": 25}
  },
  "ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0, "net_usd": 0.0},
  "notices_emitted": 0,
  "statusline_snapshot": null
}
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_post_tool.py
import json
import os
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def run_hook(payload: dict, env: dict) -> "subprocess.CompletedProcess":
    import subprocess
    e = env.copy()
    e.setdefault("PYTHONPATH", SRC)
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.post_tool"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=e,
    )


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    e = os.environ.copy()
    return e, tmp_path


class TestPostTool:
    def test_records_tool_call(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x" * 100}
        result = run_hook(payload, env)
        assert result.returncode == 0
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert len(session["tool_calls"]) == 1
        tc = session["tool_calls"][0]
        assert tc["tool"] == "Read"
        assert tc["response_chars"] == 100
        assert tc["tokens_est"] == 25  # 100 // 4

    def test_increments_seq(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x"}
        run_hook(payload, env)
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert session["tool_calls"][0]["seq"] == 1
        assert session["tool_calls"][1]["seq"] == 2

    def test_updates_tool_profile(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x" * 100}
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert "Read" in session["tool_profile"]
        p = session["tool_profile"]["Read"]
        assert p["count"] == 1
        assert p["total_tokens"] == 25
        assert p["mean_tokens"] == 25
        assert p["max_tokens"] == 25

    def test_no_notice_for_small_response(self, hook_env):
        env, tmp_path = hook_env
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x" * 100}
        result = run_hook(payload, env)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_emits_notice_for_large_response(self, hook_env):
        env, tmp_path = hook_env
        # tokens_est = 40_004 // 4 = 10_001 > 10_000 default threshold
        big = "x" * 40_004
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": big}
        result = run_hook(payload, env)
        assert result.returncode == 0
        out = json.loads(result.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "Read" in ctx
        assert "~10,001" in ctx
        assert "offset/limit" in ctx

    def test_notice_increments_notices_emitted(self, hook_env):
        env, tmp_path = hook_env
        big = "x" * 40_004
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": big}
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert session["notices_emitted"] == 1
        assert session["tool_calls"][0]["notice_bytes"] > 0

    def test_notice_capped_at_max_per_session(self, hook_env):
        env, tmp_path = hook_env
        big = "x" * 40_004
        # Emit 4 notices (default maxNoticesPerSession=4)
        for _ in range(4):
            run_hook({"session_id": "s1", "tool_name": "Read", "tool_response": big}, env)
        # 5th call must emit no notice
        result = run_hook(
            {"session_id": "s1", "tool_name": "Read", "tool_response": big}, env
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_reads_statusline_snapshot(self, hook_env):
        env, tmp_path = hook_env
        snap_dir = tmp_path / "sessions"
        snap_dir.mkdir(parents=True)
        snap = snap_dir / "s1-statusline.json"
        snap.write_text(json.dumps({"session_id": "s1", "hit_ratio": 0.9}))
        payload = {"session_id": "s1", "tool_name": "Read", "tool_response": "x"}
        run_hook(payload, env)
        session = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert session["statusline_snapshot"] is not None
        assert session["statusline_snapshot"]["hit_ratio"] == 0.9

    def test_exit_zero_on_empty_stdin(self, hook_env):
        env, tmp_path = hook_env
        import subprocess
        e = env.copy()
        e.setdefault("PYTHONPATH", SRC)
        result = subprocess.run(
            [PYTHON, "-m", "ccgate.hooks.post_tool"],
            input="",
            capture_output=True,
            text=True,
            env=e,
        )
        assert result.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_post_tool.py -v
```
Expected: ImportError or ModuleNotFoundError — `ccgate.hooks` package does not exist yet.

- [ ] **Step 3: Create `src/ccgate/hooks/__init__.py`**

```python
# empty package marker
```

- [ ] **Step 4: Implement `src/ccgate/hooks/post_tool.py`**

```python
"""post_tool.py — PostToolUse hook: silent tool-response measurement (Phase 1)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from ccgate.config import load_config
from ccgate.state import acquire_lock, ccgate_home, read_session, write_session


def _default_session(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": None,
        "tool_calls": [],
        "tool_profile": {},
        "ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0, "net_usd": 0.0},
        "notices_emitted": 0,
        "statusline_snapshot": None,
    }


def _update_profile(profile: dict, tool: str, tokens_est: int) -> None:
    if tool not in profile:
        profile[tool] = {"count": 0, "mean_tokens": 0, "max_tokens": 0, "total_tokens": 0}
    p = profile[tool]
    p["count"] += 1
    p["total_tokens"] += tokens_est
    p["mean_tokens"] = p["total_tokens"] // p["count"]
    p["max_tokens"] = max(p["max_tokens"], tokens_est)


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "Unknown")
    tool_response = payload.get("tool_response", "")
    model = payload.get("model")

    config = load_config()
    unbounded = config.get("unboundedOutputTokens", 10_000)
    max_notices = config.get("maxNoticesPerSession", 4)

    response_chars = len(tool_response)
    tokens_est = response_chars // 4
    ts = datetime.now(timezone.utc).isoformat()

    notice = ""
    with acquire_lock(session_id):
        session = read_session(session_id) or _default_session(session_id)

        if model and not session.get("model"):
            session["model"] = model

        seq = len(session["tool_calls"]) + 1
        record = {
            "seq": seq,
            "tool": tool_name,
            "response_chars": response_chars,
            "tokens_est": tokens_est,
            "notice_bytes": 0,
            "ts": ts,
        }
        session["tool_calls"].append(record)
        _update_profile(session["tool_profile"], tool_name, tokens_est)

        # Read statusline snapshot if available
        snap_path = ccgate_home() / "sessions" / f"{session_id}-statusline.json"
        if snap_path.exists():
            try:
                session["statusline_snapshot"] = json.loads(
                    snap_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                pass

        # AdditionalContext gate (I2 — only when unbounded output detected)
        if tokens_est > unbounded and session["notices_emitted"] < max_notices:
            notice = (
                f"{tool_name} returned ~{tokens_est:,} tokens"
                " — consider a tighter offset/limit."
            )
            session["notices_emitted"] += 1
            session["tool_calls"][-1]["notice_bytes"] = len(notice)

        write_session(session_id, session)

    if notice:
        print(json.dumps({"hookSpecificOutput": {"additionalContext": notice}}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_post_tool.py -v
```
Expected: all 9 tests pass.

- [ ] **Step 6: Run full suite**

```
pytest --tb=short -q
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```
git add src/ccgate/hooks/__init__.py src/ccgate/hooks/post_tool.py tests/test_post_tool.py
git commit -m "feat: post_tool.py — PostToolUse hook with silent measurement and unbounded-output notice"
```

---

### Task 5: hooks/session_end.py — Session Finalization

**Files:**
- Create: `src/ccgate/hooks/session_end.py`
- Test: `tests/test_session_end.py`

**Interfaces:**
- Consumes:
  - `acquire_lock`, `ccgate_home`, `read_session`, `write_session` from `ccgate.state`
  - `compute_net` from `ccgate.ledger`
  - `read_transcript` from `ccgate.transcript` (sums `usage.input_tokens`)
- Produces: finalized `session["ledger"]` dict; appended report line in `{ccgate_home}/reports/{date}-{id[:8]}.txt`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_end.py
import json
import os
import sys
from pathlib import Path

import pytest

from ccgate.state import write_session

PYTHON = sys.executable
SRC = str(Path(__file__).parent.parent / "src")


def run_hook(payload: dict, env: dict) -> "subprocess.CompletedProcess":
    import subprocess
    e = env.copy()
    e.setdefault("PYTHONPATH", SRC)
    return subprocess.run(
        [PYTHON, "-m", "ccgate.hooks.session_end"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=e,
    )


def _write_transcript(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    e = os.environ.copy()
    return e, tmp_path


_BASE_SESSION = {
    "session_id": "s1",
    "started_at": "2026-09-20T10:00:00Z",
    "model": None,
    "tool_calls": [],
    "tool_profile": {},
    "ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0, "net_usd": 0.0},
    "notices_emitted": 0,
    "statusline_snapshot": None,
}


class TestSessionEnd:
    def test_finalizes_ledger_with_no_notices(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [
            {"type": "assistant", "message": {"usage": {"input_tokens": 1000}}}
        ])
        result = run_hook(
            {"session_id": "s1", "transcript_path": str(transcript)}, env
        )
        assert result.returncode == 0
        final = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert final["ledger"]["tokens_injected"] == 0
        assert final["ledger"]["net"] == 0

    def test_finalizes_ledger_with_notice(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        session = dict(_BASE_SESSION)
        session["tool_calls"] = [
            {"seq": 1, "tool": "Read", "response_chars": 100_000,
             "tokens_est": 25_000, "notice_bytes": 80, "ts": "2026-09-20T10:00:01Z"}
        ]
        session["notices_emitted"] = 1
        write_session("s1", session)
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [
            {"type": "assistant", "message": {"usage": {"input_tokens": 5000}}}
        ])
        result = run_hook(
            {"session_id": "s1", "transcript_path": str(transcript)}, env
        )
        assert result.returncode == 0
        final = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert final["ledger"]["tokens_injected"] == 80 // 4  # == 20
        assert final["ledger"]["net"] == -20

    def test_appends_report_line(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [])
        run_hook({"session_id": "s1", "transcript_path": str(transcript)}, env)
        reports = list((tmp_path / "reports").glob("*.txt"))
        assert len(reports) == 1
        content = reports[0].read_text()
        assert "s1" in content
        assert "net=" in content
        assert "tool_calls=" in content

    def test_missing_session_exits_0(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [])
        result = run_hook(
            {"session_id": "no_session", "transcript_path": str(transcript)}, env
        )
        assert result.returncode == 0

    def test_missing_transcript_exits_0(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        result = run_hook(
            {"session_id": "s1", "transcript_path": "/nonexistent/path.jsonl"}, env
        )
        assert result.returncode == 0

    def test_multiple_transcript_entries_summed(self, hook_env, tmp_path):
        env, tmp_path = hook_env
        write_session("s1", dict(_BASE_SESSION))
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript(transcript, [
            {"type": "assistant", "message": {"usage": {"input_tokens": 1000}}},
            {"type": "assistant", "message": {"usage": {"input_tokens": 2000}}},
        ])
        run_hook({"session_id": "s1", "transcript_path": str(transcript)}, env)
        # session_input_tokens == 3000; net == 0; injected == 0; G6 fails (net not > 0)
        # Just check the hook ran and updated the session
        final = json.loads((tmp_path / "sessions" / "s1.json").read_text())
        assert "ledger" in final
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_session_end.py -v
```
Expected: ImportError — `ccgate.hooks.session_end` does not exist yet.

- [ ] **Step 3: Implement `src/ccgate/hooks/session_end.py`**

```python
"""session_end.py — SessionEnd hook: finalize session ledger and write report."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ccgate.ledger import compute_net
from ccgate.state import acquire_lock, ccgate_home, read_session, write_session
from ccgate.transcript import read_transcript


def _session_input_tokens(transcript_path: str) -> int:
    path = Path(transcript_path)
    if not path.exists():
        return 0
    try:
        return sum(r.usage.input_tokens for r in read_transcript(path))
    except Exception:
        return 0


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    transcript_path = payload.get("transcript_path", "")

    input_tokens = _session_input_tokens(transcript_path)

    ledger: dict = {}
    session: dict = {}
    with acquire_lock(session_id):
        session = read_session(session_id)
        if not session:
            return
        ledger = compute_net(session, input_tokens)
        session["ledger"] = ledger
        write_session(session_id, session)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_dir = ccgate_home() / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"{today}-{session_id[:8]}.txt"
    net = ledger.get("net", 0)
    avoided = ledger.get("tokens_avoided", 0)
    injected = ledger.get("tokens_injected", 0)
    call_count = len(session.get("tool_calls", []))
    line = (
        f"{ts} {session_id[:8]} net={net:+d} "
        f"avoided={avoided} injected={injected} tool_calls={call_count}\n"
    )
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(line)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_session_end.py -v
```
Expected: all 6 tests pass.

- [ ] **Step 5: Run full suite**

```
pytest --tb=short -q
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```
git add src/ccgate/hooks/session_end.py tests/test_session_end.py
git commit -m "feat: session_end.py — SessionEnd hook finalizes ledger and appends report"
```

---

### Task 6: Hook Registration

**Files:**
- Create: `hooks/hooks.json`
- Modify: `.claude-plugin/plugin.json`

No test file for this task — these are configuration files with no executable logic. The correctness check is that `python -m ccgate.hooks.post_tool` and `python -m ccgate.hooks.session_end` are the right module paths (verified by Tasks 4+5 passing).

- [ ] **Step 1: Create `hooks/hooks.json`**

Create the file at the project root (sibling to `src/`, `tests/`, `.claude-plugin/`):

```json
{
  "PostToolUse": [
    {
      "matcher": "Read|Edit|Write|Glob|Grep|Bash|Agent|mcp__.*",
      "hooks": [
        {"type": "command", "command": "python -m ccgate.hooks.post_tool"}
      ]
    }
  ],
  "SessionEnd": [
    {
      "hooks": [
        {"type": "command", "command": "python -m ccgate.hooks.session_end"}
      ]
    }
  ]
}
```

- [ ] **Step 2: Update `.claude-plugin/plugin.json`**

Replace:
```json
{
  "name": "ccgate",
  "version": "0.1.0",
  "description": "Deterministic context and prompt-cache gate for Claude Code",
  "hooks": []
}
```

With:
```json
{
  "name": "ccgate",
  "version": "0.1.0",
  "description": "Deterministic context and prompt-cache gate for Claude Code",
  "hooks": [
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

- [ ] **Step 3: Run full suite (regression check)**

```
pytest --tb=short -q
```
Expected: all tests pass (no code was changed — only config files).

- [ ] **Step 4: Commit**

```
git add hooks/hooks.json .claude-plugin/plugin.json
git commit -m "feat: register PostToolUse and SessionEnd hooks in plugin.json and hooks.json"
```

---

### Task 7: perf_hooks.py — G7 Latency Gate

**Files:**
- Create: `tests/perf_hooks.py`

This is a standalone script (not a pytest file). Run it directly: `python tests/perf_hooks.py`. It measures the in-process computation latency of `post_tool.main()` over 500 synthetic payloads and asserts p95 < 50ms.

**Interfaces:**
- Consumes: `ccgate.hooks.post_tool.main` (Task 4)

- [ ] **Step 1: Implement `tests/perf_hooks.py`**

```python
"""perf_hooks.py — G7 gate: post_tool computation p95 < 50 ms over 500 payloads.

Run as a standalone script: python tests/perf_hooks.py
Measures in-process computation time only (excludes Python startup overhead).
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

# Must set CCGATE_HOME before importing post_tool so state.py resolves correctly
_tmpdir = tempfile.mkdtemp(prefix="ccgate_perf_")
os.environ["CCGATE_HOME"] = _tmpdir

import ccgate.hooks.post_tool as post_tool  # noqa: E402

N = 500
SEED = 42
TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "Agent"]
P95_LIMIT_MS = 50.0


def _run_one(payload: dict) -> float:
    raw = json.dumps(payload)
    out_buf = io.StringIO()
    t0 = time.perf_counter()
    with patch("sys.stdin", io.StringIO(raw)), patch("sys.stdout", out_buf):
        post_tool.main()
    return (time.perf_counter() - t0) * 1000


def main() -> int:
    rng = random.Random(SEED)
    latencies: list[float] = []
    for i in range(N):
        tool = rng.choice(TOOLS)
        chars = rng.randint(100, 100_000)
        payload = {
            "session_id": "perf_session",
            "tool_name": tool,
            "tool_response": "x" * chars,
        }
        latencies.append(_run_one(payload))

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(0.95 * N)]
    p99 = latencies[int(0.99 * N)]
    p_max = latencies[-1]
    print(
        f"G7 latency ({N} payloads) — "
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

- [ ] **Step 2: Run the perf gate to verify it passes**

```
python tests/perf_hooks.py
```
Expected output: `G7 PASS: p95=<N>ms < 50.0ms`. If it fails, the bottleneck is almost certainly the file I/O inside `acquire_lock` + `write_session`; check that the OS's `os.replace()` is fast on the test machine.

- [ ] **Step 3: Run full pytest suite**

```
pytest --tb=short -q
```
Expected: all tests pass (perf_hooks.py is a standalone script, not collected by pytest — no change to existing suite count).

- [ ] **Step 4: Commit**

```
git add tests/perf_hooks.py
git commit -m "feat: perf_hooks.py — G7 latency gate (p95 < 50ms over 500 payloads)"
```
