# Phase 2 — Enforcement Design

**Date:** 2026-09-20
**Status:** approved
**Implements:** BUILD-SPEC §5.5, §9 (G4, G5, G7), §10

---

## 1. Purpose

Phase 2 introduces enforcement: a `PreToolUse` hook (`pre_tool.py`) that intercepts tool calls before they execute and either blocks them, rewrites their input, or passes through. Three independently-toggleable rules run in order:

1. `.contextignore` glob deny — blocks reads of paths matching project or global ignore patterns
2. Read-cache deny — blocks re-reads of unchanged file ranges within the same session
3. Bash output rewriting — appends output-capping filters to noisy test/search commands

All rules are off by default until their own A/B over recorded sessions shows positive net. Gates G4 and G5 (added to `miss_audit.py --assert`) verify quality on synthesized fixture sessions.

---

## 2. Architecture

```
PreToolUse hook  →  pre_tool.py
                        │
                        ├── Rule 1: _check_contextignore()
                        │         reads .contextignore (cwd + ~/.claude/)
                        │
                        ├── Rule 2: _check_read_cache()
                        │         reads/writes {session_id}-read-cache.json
                        │         reads {session_id}-statusline.json (window size, read-only)
                        │
                        └── Rule 3: _apply_bash_rewrite()
                                  stateless; reads config only

Output: one of
  {"permissionDecision": "deny", "permissionDecisionReason": "..."}
  {"updatedInput": {...}}
  (nothing — allow)
```

All state lives under `~/.ccgate/`. No network. No third-party packages. Exit 0 always.

---

## 3. Read-cache State

### 3.1 File and lock

- State: `~/.ccgate/sessions/{session_id}-read-cache.json`
- Lock: `~/.ccgate/locks/{session_id}-read-cache.lock`

This is **separate** from the Phase 1 session JSON (`{session_id}.json`) — no schema coupling, no lock contention between pre_tool and post_tool.

### 3.2 Schema

```json
{
  "reads": {
    "/path/to/file:None:None": {
      "mtime": 1726800000.0,
      "ts": "2026-09-20T10:00:00Z",
      "idx": 0
    },
    "/path/to/file:100:50": {
      "mtime": 1726800001.0,
      "ts": "2026-09-20T10:00:05Z",
      "idx": 2
    }
  },
  "file_log": [
    "/path/to/file",
    "/other/file",
    "/path/to/file"
  ],
  "seq": 3
}
```

- `reads`: dict keyed by `"{file_path}:{offset}:{limit}"` (O(1) lookup). `idx` is the index in `file_log` where this read's path was appended (`idx = len(file_log)` before appending), so `file_log[idx+1:]` gives all reads that occurred after this one.
- `file_log`: ordered list of file paths (one entry per Read hook call). Used for `staleFiles` staleness count.
- `seq`: total Read calls so far (equals `len(file_log)`).

---

## 4. `.contextignore` Rule

### 4.1 Applicability

Applies to **Read, Edit, Write** — tools where `tool_input["file_path"]` is the path being accessed. Grep/Glob take patterns, not resolved paths, and are excluded.

### 4.2 Pattern loading

On every hook invocation (no cross-invocation cache — each invocation is a fresh process):

1. `{cwd}/.contextignore` (project root)
2. `~/.claude/.contextignore` (global)

Patterns from both are merged into one list. Lines starting with `#` and blank lines are skipped. CRLF stripped from each pattern. If a file is absent, it is silently skipped.

### 4.3 Matching

Via `fnmatch` (stdlib). For each pattern, two tests:
- Full path: `fnmatch(file_path.replace("\\", "/"), pattern)`
- Filename only: `fnmatch(Path(file_path).name, pattern)`

Backslash → forward slash normalization is applied to `file_path` before matching (Windows correctness).

### 4.4 Output on match

```json
{
  "permissionDecision": "deny",
  "permissionDecisionReason": "blocked by .contextignore pattern '*.json' — use Grep for targeted search. To override: remove the pattern or set contextignoreEnabled: false in .claude/settings.json."
}
```

Reason names the matched pattern and states the override mechanism.

### 4.5 Config

| Key | Default | Effect |
|---|---|---|
| `contextignoreEnabled` | `false` | Set `true` to activate rule |

When disabled: function returns `None` immediately, hook passes through.

---

## 5. Read-cache Rule

### 5.1 Applicability

Applies to **Read** tool only.

### 5.2 Algorithm

Inside `acquire_lock({session_id}-read-cache.lock)`:

1. Load `{session_id}-read-cache.json` (empty cache if absent)
2. Build key: `f"{file_path}:{offset}:{limit}"` — `offset` and `limit` from `tool_input`, defaulting to `None`
3. **Key absent** → record read, return `None` (allow)
4. **Key present, mtime changed** (`os.stat(file_path).st_mtime != entry.mtime`) → update entry, return `None` (allow)
5. **Key present, mtime unchanged** → check staleness:
   - **Stale** (any condition): remove entry, record fresh read, return `None` (allow)
   - **Not stale**: return deny

On allow: append `file_path` to `file_log`, increment `seq`, write cache atomically with `os.replace()` before releasing lock.

On `os.stat` `OSError` (file deleted or permission denied): treat as mtime changed → allow.

### 5.3 Staleness conditions (re-allow if ANY met)

| Condition | Check |
|---|---|
| `staleTimeMs` elapsed | `(time.time() - parse_ts(entry.ts)) * 1000 > staleTimeMs` |
| `staleFiles` distinct files since | `len(set(file_log[entry.idx + 1:])) > effective_staleFiles` |

`staleTokenRatio` is deferred to a later phase (requires coupling to Phase 1 token data).

**Staleness scaling** — `staleFiles` default of 8 assumes a 200K context window. Actual threshold:
```python
effective_staleFiles = round(config.staleFiles * actual_window / 200_000)
```
`actual_window` read from `{session_id}-statusline.json` (written by Phase 1's statusline hook); falls back to 200,000 if absent or unparseable.

### 5.4 Output on deny

```json
{
  "permissionDecision": "deny",
  "permissionDecisionReason": "read cache: '/path/to/file' (lines None–None, unchanged) was already read this session — use the earlier result. To override: set readCacheEnabled: false."
}
```

### 5.5 Config

| Key | Default | Effect |
|---|---|---|
| `readCacheEnabled` | `false` | Set `true` to activate rule |
| `staleTimeMs` | `600000` | Re-allow after idle (ms) |
| `staleFiles` | `8` | Re-allow after N distinct files loaded (scaled to window) |

---

## 6. Bash Rewriting Rule

### 6.1 Applicability

Applies to **Bash** tool only. Stateless — no file I/O.

### 6.2 Built-in default rules

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

### 6.3 Config extension

User-defined `bashRewriteRules` (list of `{"prefix": str, "filter": str}`) in `.claude/settings.json` are appended after built-ins. First match wins.

**Validation at load time**: any rule whose `prefix` contains a regex metacharacter (`. * + ? [ (`) is rejected with a logged error and skipped; remaining rules still apply. The hook never exits non-zero due to validation failure.

### 6.4 Matching

```python
command.lstrip().startswith(prefix)  # literal, case-sensitive
```

### 6.5 Output on match

```json
{
  "updatedInput": {"command": "pytest tests/ 2>&1 | tail -100"}
}
```

Filter appended: `f"{command} {filter}"`.

### 6.6 Config

| Key | Default | Effect |
|---|---|---|
| `bashRewriteEnabled` | `false` | Set `true` to activate rule |
| `bashRewriteRules` | `[]` | Additional prefix → filter rules (merged after built-ins) |

---

## 7. Hook Registration

**`hooks/hooks.json`** — add PreToolUse:
```json
{
  "PreToolUse": [
    {
      "matcher": "Read|Edit|Write|Bash",
      "hooks": [{"type": "command", "command": "python -m ccgate.hooks.pre_tool"}]
    }
  ]
}
```

**`.claude-plugin/plugin.json`** — add PreToolUse entry:
```json
{
  "event": "PreToolUse",
  "matcher": "Read|Edit|Write|Bash",
  "command": "python -m ccgate.hooks.pre_tool"
}
```

---

## 8. G4/G5 Gates

### 8.1 Additions to `miss_audit.py --assert`

`--assert` mode gains two new checks (alongside existing G8/G9/G10/G11):

- **G4**: `hit_ratio ≥ 0.85` where `hit_ratio = cache_hit_count / total_request_count` (requests with `cache_read_input_tokens > 0`). Only asserted when session has ≥ 10 requests.
- **G5**: Zero entries with `miss_cause` in `{D1.model_switch, D1.tools_changed}`.

Exit 0 if all gates pass; exit 1 and print which gates failed.

### 8.2 Fixture session

**`tests/fixtures/g4g5_clean.jsonl`** — synthesized, 15 assistant turns:
- Consistent model throughout
- No effort/thinking changes between turns
- 13+ turns have `cache_read_input_tokens > 0` (hit_ratio ≥ 0.87)
- Zero turns trigger `D1.model_switch` or `D1.tools_changed`

Verified by: `python -m ccgate.scripts.miss_audit --assert tests/fixtures/g4g5_clean.jsonl`

---

## 9. Testing Plan

| File | Scope |
|---|---|
| `tests/test_pre_tool.py` | Subprocess-based integration tests for all three rules |
| `tests/test_miss_audit_g4g5.py` | G4/G5 assertion tests against g4g5_clean.jsonl and adversarial fixtures |
| `tests/perf_pre_tool.py` | G7 gate for pre_tool.py: 500 payloads (Read/Edit/Bash mix), p95 < 50ms |

**`tests/test_pre_tool.py`** covers:

*Contextignore:*
- Matching pattern denies Read; non-match allows; CRLF stripped from pattern; `\` normalized in path; project-root and global file locations; disabled rule passes through

*Read cache:*
- First read allowed and recorded; identical (path, offset, limit) + unchanged mtime → denied; changed mtime → allowed; different offset/limit on same file → allowed; `staleTimeMs` eviction allows; `staleFiles` eviction allows; disabled rule passes through; exit 0 always (even on lock timeout simulation)

*Bash rewrite:*
- `pytest` prefix → `updatedInput` with `tail`; `grep` → `head`; non-matching → no rewrite; metacharacter prefix in config → skipped, other rules apply; user rule in config → applied; disabled passes through

All tests set `CCGATE_HOME` via env var pointing to `tmp_path`.

---

## 10. Files Created / Modified

| Action | Path |
|---|---|
| CREATE | `src/ccgate/hooks/pre_tool.py` |
| CREATE | `tests/test_pre_tool.py` |
| CREATE | `tests/fixtures/g4g5_clean.jsonl` |
| CREATE | `tests/perf_pre_tool.py` |
| MODIFY | `src/ccgate/scripts/miss_audit.py` (G4/G5 in `--assert`) |
| MODIFY | `hooks/hooks.json` (add PreToolUse) |
| MODIFY | `.claude-plugin/plugin.json` (add PreToolUse) |

---

## 11. Global Constraints

- Python 3.11+, stdlib only — no third-party packages
- `CCGATE_HOME` env var overrides `~/.ccgate` in all state functions
- `os.replace(tmp, target)` for all atomic writes
- `acquire_lock` from `ccgate.state` for all read-mutate-write on read-cache state
- Exit 0 always — all exceptions caught at top level with `try/except Exception: pass`
- Token estimates carry `~` prefix wherever surfaced in user-visible strings (I4)
- Deny reason always states the override mechanism (config key to set)
- Every rule is independently toggled by its own config boolean (default `false`)
