# Phase 3 — CI and Fleet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four standalone CLI tools — `baseline.py`, `digest.py`, `otel_reader.py`, `clear_eval.py` — extending ccgate into a CI gate and team-sharing system.

**Architecture:** Four independent scripts under `src/ccgate/scripts/`, each using existing shared modules (`ccgate.config`, `ccgate.state`, `ccgate.model`, `ccgate.transcript`). None call each other. Hooks and core pipeline remain stdlib-only.

**Tech Stack:** Python 3.11+, stdlib only (except `anthropic` package in `clear_eval.py`, guarded with `try/except ImportError`). pytest for tests. All code goes in the worktree at `.claude/worktrees/ccgate-phase0/`.

**Spec:** `docs/superpowers/specs/2026-09-21-phase3-ci-fleet-design.md`

## Global Constraints

- Python 3.11+ required; no f-string assignment expressions
- `CCGATE_HOME` env var overrides `~/.ccgate` — always resolve state paths via `ccgate.state.ccgate_home()`
- All file writes must be atomic: write to `.tmp` then `os.replace(str(tmp), str(target))`
- Hooks and core pipeline stay stdlib-only; `clear_eval.py` is the only script with optional non-stdlib dep
- `anthropic` import in `clear_eval.py` must be inside `try/except ImportError` — never at module level
- `ANTHROPIC_API_KEY` read from environment only — never hardcoded
- USD figures carry `~` prefix (BUILD-SPEC I4)
- Zero or negative savings always reported as `0`, never as a negative number
- No absolute paths hardcoded; use `Path.home()`, `os.environ`, or relative paths
- Commit messages end with: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
- Test command from worktree root: `pytest tests/ -v`

---

## File Map

| Action | Path (relative to worktree `.claude/worktrees/ccgate-phase0/`) |
|---|---|
| MODIFY | `src/ccgate/config.py` |
| MODIFY | `schema/ccgate.config.schema.json` |
| CREATE | `src/ccgate/scripts/baseline.py` |
| CREATE | `tests/fixtures/baseline/first_turn.jsonl` |
| CREATE | `tests/test_baseline.py` |
| CREATE | `schema/ccgate.digest.schema.json` |
| CREATE | `src/ccgate/scripts/digest.py` |
| CREATE | `tests/test_digest.py` |
| CREATE | `src/ccgate/scripts/otel_reader.py` |
| CREATE | `tests/fixtures/otlp_export.json` |
| CREATE | `tests/test_otel_reader.py` |
| CREATE | `src/ccgate/scripts/clear_eval.py` |
| CREATE | `tests/test_clear_eval.py` |

---

## Task 1: Config Keys + Schema

Add `startupTokenCap`, `otelPort`, `digestMaxPaths` to config and schema.

**Files:**
- Modify: `src/ccgate/config.py`
- Modify: `schema/ccgate.config.schema.json`
- Create: `tests/test_config_phase3.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `load_config()` returns `cfg["startupTokenCap"]` (int), `cfg["otelPort"]` (int), `cfg["digestMaxPaths"]` (int) — consumed by Tasks 2, 3, 4

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_phase3.py`:

```python
import os
import pytest
from ccgate.config import load_config, DEFAULTS


def test_defaults_include_new_keys():
    cfg = load_config()
    assert cfg["startupTokenCap"] == 12000
    assert cfg["otelPort"] == 4318
    assert cfg["digestMaxPaths"] == 500


def test_env_override_startup_token_cap(monkeypatch):
    monkeypatch.setenv("CCGATE_STARTUP_TOKEN_CAP", "5000")
    cfg = load_config()
    assert cfg["startupTokenCap"] == 5000


def test_range_guard_otel_port_rejects_out_of_range(monkeypatch):
    # 100 < minimum 1024 → falls back to default
    monkeypatch.setenv("CCGATE_OTEL_PORT", "100")
    cfg = load_config()
    assert cfg["otelPort"] == 4318


def test_env_override_digest_max_paths(monkeypatch):
    monkeypatch.setenv("CCGATE_DIGEST_MAX_PATHS", "200")
    cfg = load_config()
    assert cfg["digestMaxPaths"] == 200


def test_new_keys_in_defaults_dict():
    assert "startupTokenCap" in DEFAULTS
    assert "otelPort" in DEFAULTS
    assert "digestMaxPaths" in DEFAULTS
```

- [ ] **Step 2: Run test to verify it fails**

```
cd .claude/worktrees/ccgate-phase0
pytest tests/test_config_phase3.py -v
```

Expected: FAIL — `KeyError: 'startupTokenCap'`

- [ ] **Step 3: Add new keys to `src/ccgate/config.py`**

In `DEFAULTS` dict, add after the existing keys:

```python
    "startupTokenCap": 12000,
    "otelPort": 4318,
    "digestMaxPaths": 500,
```

In `_RANGE` dict, add after the existing entries:

```python
    "startupTokenCap": (1, 200_000),
    "otelPort":        (1024, 65535),
    "digestMaxPaths":  (1, 10_000),
```

- [ ] **Step 4: Update `schema/ccgate.config.schema.json`**

Inside the `"properties"` object, add before the closing `}`:

```json
    "contextignoreEnabled": {"type": "boolean",                                "default": false},
    "bashRewriteEnabled":   {"type": "boolean",                                "default": false},
    "startupTokenCap":      {"type": "integer", "minimum": 1, "maximum": 200000, "default": 12000},
    "otelPort":             {"type": "integer", "minimum": 1024, "maximum": 65535, "default": 4318},
    "digestMaxPaths":       {"type": "integer", "minimum": 1, "maximum": 10000, "default": 500}
```

Note: open the file first to see the current last property; add the three new properties after it, keeping JSON syntax valid (comma on the previous last property).

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/test_config_phase3.py -v
```

Expected: 5 passed

- [ ] **Step 6: Run full suite to check for regressions**

```
pytest tests/ -v
```

Expected: all existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add src/ccgate/config.py schema/ccgate.config.schema.json tests/test_config_phase3.py
git commit -m "feat: add Phase 3 config keys (startupTokenCap, otelPort, digestMaxPaths)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: `baseline.py` — G1 Gate

G1 gate script: reads startup token count from a fixture and fails if it exceeds `startupTokenCap`.

**Files:**
- Create: `tests/fixtures/baseline/first_turn.jsonl`
- Create: `src/ccgate/scripts/baseline.py`
- Create: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `ccgate.config.load_config()` → `cfg["startupTokenCap"]`
- Produces: exit 0 (pass) or exit 1 (fail); stdout human or JSON line — no Python API, CLI only

- [ ] **Step 1: Create the fixture file**

Create `tests/fixtures/baseline/` directory and `tests/fixtures/baseline/first_turn.jsonl`:

```
# baseline fixture — calibrated 2026-09-21
# input_tokens=8000 reflects realistic startup footprint: plugin manifest + 1 CLAUDE.md + skills listing
# update this value if ccgate's startup footprint grows toward the 12000 cap
# parser (baseline.py) skips non-JSON lines — this comment header is intentional
{"type":"assistant","timestamp":"2026-09-21T00:00:00Z","message":{"model":"claude-sonnet-5","usage":{"input_tokens":8000,"output_tokens":50,"cache_read_input_tokens":0,"cache_creation_input_tokens":0},"content":[{"type":"text","text":"ccgate startup fixture"}]}}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_baseline.py`:

```python
import json
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREE = Path(__file__).parent.parent
FIXTURE = WORKTREE / "tests" / "fixtures" / "baseline" / "first_turn.jsonl"


def _run(*args):
    """Run baseline as a subprocess; return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "ccgate.scripts.baseline", *args],
        capture_output=True,
        text=True,
        cwd=str(WORKTREE),
        env={**__import__("os").environ, "PYTHONPATH": str(WORKTREE / "src")},
    )
    return result.returncode, result.stdout, result.stderr


def test_passes_on_fixture():
    rc, out, _ = _run("--fixture", str(FIXTURE))
    assert rc == 0
    assert "PASS" in out
    assert "8,000" in out


def test_fails_above_cap(tmp_path):
    # Write a fixture with 13 000 tokens — above the default 12 000 cap
    fixture = tmp_path / "first_turn.jsonl"
    fixture.write_text(
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-21T00:00:00Z",
            "message": {
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 13000, "output_tokens": 10,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        }) + "\n",
        encoding="utf-8",
    )
    rc, out, _ = _run("--fixture", str(fixture))
    assert rc == 1
    assert "FAIL" in out


def test_custom_cap_flag():
    # Bundled fixture has 8 000; cap 7 000 → should fail
    rc, out, _ = _run("--fixture", str(FIXTURE), "--cap", "7000")
    assert rc == 1
    assert "FAIL" in out


def test_json_output():
    rc, out, _ = _run("--fixture", str(FIXTURE), "--json")
    assert rc == 0
    data = json.loads(out)
    assert data["tokens"] == 8000
    assert data["cap"] == 12000
    assert data["pass"] is True


def test_missing_fixture(tmp_path):
    rc, _, err = _run("--fixture", str(tmp_path / "nonexistent.jsonl"))
    assert rc == 1
    assert "not found" in err.lower() or "fixture" in err.lower()
```

- [ ] **Step 3: Run tests to verify they fail**

```
pytest tests/test_baseline.py -v
```

Expected: 5 failures — `ModuleNotFoundError: No module named 'ccgate.scripts.baseline'`

- [ ] **Step 4: Create `src/ccgate/scripts/baseline.py`**

```python
"""baseline.py — G1 gate: assert ccgate startup overhead ≤ startupTokenCap."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ccgate.config import load_config

_DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "baseline" / "first_turn.jsonl"


def _parse_fixture(fixture_path: Path) -> int:
    """Return input_tokens from the first assistant entry; skip non-JSON lines."""
    with fixture_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            usage = (entry.get("message") or {}).get("usage") or {}
            tokens = usage.get("input_tokens")
            if tokens is not None:
                return int(tokens)
    raise ValueError(f"No assistant entry with input_tokens found in {fixture_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="G1 gate: check ccgate startup token overhead"
    )
    parser.add_argument("--fixture", type=Path, default=None,
                        help="Path to JSONL fixture (default: bundled first_turn.jsonl)")
    parser.add_argument("--cap", type=int, default=None,
                        help="Token cap override (default: startupTokenCap from config)")
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Emit JSON output instead of human-readable")
    args = parser.parse_args(argv)

    fixture = args.fixture if args.fixture is not None else _DEFAULT_FIXTURE

    if not fixture.exists():
        print(f"baseline: fixture not found: {fixture}", file=sys.stderr)
        return 1

    try:
        tokens = _parse_fixture(fixture)
    except ValueError as e:
        print(f"baseline: {e}", file=sys.stderr)
        return 1

    cfg = load_config()
    cap = args.cap if args.cap is not None else cfg["startupTokenCap"]
    passed = tokens <= cap

    if args.json_out:
        print(json.dumps({"tokens": tokens, "cap": cap, "pass": passed}))
    else:
        label = "PASS" if passed else "FAIL"
        print(f"startup: {tokens:,} tokens  [{label} ≤ {cap:,}]")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_baseline.py -v
```

Expected: 5 passed

- [ ] **Step 6: Run full suite**

```
pytest tests/ -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/ccgate/scripts/baseline.py tests/fixtures/baseline/first_turn.jsonl tests/test_baseline.py
git commit -m "feat: add baseline.py G1 gate and fixture

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: `digest.py` — Pattern Digest Export/Import

Export learned file-access patterns to a portable JSON digest; import a digest and merge into local state.

**Files:**
- Create: `schema/ccgate.digest.schema.json`
- Create: `src/ccgate/scripts/digest.py`
- Create: `tests/test_digest.py`

**Interfaces:**
- Consumes: `ccgate.state.ccgate_home()`, `ccgate.config.load_config()` → `cfg["digestMaxPaths"]`
- Produces: `digest.json` file (export); `~/.ccgate/patterns.json` (import) — CLI only

**Read-cache state format** (written by `src/ccgate/hooks/pre_tool.py`):
```json
{
  "reads": {
    "path:offset:limit": {"mtime": 1234567890.0, "ts": "ISO-8601", "idx": 0}
  },
  "file_log": ["/abs/path/to/file.py", "/abs/path/to/other.py"],
  "seq": 2
}
```
`file_log` is the ordered list of ALL path reads that were ALLOWED (blocks leave no trace). `read_count` for a path = number of appearances in `file_log`. `cache_block_count` is not derivable from current state format; always export as 0 (placeholder for future phases).

- [ ] **Step 1: Create `schema/ccgate.digest.schema.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["digest_id", "exported_at", "ccgate_version", "path_stats", "sessions_analyzed"],
  "additionalProperties": false,
  "properties": {
    "digest_id":          { "type": "string" },
    "exported_at":        { "type": "string", "format": "date-time" },
    "ccgate_version":     { "type": "string" },
    "sessions_analyzed":  { "type": "integer", "minimum": 0 },
    "date_range": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "first": { "type": "string" },
        "last":  { "type": "string" }
      }
    },
    "path_stats": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["path", "read_count", "cache_block_count"],
        "additionalProperties": false,
        "properties": {
          "path":              { "type": "string", "pattern": "^(?!/)(?!.*\\.\\.).*" },
          "read_count":        { "type": "integer", "minimum": 0 },
          "cache_block_count": { "type": "integer", "minimum": 0 }
        }
      }
    }
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_digest.py`:

```python
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).parent.parent
SCHEMA_PATH = WORKTREE / "schema" / "ccgate.digest.schema.json"


def _run(*args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src")}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "ccgate.scripts.digest", *args],
        capture_output=True, text=True, cwd=str(WORKTREE), env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _make_rc(tmp_path, session_id, file_log, reads=None):
    """Write a minimal read-cache JSON file."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    rc = {"reads": reads or {}, "file_log": file_log, "seq": len(file_log)}
    (sessions_dir / f"{session_id}-read-cache.json").write_text(
        json.dumps(rc), encoding="utf-8"
    )


def test_export_produces_relative_paths(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_path = str(tmp_path / "project" / "src" / "main.py")
    _make_rc(tmp_path, "sess1", [abs_path, abs_path])

    out_file = tmp_path / "digest.json"
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err
    data = json.loads(out_file.read_text())
    paths = [e["path"] for e in data["path_stats"]]
    assert all(not os.path.isabs(p) for p in paths)


def test_export_rejects_absolute_paths(tmp_path, monkeypatch):
    # Absolute path that cannot be made relative — different Windows drive
    # Simulate by using a path that produces an absolute relpath
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    # Use a path outside cwd that would be relative but starts with ..
    outside_path = str(tmp_path / "other" / "secret.py")
    inside_path = str(tmp_path / "project" / "ok.py")
    _make_rc(tmp_path, "sess1", [outside_path, inside_path])

    out_file = tmp_path / "digest.json"
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    # The outside path has ".." in relpath and is skipped; inside path is exported
    exported_paths = [e["path"] for e in data["path_stats"]]
    assert not any(".." in p for p in exported_paths)


def test_export_rejects_dotdot(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_outside = str(tmp_path / "secret.py")  # one level above cwd
    _make_rc(tmp_path, "sess1", [abs_outside])

    out_file = tmp_path / "digest.json"
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    # Path with ".." must not appear in output
    assert all(".." not in e["path"] for e in data["path_stats"])


def test_export_caps_at_digest_max_paths(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    # Create 10 distinct paths; set cap to 5
    file_log = [str(tmp_path / "project" / f"f{i}.py") for i in range(10)]
    _make_rc(tmp_path, "sess1", file_log)

    out_file = tmp_path / "digest.json"
    env_extra = {"CCGATE_HOME": str(tmp_path), "CCGATE_DIGEST_MAX_PATHS": "5"}
    rc, _, err = _run(
        "export", "--out", str(out_file), "--cwd", cwd, env_extra=env_extra,
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    assert len(data["path_stats"]) <= 5


def test_export_schema_valid(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_path = str(tmp_path / "project" / "main.py")
    _make_rc(tmp_path, "sess1", [abs_path, abs_path])

    out_file = tmp_path / "digest.json"
    rc, _, _ = _run(
        "export", "--out", str(out_file), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0
    data = json.loads(out_file.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    # Manual validation of required fields
    for field in schema["required"]:
        assert field in data, f"Missing required field: {field}"
    for entry in data["path_stats"]:
        for field in ("path", "read_count", "cache_block_count"):
            assert field in entry
        assert isinstance(entry["read_count"], int)
        assert isinstance(entry["cache_block_count"], int)
        assert not os.path.isabs(entry["path"])


def test_import_merges_counts(tmp_path):
    # Pre-populate patterns.json with one path
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()
    existing = {"src/a.py": {"read_count": 3, "cache_block_count": 0}}
    (ccgate_home / "patterns.json").write_text(json.dumps(existing))

    # Create a digest that adds to a.py and introduces b.py
    digest = {
        "digest_id": "test-123",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [
            {"path": "src/a.py", "read_count": 2, "cache_block_count": 0},
            {"path": "src/b.py", "read_count": 5, "cache_block_count": 0},
        ],
    }
    digest_file = tmp_path / "digest.json"
    digest_file.write_text(json.dumps(digest))

    rc, out, err = _run(
        "import", str(digest_file),
        env_extra={"CCGATE_HOME": str(ccgate_home)},
    )
    assert rc == 0, err
    patterns = json.loads((ccgate_home / "patterns.json").read_text())
    assert patterns["src/a.py"]["read_count"] == 5   # 3 + 2
    assert patterns["src/b.py"]["read_count"] == 5


def test_import_rejects_absolute_path(tmp_path):
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()

    digest = {
        "digest_id": "bad-1",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [
            {"path": "/etc/passwd", "read_count": 1, "cache_block_count": 0},
        ],
    }
    digest_file = tmp_path / "bad_digest.json"
    digest_file.write_text(json.dumps(digest))

    rc, _, err = _run(
        "import", str(digest_file),
        env_extra={"CCGATE_HOME": str(ccgate_home)},
    )
    assert rc != 0
    # patterns.json must NOT have been created
    assert not (ccgate_home / "patterns.json").exists()


def test_import_rejects_dotdot(tmp_path):
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()

    digest = {
        "digest_id": "bad-2",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [
            {"path": "../secret/file.py", "read_count": 1, "cache_block_count": 0},
        ],
    }
    digest_file = tmp_path / "dotdot_digest.json"
    digest_file.write_text(json.dumps(digest))

    rc, _, err = _run(
        "import", str(digest_file),
        env_extra={"CCGATE_HOME": str(ccgate_home)},
    )
    assert rc != 0
    assert not (ccgate_home / "patterns.json").exists()


def test_import_atomic(tmp_path, monkeypatch):
    # If os.replace raises, patterns.json must remain unchanged
    ccgate_home = tmp_path / "ccgate"
    ccgate_home.mkdir()
    original = {"src/keep.py": {"read_count": 10, "cache_block_count": 0}}
    (ccgate_home / "patterns.json").write_text(json.dumps(original))

    digest = {
        "digest_id": "atomic-test",
        "exported_at": "2026-09-21T00:00:00Z",
        "ccgate_version": "0.1.0",
        "sessions_analyzed": 1,
        "path_stats": [{"path": "src/new.py", "read_count": 1, "cache_block_count": 0}],
    }
    digest_file = tmp_path / "digest.json"
    digest_file.write_text(json.dumps(digest))

    # Patch os.replace inside digest module to raise before commit
    import ccgate.scripts.digest as digest_mod
    original_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(digest_mod.os, "replace", failing_replace)

    with pytest.raises(SystemExit):
        digest_mod._do_import(str(digest_file), str(ccgate_home))

    # patterns.json unchanged
    surviving = json.loads((ccgate_home / "patterns.json").read_text())
    assert surviving == original


def test_round_trip(tmp_path):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    abs_path = str(tmp_path / "project" / "src" / "app.py")
    _make_rc(tmp_path, "sess1", [abs_path, abs_path, abs_path])

    # Export
    out1 = tmp_path / "digest1.json"
    rc, _, err = _run(
        "export", "--out", str(out1), "--cwd", cwd,
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err

    # Import into fresh home
    ccgate_home2 = tmp_path / "home2"
    ccgate_home2.mkdir()
    rc, _, err = _run(
        "import", str(out1),
        env_extra={"CCGATE_HOME": str(ccgate_home2)},
    )
    assert rc == 0, err

    # Verify patterns were merged
    patterns = json.loads((ccgate_home2 / "patterns.json").read_text())
    # src/app.py appears 3 times in file_log → read_count == 3
    rel = os.path.relpath(abs_path, cwd)
    assert patterns[rel]["read_count"] == 3
```

- [ ] **Step 3: Run tests to verify they fail**

```
pytest tests/test_digest.py -v
```

Expected: failures — `ModuleNotFoundError: No module named 'ccgate.scripts.digest'`

- [ ] **Step 4: Create `src/ccgate/scripts/digest.py`**

```python
"""digest.py — export/import learned file-access patterns as a portable digest."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ccgate.config import load_config
from ccgate.state import ccgate_home


def _validate_import_digest(raw: dict) -> None:
    """Raise SystemExit on missing required fields or bad path entries."""
    for field in ("digest_id", "exported_at", "ccgate_version", "path_stats", "sessions_analyzed"):
        if field not in raw:
            print(f"digest import: missing required field '{field}'", file=sys.stderr)
            sys.exit(1)
    if not isinstance(raw["path_stats"], list):
        print("digest import: path_stats must be a list", file=sys.stderr)
        sys.exit(1)
    for entry in raw["path_stats"]:
        path = entry.get("path", "")
        if os.path.isabs(path):
            print(f"digest import: ABORTED — absolute path detected: {path!r}", file=sys.stderr)
            sys.exit(1)
        if ".." in Path(path).parts:
            print(f"digest import: ABORTED — path traversal detected: {path!r}", file=sys.stderr)
            sys.exit(1)


def _do_export(out_path_str: str | None, cwd_str: str | None, home: Path | None = None) -> None:
    sessions_dir = (home or ccgate_home()) / "sessions"
    cwd = Path(cwd_str) if cwd_str else Path.cwd()
    cfg = load_config()
    max_paths = cfg["digestMaxPaths"]

    path_read_counts: dict[str, int] = {}
    sessions_analyzed = 0

    if sessions_dir.exists():
        for rc_file in sessions_dir.glob("*-read-cache.json"):
            try:
                rc = json.loads(rc_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            sessions_analyzed += 1
            for abs_path in rc.get("file_log", []):
                try:
                    rel = os.path.relpath(abs_path, cwd)
                except ValueError:
                    print(f"digest: skipping {abs_path!r} (cannot relativize)", file=sys.stderr)
                    continue
                if os.path.isabs(rel):
                    print(f"digest: skipping {abs_path!r} (absolute relpath)", file=sys.stderr)
                    continue
                if ".." in Path(rel).parts:
                    print(f"digest: skipping {abs_path!r} (.. traversal)", file=sys.stderr)
                    continue
                path_read_counts[rel] = path_read_counts.get(rel, 0) + 1

    sorted_paths = sorted(path_read_counts.items(), key=lambda x: -x[1])[:max_paths]

    try:
        from importlib.metadata import version as _pkg_version
        ccgate_version = _pkg_version("ccgate")
    except Exception:
        ccgate_version = "0.1.0"

    digest = {
        "digest_id": str(uuid.uuid4()),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "ccgate_version": ccgate_version,
        "sessions_analyzed": sessions_analyzed,
        "path_stats": [
            {"path": rel, "read_count": count, "cache_block_count": 0}
            for rel, count in sorted_paths
        ],
    }

    out_path = Path(out_path_str) if out_path_str else Path("digest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(out_path))
    print(f"exported {len(sorted_paths)} paths to {out_path}")


def _do_import(digest_path_str: str, home_str: str | None = None) -> None:
    digest_path = Path(digest_path_str)
    try:
        raw = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"digest import: cannot read {digest_path}: {e}", file=sys.stderr)
        sys.exit(1)

    _validate_import_digest(raw)

    home = Path(home_str) if home_str else ccgate_home()
    patterns_path = home / "patterns.json"

    if patterns_path.exists():
        try:
            patterns: dict = json.loads(patterns_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            patterns = {}
    else:
        patterns = {}

    imported = 0
    for entry in raw["path_stats"]:
        path = entry["path"]
        existing = patterns.get(path, {"read_count": 0, "cache_block_count": 0})
        existing["read_count"] = existing.get("read_count", 0) + entry.get("read_count", 0)
        existing["cache_block_count"] = (
            existing.get("cache_block_count", 0) + entry.get("cache_block_count", 0)
        )
        patterns[path] = existing
        imported += 1

    imports_meta = patterns.setdefault("_imports", [])
    imports_meta.append({
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "digest_id": raw.get("digest_id"),
        "paths_merged": imported,
    })

    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = patterns_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(patterns, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(patterns_path))

    print(f"imported {imported} paths from {raw.get('digest_id')}; 0 entries rejected")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ccgate pattern digest export/import")
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="Export learned path patterns to a digest file")
    exp.add_argument("--out", default=None, help="Output file path (default: digest.json)")
    exp.add_argument("--cwd", default=None, help="Base directory for relative path conversion")

    imp = sub.add_parser("import", help="Import a digest file and merge into local patterns")
    imp.add_argument("path", help="Path to the digest.json file to import")

    args = parser.parse_args(argv)

    if args.command == "export":
        _do_export(args.out, args.cwd)
    else:
        _do_import(args.path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note on `test_import_atomic`: this test calls `digest_mod._do_import(...)` directly with monkeypatched `os.replace`. If `_do_import` calls `sys.exit(1)` on the `OSError`, the test catches `SystemExit`. If it raises `OSError` instead, the test should use `pytest.raises(OSError)`. The implementation above re-raises `OSError` from the `os.replace` call (no try/except around it), so adjust the test to `pytest.raises(OSError)` if needed.

- [ ] **Step 5: Fix `test_import_atomic` if needed**

After running the tests, if `test_import_atomic` fails because `OSError` propagates (not `SystemExit`), change the test's `pytest.raises(SystemExit)` to `pytest.raises(OSError)`.

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_digest.py -v
```

Expected: 10 passed

- [ ] **Step 7: Run full suite**

```
pytest tests/ -v
```

- [ ] **Step 8: Commit**

```bash
git add schema/ccgate.digest.schema.json src/ccgate/scripts/digest.py tests/test_digest.py
git commit -m "feat: add digest.py pattern export/import and schema

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: `otel_reader.py` — OTLP Collector

Receive `claude_code.token.usage` metrics via OTLP HTTP or file; write ccgate session accounting files.

**Files:**
- Create: `tests/fixtures/otlp_export.json`
- Create: `src/ccgate/scripts/otel_reader.py`
- Create: `tests/test_otel_reader.py`

**Interfaces:**
- Consumes: `ccgate.state.ccgate_home()`, `ccgate.config.load_config()` → `cfg["otelPort"]`
- Produces: `~/.ccgate/sessions/{session_id}-otel.json` with shape `{"session_id": str, "collected_at": str, "source": str, "tokens": {"input": int, "output": int, "cache_read": int, "cache_creation": int}}`

**OTLP JSON shape** (the only shape handled):
```json
{
  "resourceMetrics": [{
    "resource": {
      "attributes": [{"key": "session.id", "value": {"stringValue": "<id>"}}]
    },
    "scopeMetrics": [{
      "metrics": [{
        "name": "claude_code.token.usage",
        "sum": {
          "dataPoints": [
            {"attributes": [{"key": "type", "value": {"stringValue": "input"}}], "asInt": "12345"}
          ]
        }
      }]
    }]
  }]
}
```

- [ ] **Step 1: Create `tests/fixtures/otlp_export.json`**

```json
{
  "resourceMetrics": [{
    "resource": {
      "attributes": [
        {"key": "session.id", "value": {"stringValue": "fixture-session-001"}}
      ]
    },
    "scopeMetrics": [{
      "metrics": [{
        "name": "claude_code.token.usage",
        "sum": {
          "dataPoints": [
            {"attributes": [{"key": "type", "value": {"stringValue": "input"}}],
             "asInt": "12345"},
            {"attributes": [{"key": "type", "value": {"stringValue": "output"}}],
             "asInt": "678"},
            {"attributes": [{"key": "type", "value": {"stringValue": "cache_read"}}],
             "asInt": "9800"},
            {"attributes": [{"key": "type", "value": {"stringValue": "cache_creation"}}],
             "asInt": "500"},
            {"attributes": [{"key": "type", "value": {"stringValue": "experimental_new_type"}}],
             "asInt": "42"}
          ]
        }
      }]
    }]
  }]
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_otel_reader.py`:

```python
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

import pytest

WORKTREE = Path(__file__).parent.parent
FIXTURE = WORKTREE / "tests" / "fixtures" / "otlp_export.json"


def _run(*args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src")}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", *args],
        capture_output=True, text=True, cwd=str(WORKTREE), env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_payload(payload: dict) -> dict:
    """Call parse_otlp_payload directly for unit tests."""
    sys.path.insert(0, str(WORKTREE / "src"))
    from ccgate.scripts.otel_reader import parse_otlp_payload
    return parse_otlp_payload(payload)


def test_parse_standard_payload():
    payload = json.loads(FIXTURE.read_text())
    result = _parse_payload(payload)
    assert result["session_id"] == "fixture-session-001"
    assert result["tokens"]["input"] == 12345
    assert result["tokens"]["output"] == 678
    assert result["tokens"]["cache_read"] == 9800
    assert result["tokens"]["cache_creation"] == 500


def test_unknown_type_label():
    payload = json.loads(FIXTURE.read_text())
    result = _parse_payload(payload)
    # "experimental_new_type" → stored under "unknown", not dropped
    assert result["tokens"].get("unknown", 0) == 42


def test_missing_session_id():
    payload = {
        "resourceMetrics": [{
            "resource": {"attributes": []},
            "scopeMetrics": [{
                "metrics": [{
                    "name": "claude_code.token.usage",
                    "sum": {"dataPoints": [
                        {"attributes": [{"key": "type", "value": {"stringValue": "input"}}],
                         "asInt": "100"}
                    ]}
                }]
            }]
        }]
    }
    result = _parse_payload(payload)
    assert result["session_id"] == "unknown"
    assert result["tokens"]["input"] == 100


def test_asDouble_fallback():
    payload = {
        "resourceMetrics": [{
            "resource": {"attributes": [
                {"key": "session.id", "value": {"stringValue": "double-session"}}
            ]},
            "scopeMetrics": [{
                "metrics": [{
                    "name": "claude_code.token.usage",
                    "sum": {"dataPoints": [
                        {"attributes": [{"key": "type", "value": {"stringValue": "input"}}],
                         "asDouble": 5000.0}
                    ]}
                }]
            }]
        }]
    }
    result = _parse_payload(payload)
    assert result["tokens"]["input"] == 5000


def test_file_mode_writes_session(tmp_path):
    rc, out, err = _run(
        "read", "--file", str(FIXTURE),
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err
    otel_file = tmp_path / "sessions" / "fixture-session-001-otel.json"
    assert otel_file.exists(), f"Expected {otel_file} to exist"
    data = json.loads(otel_file.read_text())
    assert data["session_id"] == "fixture-session-001"
    assert data["source"] == "otlp_file"
    assert data["tokens"]["input"] == 12345
    assert data["tokens"]["cache_read"] == 9800


def test_atomic_write(tmp_path):
    # File is written atomically: check no partial file remains on success
    rc, _, err = _run(
        "read", "--file", str(FIXTURE),
        env_extra={"CCGATE_HOME": str(tmp_path)},
    )
    assert rc == 0, err
    # .tmp file must not remain after successful write
    tmp_files = list((tmp_path / "sessions").glob("*.tmp"))
    assert tmp_files == [], f"Stale .tmp files: {tmp_files}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_post_returns_200(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src"),
           "CCGATE_HOME": str(tmp_path), "CCGATE_OTEL_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", "serve", "--port", str(port)],
        env=env, cwd=str(WORKTREE),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.5)  # give server time to bind
        payload = json.loads(FIXTURE.read_text()).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/metrics",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            assert resp.status == 200
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_server_bad_json_returns_400(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src"),
           "CCGATE_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", "serve", "--port", str(port)],
        env=env, cwd=str(WORKTREE),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.5)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/metrics",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=3)
            assert False, "Expected HTTP 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_server_wrong_path_returns_404(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(WORKTREE / "src"),
           "CCGATE_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ccgate.scripts.otel_reader", "serve", "--port", str(port)],
        env=env, cwd=str(WORKTREE),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.5)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/wrong/path",
            method="GET",
        )
        try:
            urllib.request.urlopen(req, timeout=3)
            assert False, "Expected HTTP 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        proc.terminate()
        proc.wait(timeout=3)
```

- [ ] **Step 3: Run tests to verify they fail**

```
pytest tests/test_otel_reader.py -v
```

Expected: failures — `ModuleNotFoundError: No module named 'ccgate.scripts.otel_reader'`

- [ ] **Step 4: Create `src/ccgate/scripts/otel_reader.py`**

```python
"""otel_reader.py — OTLP metric collector for claude_code.token.usage."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ccgate.config import load_config
from ccgate.state import ccgate_home

_KNOWN_TYPES = frozenset({"input", "output", "cache_read", "cache_creation"})


def parse_otlp_payload(payload: dict) -> dict:
    """Extract session_id and token counts from an OTLP JSON payload."""
    session_id = "unknown"
    tokens: dict[str, int] = {
        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0
    }

    for rm in payload.get("resourceMetrics", []):
        # Extract session.id from resource attributes
        for attr in (rm.get("resource") or {}).get("attributes", []):
            if attr.get("key") == "session.id":
                val = (attr.get("value") or {}).get("stringValue")
                if val:
                    session_id = val

        for sm in rm.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                if metric.get("name") != "claude_code.token.usage":
                    continue
                for dp in (metric.get("sum") or {}).get("dataPoints", []):
                    type_label = "unknown"
                    for attr in dp.get("attributes", []):
                        if attr.get("key") == "type":
                            raw_label = (attr.get("value") or {}).get("stringValue", "unknown")
                            type_label = raw_label if raw_label in _KNOWN_TYPES else "unknown"
                    # Prefer asInt; fall back to asDouble
                    if "asInt" in dp:
                        value = int(dp["asInt"])
                    elif "asDouble" in dp:
                        value = int(dp["asDouble"])
                    else:
                        continue
                    tokens[type_label] = tokens.get(type_label, 0) + value

    return {"session_id": session_id, "tokens": tokens}


def _write_session_file(parsed: dict, source: str, home: Path | None = None) -> Path:
    """Write {session_id}-otel.json atomically; return the path."""
    h = home or ccgate_home()
    sessions_dir = h / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    out_path = sessions_dir / f"{parsed['session_id']}-otel.json"
    data = {
        "session_id": parsed["session_id"],
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "tokens": parsed["tokens"],
    }
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(out_path))
    return out_path


def _make_handler(home: Path | None = None):
    class OTLPHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/metrics":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"invalid json"}')
                return
            parsed = parse_otlp_payload(payload)
            _write_session_file(parsed, "otlp_http", home)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, fmt, *args):
            pass  # suppress default request logging

    return OTLPHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ccgate OTLP metric collector")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start OTLP HTTP server")
    serve_p.add_argument("--port", type=int, default=None,
                         help="Port to listen on (default: otelPort from config)")

    read_p = sub.add_parser("read", help="Parse a pre-exported OTLP JSON file")
    read_p.add_argument("--file", required=True, help="Path to OTLP JSON file")

    args = parser.parse_args(argv)

    if args.command == "read":
        path = Path(args.file)
        if not path.exists():
            print(f"otel_reader: file not found: {path}", file=sys.stderr)
            return 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"otel_reader: {e}", file=sys.stderr)
            return 1
        parsed = parse_otlp_payload(payload)
        out = _write_session_file(parsed, "otlp_file")
        print(f"written: {out}")
        return 0

    # serve mode
    cfg = load_config()
    port = args.port if args.port is not None else cfg["otelPort"]
    handler = _make_handler()
    server = HTTPServer(("0.0.0.0", port), handler)
    print(f"otel_reader: listening on :{port} for POST /v1/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_otel_reader.py -v
```

Expected: 9 passed (server tests may be slow ~0.5s each — that's normal)

- [ ] **Step 6: Run full suite**

```
pytest tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/otlp_export.json src/ccgate/scripts/otel_reader.py tests/test_otel_reader.py
git commit -m "feat: add otel_reader.py OTLP collector and fixture

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: `clear_eval.py` — Tool-Use Clearing Evaluation

Call the Anthropic `count_tokens` API with `clear_tool_uses_20250919` to measure token savings.

**Files:**
- Create: `src/ccgate/scripts/clear_eval.py`
- Create: `tests/test_clear_eval.py`

**Interfaces:**
- Consumes: `ccgate.transcript.read_transcript(path)` → `list[Request]`, `ccgate.model.get_model_spec(model_id)` → `ModelSpec`
- Produces: stdout human-readable or JSON. No state files written.

**Key types from `ccgate.transcript`:**
```python
# Request.model_id: str — the model string from the assistant message
# Request.usage: Usage — token counts (input_tokens, cache_read_input_tokens, ...)
# read_transcript(path: Path) -> list[Request]
```

**Key from `ccgate.model`:**
```python
# get_model_spec(model_id: str) -> ModelSpec
# ModelSpec.rate_in: float — USD per input token
```

**Mocked API shape** (what tests will mock):
```python
# client.beta.messages.count_tokens(model=..., messages=[...])
#   returns: Mock(input_tokens=N)
# client.beta.messages.count_tokens(model=..., messages=[...],
#                                   context_management={"type":"clear_tool_uses_20250919"})
#   returns: Mock(input_tokens=N,
#                 context_management=Mock(original_input_tokens=N))
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_clear_eval.py`:

```python
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

WORKTREE = Path(__file__).parent.parent
sys.path.insert(0, str(WORKTREE / "src"))


def _make_transcript(tmp_path, model_id="claude-sonnet-5"):
    """Write a minimal single-turn JSONL transcript."""
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-21T00:00:00Z",
        "message": {
            "model": model_id,
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 100,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "content": [{"type": "text", "text": "Hello."}],
        },
    })
    p = tmp_path / "session.jsonl"
    p.write_text(line + "\n", encoding="utf-8")
    return p


def _fake_anthropic(original_tokens=5000, cleared_tokens=3000):
    """Return a mock anthropic module with count_tokens responses."""
    mock_client = MagicMock()
    baseline_resp = MagicMock()
    baseline_resp.input_tokens = original_tokens

    cleared_resp = MagicMock()
    cleared_resp.input_tokens = cleared_tokens
    cleared_resp.context_management = MagicMock()
    cleared_resp.context_management.original_input_tokens = original_tokens

    def count_tokens(**kwargs):
        if "context_management" in kwargs:
            return cleared_resp
        return baseline_resp

    mock_client.beta.messages.count_tokens.side_effect = count_tokens

    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    return mock_anthropic


def test_savings_computed_correctly(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)
    fake_anthro = _fake_anthropic(original_tokens=5000, cleared_tokens=3000)

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib
        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=False)

    assert rc == 0
    assert "5,000" in out   # original
    assert "3,000" in out   # cleared
    assert "2,000" in out   # savings


def test_json_output_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)
    fake_anthro = _fake_anthropic(original_tokens=5000, cleared_tokens=3000)

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib
        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=True)

    assert rc == 0
    data = json.loads(out)
    assert "original_tokens" in data
    assert "cleared_tokens" in data
    assert "savings_tokens" in data
    assert "savings_pct" in data
    assert "savings_usd_approx" in data
    assert data["savings_tokens"] == 2000


def test_missing_api_key_exits_1(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    transcript = _make_transcript(tmp_path)
    fake_anthro = _fake_anthropic()

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib
        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=False)

    assert rc == 1
    assert "ANTHROPIC_API_KEY" in out


def test_missing_anthropic_package(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)

    # Simulate ImportError by removing anthropic from sys.modules and blocking it
    with patch.dict(sys.modules, {"anthropic": None}):
        import importlib
        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=False)

    assert rc == 1
    assert "pip install anthropic" in out.lower() or "anthropic" in out.lower()


def test_zero_savings_reported(tmp_path, monkeypatch):
    # cleared >= original → savings == 0, never negative
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)
    # cleared_tokens > original: simulate no savings (or expansion)
    fake_anthro = _fake_anthropic(original_tokens=3000, cleared_tokens=3500)

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib
        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=True)

    assert rc == 0
    data = json.loads(out)
    assert data["savings_tokens"] == 0
    assert data["savings_usd_approx"] == 0


def test_model_id_from_last_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    # Transcript with two turns using different models
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-21T00:00:00Z",
            "message": {
                "model": "claude-haiku-4-5-20251001",
                "usage": {"input_tokens": 100, "output_tokens": 10,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            },
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-21T00:01:00Z",
            "message": {
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 200, "output_tokens": 20,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            },
        }),
    ]
    transcript = tmp_path / "two_turns.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    seen_models: list[str] = []
    fake_anthro = MagicMock()

    def count_tokens(**kwargs):
        seen_models.append(kwargs.get("model"))
        resp = MagicMock()
        resp.input_tokens = 200
        resp.context_management = MagicMock()
        resp.context_management.original_input_tokens = 200
        return resp

    fake_anthro.Anthropic.return_value.beta.messages.count_tokens.side_effect = count_tokens

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib
        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        mod._run_eval(str(transcript), json_out=False)

    # All count_tokens calls use the model from the LAST turn
    assert all(m == "claude-sonnet-5" for m in seen_models if m is not None)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_clear_eval.py -v
```

Expected: failures — `ModuleNotFoundError: No module named 'ccgate.scripts.clear_eval'`

- [ ] **Step 3: Create `src/ccgate/scripts/clear_eval.py`**

```python
"""clear_eval.py — measure token savings from clear_tool_uses_20250919."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ccgate.model import get_model_spec
from ccgate.transcript import read_transcript


def _error(msg: str) -> tuple[int, str]:
    return 1, msg


def _run_eval(transcript_path: str, json_out: bool) -> tuple[int, str]:
    """Run the evaluation; return (returncode, output_string)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _error(
            "clear_eval: ANTHROPIC_API_KEY environment variable not set.\n"
            "Set it with: export ANTHROPIC_API_KEY=sk-..."
        )

    try:
        import anthropic
        if anthropic is None:
            raise ImportError("anthropic is None")
    except (ImportError, TypeError):
        return _error(
            "clear_eval: anthropic package not installed.\n"
            "Install with: pip install anthropic"
        )

    path = Path(transcript_path)
    if not path.exists():
        return _error(f"clear_eval: transcript not found: {path}")

    requests = read_transcript(path)
    if not requests:
        return _error("clear_eval: no assistant turns found in transcript")

    last_model = requests[-1].model_id

    # Reconstruct messages list from transcript entries
    messages: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_type = entry.get("type")
            msg = entry.get("message") or {}
            content = msg.get("content") or ""
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                content = " ".join(text_parts)
            if entry_type == "user":
                messages.append({"role": "user", "content": content})
            elif entry_type == "assistant":
                messages.append({"role": "assistant", "content": content})

    if not messages:
        return _error("clear_eval: no messages found in transcript")

    client = anthropic.Anthropic(api_key=api_key)

    baseline_resp = client.beta.messages.count_tokens(
        model=last_model,
        messages=messages,
    )
    original_tokens: int = baseline_resp.input_tokens

    cleared_resp = client.beta.messages.count_tokens(
        model=last_model,
        messages=messages,
        context_management={"type": "clear_tool_uses_20250919"},
    )
    cleared_tokens: int = cleared_resp.input_tokens
    original_before: int = cleared_resp.context_management.original_input_tokens

    raw_savings = original_before - cleared_tokens
    savings_tokens = max(0, raw_savings)
    savings_pct = (savings_tokens / original_before * 100) if original_before > 0 else 0.0

    model_spec = get_model_spec(last_model)
    savings_usd = savings_tokens * model_spec.rate_in if savings_tokens > 0 else 0.0

    if json_out:
        out = json.dumps({
            "original_tokens": original_before,
            "cleared_tokens": cleared_tokens,
            "savings_tokens": savings_tokens,
            "savings_pct": round(savings_pct, 2),
            "savings_usd_approx": round(savings_usd, 6),
        })
    else:
        out = (
            f"original : {original_before:,} tokens\n"
            f"cleared  : {cleared_tokens:,} tokens\n"
            f"savings  : {savings_tokens:,} tokens  ({savings_pct:.1f}%)  ~${savings_usd:.4f}"
        )

    return 0, out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure token savings from clear_tool_uses_20250919"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--transcript", metavar="PATH",
                       help="Path to a JSONL transcript file")
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Emit JSON output instead of human-readable")
    args = parser.parse_args(argv)

    rc, out = _run_eval(args.transcript, args.json_out)
    if rc == 0:
        print(out)
    else:
        print(out, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_clear_eval.py -v
```

Expected: 6 passed

Note: if tests for `_run_eval` fail due to module reload issues with the `anthropic` import guard, adjust the test approach: instead of `importlib.reload`, test the function directly by passing the mock as a side effect. The key invariant to preserve: the `try/except ImportError` guard must be inside `_run_eval`, not at module level.

- [ ] **Step 5: Run full suite**

```
pytest tests/ -v
```

Expected: all 30 new tests pass; no regressions in existing suite

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/scripts/clear_eval.py tests/test_clear_eval.py
git commit -m "feat: add clear_eval.py count_tokens evaluation

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

**Spec coverage:**
- §3 (config keys): Task 1 ✓
- §4 (baseline.py): Task 2 ✓
- §5 (digest.py + schema): Task 3 ✓
- §6 (otel_reader.py): Task 4 ✓
- §7 (clear_eval.py): Task 5 ✓
- §8 (G1 gate): covered by baseline.py exit code ✓
- §9 (test count 30): 5 + 10 + 9 + 6 = 30 ✓
- §10 (file map): all 13 files covered ✓
- §11 (constraints): I4 `~` prefix in output ✓, I5 guarded import ✓, atomic writes ✓

**Placeholder scan:** No TBD or TODO in task steps. All code blocks are complete.

**Type consistency:** `read_transcript(path)` → `list[Request]` used correctly in Task 5. `get_model_spec(model_id)` → `ModelSpec.rate_in` used correctly. `ccgate_home()` used in Tasks 3, 4. `load_config()["startupTokenCap"]` in Task 2, `["digestMaxPaths"]` in Task 3, `["otelPort"]` in Task 4.
