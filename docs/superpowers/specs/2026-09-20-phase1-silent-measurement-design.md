# Phase 1 — Silent Measurement Design

**Date:** 2026-09-20
**Status:** approved
**Implements:** BUILD-SPEC §5.4, §6, §8, §9 (G6, G7)

---

## 1. Purpose

Phase 1 introduces measurement without enforcement. Every tool call is recorded; every session is accounted for; net cost is computed and reported. No context is blocked; the only output to Claude's context is a one-line notice when a single tool response is unboundedly large. This baseline proves the measurement pipeline before Phase 2 adds enforcement.

---

## 2. Architecture

```
PostToolUse hook  →  post_tool.py ──┐
                                     ├──  state.py  (acquire_lock → read → write → release)
StatusLine hook   →  statusline.py  │          ↕
(existing)             writes snap  │    ~/.ccgate/
                                     │      sessions/{id}.json
SessionEnd hook   →  session_end.py ┘      sessions/{id}-statusline.json
                          ↕               locks/{id}.lock
                      ledger.py           tools.json
                                          reports/{date}-{id[:8]}.txt
```

All state lives under `~/.ccgate/`. Fully wipeable (I5). No network, no external dependencies.

---

## 3. state.py — Atomic I/O and Locking

### 3.1 Lock strategy (Approach A — advisory lock file)

```python
import os, time, pathlib

def acquire_lock(session_id: str, timeout_ms: int = 200) -> contextmanager:
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
            # Stale lock detection: break any lock older than 30 s
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 30:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"Could not acquire lock for session {session_id}")
            time.sleep(delay)
            delay = min(delay * 2, 0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
```

### 3.2 Atomic write

```python
def write_session(session_id: str, data: dict) -> None:
    path = session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)   # atomic on both POSIX and Windows
```

Same pattern for `write_tools()`.

### 3.3 Public API

```python
def session_path(session_id: str) -> Path
def tools_path() -> Path
def read_session(session_id: str) -> dict          # {} if not found
def write_session(session_id: str, data: dict)     # atomic
def acquire_lock(session_id: str, timeout_ms: int = 200)  # contextmanager
def read_tools() -> dict                           # {} if not found
def write_tools(data: dict)                        # atomic
```

`ccgate_home()` returns `Path(os.environ.get("CCGATE_HOME", Path.home() / ".ccgate"))` — overridable in tests via env var.

---

## 4. Session JSON Schema

```json
{
  "session_id": "abc123",
  "started_at": "2026-09-20T10:00:00Z",
  "model": "claude-sonnet-4-6",
  "tool_calls": [
    {
      "seq": 1,
      "tool": "Read",
      "response_chars": 4096,
      "tokens_est": 1024,
      "notice_bytes": 0,
      "ts": "2026-09-20T10:00:05Z"
    }
  ],
  "tool_profile": {
    "Read": {"count": 5, "mean_tokens": 900, "max_tokens": 2048, "total_tokens": 4500}
  },
  "ledger": {
    "tokens_avoided": 0,
    "tokens_injected": 0,
    "net": 0,
    "net_usd": 0.0
  },
  "notices_emitted": 0,
  "statusline_snapshot": null
}
```

`notice_bytes` on a tool call record is the byte length of the `additionalContext` emitted for that call (0 when no notice was emitted). `ledger.tokens_injected` is derived from summing `notice_bytes // 4` across all tool calls with `notice_bytes > 0`.

---

## 5. post_tool.py — PostToolUse Hook

**Matcher:** `Read|Edit|Write|Glob|Grep|Bash|Agent|mcp__.*`

**Input** (stdin, §4.3):
```json
{"session_id": "...", "tool_name": "Read", "tool_response": "...", "transcript_path": "..."}
```

**Algorithm:**

1. Parse stdin. Extract `session_id`, `tool_name`, `tool_response`.
2. Acquire lock → read session JSON (create with defaults if absent).
3. Compute `tokens_est = len(tool_response) // 4` — Python character count, not byte length (§I4).
4. Append tool call record (`seq`, `tool`, `response_chars`, `tokens_est`, `ts`, `notice_bytes=0`).
5. Update `tool_profile[tool_name]`: increment count, update mean/max/total.
6. Read `sessions/{id}-statusline.json`; store as `statusline_snapshot` if it exists.
7. **AdditionalContext gate** (§I2, D3.unbounded_output):
   - If `tokens_est > config.unboundedOutputTokens` (default 10,000):
     - And `session["notices_emitted"] < config.maxNoticesPerSession` (default 4):
       - Build notice: `f"{tool_name} returned ~{tokens_est:,} tokens — consider a tighter offset/limit."`
       - Increment `notices_emitted`. Set `tool_calls[-1]["notice_bytes"] = len(notice)`.
       - Print `{"hookSpecificOutput": {"additionalContext": notice}}` to stdout.
8. Write session JSON atomically. Release lock.
9. Exit 0.

**Per-tool budget**: when `tool_profile[tool]["count"] >= 3`, the budget for that tool is `mean_tokens`; before that, use constant 500. (Used in Phase 2 for pre-tool cost estimation; stored now.)

---

## 6. statusline.py — Payload Snapshot Write

After `render()` writes its output to stdout, add:

```python
from ccgate.state import ccgate_home
import json, os

def _persist_snapshot(payload: dict) -> None:
    sid = payload.get("session_id")
    if not sid:
        return
    dest = ccgate_home() / "sessions" / f"{sid}-statusline.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, dest)
```

No lock needed — statusline is single-writer per session.

---

## 7. ledger.py — Net Accounting

Pure computation; no I/O.

```python
def compute_net(session_data: dict, session_input_tokens: int = 0) -> dict:
    tokens_avoided = session_data.get("ledger", {}).get("tokens_avoided", 0)  # 0 in Phase 1
    tokens_injected = sum(
        c.get("notice_bytes", 0) // 4
        for c in session_data.get("tool_calls", [])
        if c.get("notice_bytes", 0) > 0
    )
    net = tokens_avoided - tokens_injected
    price = _load_price(session_data.get("model"))  # reads pricing.json; 0.0 if absent
    net_usd = net * price / 1_000_000
    return {
        "tokens_avoided": tokens_avoided,
        "tokens_injected": tokens_injected,
        "net": net,
        "net_usd": net_usd,
    }

def format_headline(session_data: dict) -> str:
    # net >= 0: "net +1,234 tok (avoided 1,500, injected 266)"
    # net <  0: "net -50 tok ↑ self-cost exceeds savings"

def assert_gate(session_data: dict, session_input_tokens: int) -> bool:
    # G6: tokens_injected <= 0.02 * session_input_tokens AND net > 0
    # CLI: python -m ccgate.ledger --assert
```

`pricing.json` lives at `~/.ccgate/pricing.json`. It is hand-maintained and dated; `ccgate` never writes it. Schema: `{"claude-sonnet-4-6": {"input_per_mtok": 3.0, "cache_read_per_mtok": 0.3, ...}}`. Falls back silently to `net_usd = 0.0` if file absent or model not found. `session_data["model"]` is populated by `post_tool.py` from the hook payload's `model` field when present.

---

## 8. session_end.py — Session Finalization

**Input** (stdin): same common fields as PostToolUse — `session_id`, `transcript_path`.

**Algorithm:**

1. Parse stdin. Extract `session_id`, `transcript_path`.
2. Read transcript at `transcript_path`; sum `usage.input_tokens` across all entries → `session_input_tokens`.
3. Acquire lock → read session JSON.
4. Call `ledger.compute_net(session_data, session_input_tokens)` → update `session_data["ledger"]`.
5. Write final session JSON atomically. Release lock.
6. Append one-line summary to `~/.ccgate/reports/{YYYY-MM-DD}-{session_id[:8]}.txt`:
   ```
   {ts} {session_id[:8]} net={net:+d} avoided={tokens_avoided} injected={tokens_injected} tool_calls={len(tool_calls)}
   ```
6. Exit 0.

---

## 9. Hook Registration

**`hooks/hooks.json`** (root-level, Claude Code settings format — merge into `.claude/settings.json`):
```json
{
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

**`.claude-plugin/plugin.json`** — update `"hooks"` array:
```json
{
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

---

## 10. Testing

| File | Scope |
|---|---|
| `tests/test_state.py` | lock acquire/release, stale-lock break, atomic write, concurrent write simulation |
| `tests/test_ledger.py` | `compute_net`, `format_headline`, `assert_gate` (G6 pass/fail cases) |
| `tests/test_post_tool.py` | subprocess fixture payloads → assert session JSON mutations + stdout |
| `tests/test_session_end.py` | subprocess fixture → assert ledger finalized + report line appended |
| `tests/test_statusline_snapshot.py` | `_persist_snapshot` writes correct file; post_tool picks it up |
| `tests/perf_hooks.py` | G7 gate: 500 synthetic fixture payloads (generated in-process with `random` seed 42, varying tool names and response sizes 100–100,000 chars) replayed via subprocess, p95 < 50 ms asserted |

All tests use `CCGATE_HOME` env var pointing to `tmp_path` for full isolation.

---

## 11. Files Created / Modified

| Action | Path |
|---|---|
| CREATE | `src/ccgate/state.py` |
| CREATE | `src/ccgate/ledger.py` |
| CREATE | `src/ccgate/hooks/__init__.py` |
| CREATE | `src/ccgate/hooks/post_tool.py` |
| CREATE | `src/ccgate/hooks/session_end.py` |
| CREATE | `hooks/hooks.json` |
| MODIFY | `.claude-plugin/plugin.json` |
| MODIFY | `src/ccgate/scripts/statusline.py` |
| CREATE | `tests/test_state.py` |
| CREATE | `tests/test_ledger.py` |
| CREATE | `tests/test_post_tool.py` |
| CREATE | `tests/test_session_end.py` |
| CREATE | `tests/test_statusline_snapshot.py` |
| CREATE | `tests/perf_hooks.py` |

---

## 12. Global Constraints (from BUILD-SPEC)

- Python 3.11+, stdlib only — no third-party packages
- `CCGATE_HOME` env var overrides `~/.ccgate` in all state functions
- Token estimates carry `~` label wherever surfaced (I4)
- Hooks emit to context only via `additionalContext`; plain stdout goes to debug log (I2)
- Net accounting: if `net < 0`, headline says so (I3)
- No writes into Claude's context beyond the one-line unbounded-output notice (I6)
- `os.replace()` for all atomic writes — cross-platform (Windows + POSIX)
