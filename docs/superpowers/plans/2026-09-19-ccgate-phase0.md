# ccgate Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-only introspection layer for `ccgate` — live status line, miss-cause audit, and static config lint — with all Phase 0 acceptance gates passing (G2, G3, G8, G9, G10, G11).

**Architecture:** Three scripts (`statusline.py`, `miss_audit.py`, `shape.py`) share a data layer (`model.py`, `transcript.py`, `taxonomy.py`) and load config from `~/.ccgate/config.json`. No hooks registered; nothing written into conversation context. Every acceptance gate is a standalone script exiting 0 or 1.

**Tech Stack:** Python 3.11+, stdlib only at runtime. pytest for tests (dev dependency).

**Spec:** `docs/BUILD-SPEC.md`

## Global Constraints

- Python 3.11+. Zero third-party imports in `src/` or `bin/` — stdlib only per I5.
- `statusline.py` must finish in ≤ 300 ms; no `tput`/shell calls; read width from `os.environ.get("COLUMNS", "80")`.
- cwd→directory encoding: lowercase drive letter on Windows, then replace each of `':', '\\', '/', '.', ' '` with `'-'`. Unit-tested by G10.
- Band detection uses `miss_causes` field presence (not `last_miss_cause`) as the 2.1.260 floor discriminator. Band 1 = `prompt_cache` absent. Band 2 = `prompt_cache` present, `miss_causes` absent. Band 3 = both present.
- Every status-line field access must treat absent keys and `None` identically to zero/false — never raise on absent data.
- `miss_audit.py` output must be byte-identical given the same input (G8): no wall-clock timestamps in the report body.
- All emitted JSON must validate against `schema/*.json` (G9).
- D2 misses (`D2.compaction`, `D2.tool_result_clearing`) are reported separately and **never** counted against the hit-ratio gate.
- Miss threshold (§5.2): `re_processed > 5% of expected_cache` AND `re_processed >= 2000 tokens`.
- Never read `costUSD` from the transcript; always recompute from token counts × pricing table (I4).
- Out-of-range or unknown config keys: fall back to default and flag — never silently apply.

---

## File Map

```
ccgate/
  .claude-plugin/plugin.json          # Task 11 — no hooks in Phase 0
  bin/ccgate                          # Task 11
  src/ccgate/
    __init__.py                       # Task 1
    config.py                         # Task 1
    taxonomy.py                       # Task 1
    model.py                          # Task 2
    transcript.py                     # Tasks 3–4
    scripts/
      __init__.py                     # Task 1
      statusline.py                   # Task 9
      miss_audit.py                   # Task 6
      shape.py                        # Task 8
  schema/
    ccgate.config.schema.json         # Task 1
    ccgate.session.schema.json        # Task 1
    ccgate.report.schema.json         # Task 1
  skills/ccgate/SKILL.md              # Task 11
  tests/
    __init__.py                       # Task 1
    fixtures/
      transcripts/
        d1_model_switch.jsonl         # Task 5
        d1_ttl_expired.jsonl          # Task 5
        d2_compaction.jsonl           # Task 5
        clean_15req.jsonl             # Task 5
      statusline/
        band1_payload.json            # Task 5
        band2_payload.json            # Task 5
        band3_payload.json            # Task 5
    test_taxonomy.py                  # Task 1
    test_model.py                     # Task 2
    test_transcript.py                # Tasks 3–4
    test_miss_audit.py                # Task 6
    test_shape.py                     # Task 8
    test_statusline.py                # Task 9
    paths.py                          # Task 3 (G10 gate)
    degrade.py                        # Task 10 (G11 gate)
    determinism.py                    # Task 7 (G8 gate)
    schema_check.py                   # Task 7 (G9 gate)
  pyproject.toml                      # Task 1
```

---

### Task 1: Scaffold, taxonomy, config loader, and schemas

**Files:**
- Create: `pyproject.toml`
- Create: `src/ccgate/__init__.py`
- Create: `src/ccgate/scripts/__init__.py`
- Create: `src/ccgate/taxonomy.py`
- Create: `src/ccgate/config.py`
- Create: `schema/ccgate.config.schema.json`
- Create: `schema/ccgate.session.schema.json`
- Create: `schema/ccgate.report.schema.json`
- Create: `tests/__init__.py`
- Create: `tests/test_taxonomy.py`

**Interfaces:**
- Produces: `taxonomy.*` constants used by every other module; `load_config()` returning a validated dict used by statusline, miss_audit, shape; schemas used by `tests/schema_check.py` (G9).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "ccgate"
version = "0.1.0"
requires-python = ">=3.11"

[project.scripts]
ccgate = "ccgate.dispatch:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Create empty `__init__` files**

`src/ccgate/__init__.py` and `src/ccgate/scripts/__init__.py` and `tests/__init__.py` — all empty.

- [ ] **Step 3: Write the failing taxonomy test**

```python
# tests/test_taxonomy.py
from ccgate import taxonomy

def test_d1_codes_are_strings():
    assert taxonomy.D1_MODEL_SWITCH == "D1.model_switch"
    assert taxonomy.D1_EFFORT_CHANGE == "D1.effort_change"
    assert taxonomy.D1_TOOLS_CHANGED == "D1.tools_changed"
    assert taxonomy.D1_TTL_EXPIRED == "D1.ttl_expired"
    assert taxonomy.D1_UNCLASSIFIED == "D1.unclassified"

def test_d2_never_in_d1_all():
    assert taxonomy.D2_COMPACTION not in taxonomy.D1_ALL
    assert taxonomy.D2_TOOL_RESULT_CLEARING not in taxonomy.D1_ALL

def test_every_d1_has_a_fix_hint():
    for code in taxonomy.D1_ALL:
        assert code in taxonomy.FIX_HINTS, f"No fix hint for {code}"
```

- [ ] **Step 4: Run to verify it fails**

```
pytest tests/test_taxonomy.py -v
```
Expected: `ModuleNotFoundError` (module not yet created).

- [ ] **Step 5: Write `src/ccgate/taxonomy.py`**

```python
D1_MODEL_SWITCH         = "D1.model_switch"
D1_EFFORT_CHANGE        = "D1.effort_change"
D1_TOOLS_CHANGED        = "D1.tools_changed"
D1_SYSTEM_PROMPT_CHANGED = "D1.system_prompt_changed"
D1_FAST_MODE_TOGGLE     = "D1.fast_mode_toggle"
D1_TTL_EXPIRED          = "D1.ttl_expired"
D1_IMAGE_EVICTION       = "D1.image_eviction"
D1_UNCLASSIFIED         = "D1.unclassified"

D2_COMPACTION           = "D2.compaction"
D2_TOOL_RESULT_CLEARING = "D2.tool_result_clearing"

D3_REREAD               = "D3.reread"
D3_BLOCKED_PATH         = "D3.blocked_path"
D3_FULL_READ_LARGE      = "D3.full_read_large"
D3_UNBOUNDED_OUTPUT     = "D3.unbounded_output"

D4_CLAUDEMD_BLOAT       = "D4.claudemd_bloat"
D4_SKILL_LISTING        = "D4.skill_listing"
D4_MCP_UNUSED           = "D4.mcp_unused"
D4_RULES_UNSCOPED       = "D4.rules_unscoped"

D5_MAIN_CONTEXT_EXPLORATION = "D5.main_context_exploration"

D1_ALL: list[str] = [
    D1_MODEL_SWITCH, D1_EFFORT_CHANGE, D1_TOOLS_CHANGED,
    D1_SYSTEM_PROMPT_CHANGED, D1_FAST_MODE_TOGGLE,
    D1_TTL_EXPIRED, D1_IMAGE_EVICTION, D1_UNCLASSIFIED,
]

FIX_HINTS: dict[str, str] = {
    D1_MODEL_SWITCH:          "pin model at session start",
    D1_EFFORT_CHANGE:         "avoid /effort mid-session",
    D1_TOOLS_CHANGED:         "start MCP servers at launch",
    D1_SYSTEM_PROMPT_CHANGED: "restart after Claude Code upgrade",
    D1_FAST_MODE_TOGGLE:      "avoid toggling fast mode mid-session",
    D1_TTL_EXPIRED:           "/compact before stepping away",
    D1_IMAGE_EVICTION:        "reduce image count per session",
    D1_UNCLASSIFIED:          "upgrade Claude Code for cause attribution",
    D2_COMPACTION:            "expected — no action needed",
    D2_TOOL_RESULT_CLEARING:  "expected — no action needed",
}
```

- [ ] **Step 6: Run taxonomy test to verify it passes**

```
pytest tests/test_taxonomy.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 7: Write the three JSON schemas**

`schema/ccgate.config.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "hitRatioFloor":              {"type": "number",  "minimum": 0, "maximum": 1,    "default": 0.85},
    "minRequestsForRatio":        {"type": "integer", "minimum": 1,                  "default": 10},
    "coldRecacheWarnTokens":      {"type": "integer", "minimum": 0,                  "default": 100000},
    "compactAdviseAt":            {"type": "number",  "minimum": 0, "maximum": 1,    "default": 0.80},
    "unboundedOutputTokens":      {"type": "integer", "minimum": 0,                  "default": 10000},
    "maxNoticesPerSession":       {"type": "integer", "minimum": 0,                  "default": 4},
    "bigFileLines":               {"type": "integer", "minimum": 0,                  "default": 500},
    "staleTimeMs":                {"type": "integer", "minimum": 0,                  "default": 600000},
    "staleFiles":                 {"type": "integer", "minimum": 0,                  "default": 8},
    "staleTokenRatio":            {"type": "number",  "minimum": 0, "maximum": 1,    "default": 0.10},
    "readCacheEnabled":           {"type": "boolean",                                "default": false},
    "skillListingBudgetFraction": {"type": "number",  "minimum": 0, "maximum": 1,    "default": 0.01},
    "bashRewriteRules": {
      "type": "array",
      "default": [],
      "items": {
        "type": "object",
        "required": ["prefix", "filter"],
        "additionalProperties": false,
        "properties": {
          "prefix": {"type": "string", "minLength": 1},
          "filter": {"type": "string", "minLength": 1}
        }
      }
    }
  }
}
```

`schema/ccgate.session.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["session_id", "requests"],
  "properties": {
    "session_id":      {"type": "string"},
    "transcript_path": {"type": "string"},
    "requests": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["index", "classification"],
        "properties": {
          "index":          {"type": "integer"},
          "timestamp":      {"type": "string"},
          "model_id":       {"type": "string"},
          "classification": {"type": "string", "enum": ["HIT", "MISS", "EXPECTED_REBUILD"]},
          "miss_cause":     {"type": ["string", "null"]},
          "re_processed":   {"type": "integer"},
          "miss_cost_usd":  {"type": ["number", "null"]}
        }
      }
    }
  }
}
```

`schema/ccgate.report.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["sessions", "summary", "misses", "assumptions"],
  "properties": {
    "sessions":   {"type": "array", "items": {"type": "string"}},
    "date_range": {
      "type": "object",
      "properties": {
        "start": {"type": "string"},
        "end":   {"type": "string"}
      }
    },
    "summary": {
      "type": "object",
      "required": ["total_requests", "total_misses", "expected_rebuilds", "hit_ratio", "avoidable_usd", "total_usd"],
      "properties": {
        "total_requests":    {"type": "integer"},
        "total_misses":      {"type": "integer"},
        "expected_rebuilds": {"type": "integer"},
        "hit_ratio":         {"type": "number"},
        "avoidable_usd":     {"type": "number"},
        "total_usd":         {"type": "number"}
      }
    },
    "misses": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["cause", "count", "recached_tokens", "cost_usd", "fix"],
        "properties": {
          "cause":           {"type": "string"},
          "count":           {"type": "integer"},
          "recached_tokens": {"type": "integer"},
          "cost_usd":        {"type": ["number", "null"]},
          "fix":             {"type": "string"}
        }
      }
    },
    "assumptions": {
      "type": "object",
      "required": ["turns_remaining_est", "turns_remaining_derivation"],
      "properties": {
        "turns_remaining_est":        {"type": "number"},
        "turns_remaining_derivation": {"type": "string"}
      }
    }
  }
}
```

- [ ] **Step 8: Write `src/ccgate/config.py`**

```python
import json
import os
from pathlib import Path

# Schema-derived defaults — single source of truth.
DEFAULTS: dict = {
    "hitRatioFloor": 0.85,
    "minRequestsForRatio": 10,
    "coldRecacheWarnTokens": 100_000,
    "compactAdviseAt": 0.80,
    "unboundedOutputTokens": 10_000,
    "maxNoticesPerSession": 4,
    "bigFileLines": 500,
    "staleTimeMs": 600_000,
    "staleFiles": 8,
    "staleTokenRatio": 0.10,
    "readCacheEnabled": False,
    "skillListingBudgetFraction": 0.01,
    "bashRewriteRules": [],
}

_RANGE: dict[str, tuple] = {
    "hitRatioFloor":              (0.0, 1.0),
    "minRequestsForRatio":        (1, 10_000),
    "coldRecacheWarnTokens":      (0, 10_000_000),
    "compactAdviseAt":            (0.0, 1.0),
    "unboundedOutputTokens":      (0, 10_000_000),
    "maxNoticesPerSession":       (0, 1_000),
    "bigFileLines":               (0, 1_000_000),
    "staleTimeMs":                (0, 86_400_000),
    "staleFiles":                 (0, 10_000),
    "staleTokenRatio":            (0.0, 1.0),
    "skillListingBudgetFraction": (0.0, 1.0),
}

def load_config(cwd: str | None = None) -> dict:
    """Load merged config: global ~/.ccgate/config.json + optional project .ccgate/config.json.

    Unknown keys are ignored and a warning is printed to stderr.
    Out-of-range scalar values fall back to their default.
    Arrays (bashRewriteRules) are merged (project appends to global).
    """
    cfg = dict(DEFAULTS)
    _merge_file(cfg, Path.home() / ".ccgate" / "config.json")
    if cwd:
        _merge_file(cfg, Path(cwd) / ".ccgate" / "config.json")
    # Environment overrides: CCGATE_HIT_RATIO_FLOOR → hitRatioFloor
    for key in list(DEFAULTS.keys()):
        env_key = "CCGATE_" + _to_upper_snake(key)
        val = os.environ.get(env_key)
        if val is not None:
            try:
                cfg[key] = type(DEFAULTS[key])(val)
            except (ValueError, TypeError):
                pass
    return cfg

def _merge_file(cfg: dict, path: Path) -> None:
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    import sys
    for key, val in raw.items():
        if key not in DEFAULTS:
            print(f"ccgate: unknown config key '{key}' in {path} — ignored", file=sys.stderr)
            continue
        if key == "bashRewriteRules" and isinstance(val, list):
            cfg[key] = cfg[key] + val
            continue
        lo, hi = _RANGE.get(key, (None, None))
        if lo is not None and not (lo <= val <= hi):
            print(f"ccgate: '{key}' value {val!r} out of range [{lo}, {hi}] — using default", file=sys.stderr)
            continue
        cfg[key] = val

def _to_upper_snake(camel: str) -> str:
    import re
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).upper()
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml src/ schema/ tests/__init__.py tests/test_taxonomy.py
git commit -m "feat: scaffold taxonomy, config loader, and JSON schemas"
```

---

### Task 2: `model.py` — pricing table and TTL resolution

**Files:**
- Create: `src/ccgate/model.py`
- Create: `tests/test_model.py`

**Interfaces:**
- Produces: `get_model_spec(model_id: str) -> ModelSpec`; `resolve_ttl(payload: dict) -> int` — consumed by `miss_audit.py` and `statusline.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model.py
from ccgate.model import get_model_spec, resolve_ttl, ModelSpec

def test_known_model_returns_spec():
    spec = get_model_spec("claude-sonnet-5")
    assert isinstance(spec, ModelSpec)
    assert spec.rate_in > 0
    assert spec.write_multiplier > 1.0
    assert spec.read_multiplier < 1.0
    assert spec.window_tokens > 0

def test_unknown_model_returns_fallback():
    spec = get_model_spec("claude-future-99")
    assert isinstance(spec, ModelSpec)
    assert spec.rate_in > 0

def test_partial_prefix_match():
    # "claude-sonnet-5-20260101" should match "claude-sonnet-5"
    spec = get_model_spec("claude-sonnet-5-20260101")
    assert spec == get_model_spec("claude-sonnet-5")

def test_resolve_ttl_from_explicit_field():
    payload = {"prompt_cache": {"ttl": 3600}}
    assert resolve_ttl(payload) == 3600

def test_resolve_ttl_missing_returns_conservative():
    assert resolve_ttl({}) == 300
    assert resolve_ttl({"prompt_cache": {}}) == 300

def test_resolve_ttl_null_prompt_cache():
    assert resolve_ttl({"prompt_cache": None}) == 300
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_model.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ccgate/model.py`**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ModelSpec:
    rate_in: float          # USD per input token
    write_multiplier: float # cache write cost as multiple of rate_in
    read_multiplier: float  # cache read cost as multiple of rate_in
    window_tokens: int      # context window in tokens

# Hand-maintained pricing table. Last updated: 2026-09-19.
# Source: console.anthropic.com/settings/billing
# Add new models here when Anthropic publishes them.
PRICING: dict[str, ModelSpec] = {
    "claude-opus-5":             ModelSpec(15e-6,  1.25, 0.10, 200_000),
    "claude-sonnet-5":           ModelSpec(3e-6,   1.25, 0.10, 200_000),
    "claude-fable-5-1":          ModelSpec(3e-6,   1.25, 0.10, 200_000),
    "claude-haiku-4-5-20251001": ModelSpec(0.8e-6, 1.25, 0.10, 200_000),
    "claude-opus-4":             ModelSpec(15e-6,  1.25, 0.10, 200_000),
    "claude-sonnet-4":           ModelSpec(3e-6,   1.25, 0.10, 200_000),
    "claude-haiku-4":            ModelSpec(0.8e-6, 1.25, 0.10, 200_000),
}

_FALLBACK = ModelSpec(3e-6, 1.25, 0.10, 200_000)

DEFAULT_WINDOW_TOKENS: int = 200_000  # used by shape.py for G3 budget calculation

def get_model_spec(model_id: str) -> ModelSpec:
    """Return ModelSpec for model_id. Falls back to Sonnet-class defaults for unknowns."""
    if model_id in PRICING:
        return PRICING[model_id]
    # Prefix match for versioned variants (e.g. "claude-sonnet-5-20260101")
    for key, spec in PRICING.items():
        if model_id.startswith(key):
            return spec
    return _FALLBACK

def resolve_ttl(payload: dict) -> int:
    """Return session cache TTL in seconds from a status-line payload.

    Reads prompt_cache.ttl (explicit override from Claude Code settings).
    Falls back to 300 — the conservative API-key/cloud default.
    TTL inference from transcript ephemeral_*_input_tokens is done in transcript.py.
    """
    pc = payload.get("prompt_cache") or {}
    ttl = pc.get("ttl")
    if isinstance(ttl, (int, float)) and ttl > 0:
        return int(ttl)
    return 300
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_model.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/model.py tests/test_model.py
git commit -m "feat: add model pricing table and TTL resolver"
```

---

### Task 3: `transcript.py` path encoding + G10 gate

This task implements only the cwd encoding function and the G10 gate script. Task 4 adds JSONL reading.

**Files:**
- Create: `src/ccgate/transcript.py` (encoding portion only)
- Create: `tests/test_transcript.py` (encoding tests only)
- Create: `tests/paths.py` (G10 gate — exits 0/1)

**Interfaces:**
- Produces: `encode_cwd(cwd: str) -> str` consumed by `find_transcripts()` (Task 4) and tested by `tests/paths.py`.

- [ ] **Step 1: Write the failing encoding test**

```python
# tests/test_transcript.py
from ccgate.transcript import encode_cwd

class TestEncodeCwd:
    def test_windows_drive_colon_becomes_two_hyphens(self):
        # "C:\" → "c--" (drive lowercased, colon → hyphen, backslash → hyphen)
        result = encode_cwd("C:\\Users\\fred\\project")
        assert result.startswith("c--Users-fred-project")

    def test_windows_dot_in_username(self):
        result = encode_cwd("C:\\Users\\developer\\CCGate")
        assert result == "c--Users-developer-CCGate"

    def test_windows_space_in_path(self):
        result = encode_cwd("C:\\Users\\fred\\OneDrive - Corp\\Docs")
        assert result == "c--Users-fred-OneDrive---Corp-Docs"

    def test_posix_path(self):
        result = encode_cwd("/home/user/projects/ccgate")
        assert result == "-home-user-projects-ccgate"

    def test_known_real_path(self):
        # From the project's own session path in the system prompt
        result = encode_cwd("C:\\Users\\developer\\OneDrive - Corp\\Documents\\CCGate")
        assert result == "c--Users-developer-OneDrive---Corp-Documents-CCGate"

    def test_no_trailing_content_lost(self):
        a = encode_cwd("/a/b/c")
        b = encode_cwd("/a/b/cd")
        assert a != b
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_transcript.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the encoding function in `src/ccgate/transcript.py`**

```python
import re

def encode_cwd(cwd: str) -> str:
    """Encode a working directory path to the Claude Code transcript directory name.

    Rules (matching Claude Code's own encoder):
    - On Windows paths (second char is ':'): lowercase the drive letter.
    - Replace each of ':', '\\', '/', '.', ' ' with '-'.
    """
    path = cwd
    if len(path) >= 2 and path[1] == ":":
        path = path[0].lower() + path[1:]
    return re.sub(r"[:\\/. ]", "-", path)
```

- [ ] **Step 4: Run encoding tests**

```
pytest tests/test_transcript.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Write `tests/paths.py` (G10 gate)**

```python
#!/usr/bin/env python3
"""G10 — Cross-platform path encoding round-trip gate. Exits 0 on pass, 1 on fail."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.transcript import encode_cwd

FIXTURES: list[tuple[str, str]] = [
    # (input cwd, expected encoded name)
    ("C:\\Users\\fred\\project",                       "c--Users-fred-project"),
    ("C:\\Users\\developer\\CCGate",            "c--Users-developer-CCGate"),
    ("C:\\Users\\fred\\OneDrive - Corp\\Docs",    "c--Users-fred-OneDrive---Corp-Docs"),
    ("/home/user/projects/ccgate",                     "-home-user-projects-ccgate"),
    ("/Users/alice/work/my.project",                   "-Users-alice-work-my-project"),
]

failures = []
for cwd, expected in FIXTURES:
    got = encode_cwd(cwd)
    if got != expected:
        failures.append(f"  encode_cwd({cwd!r})\n    got:      {got!r}\n    expected: {expected!r}")

if failures:
    print("G10 FAIL — path encoding mismatches:")
    print("\n".join(failures))
    sys.exit(1)

print(f"G10 PASS — {len(FIXTURES)} path fixtures verified")
sys.exit(0)
```

- [ ] **Step 6: Run G10 gate**

```
python tests/paths.py
```
Expected: `G10 PASS — 5 path fixtures verified`.

- [ ] **Step 7: Commit**

```bash
git add src/ccgate/transcript.py tests/test_transcript.py tests/paths.py
git commit -m "feat: implement cwd encoding and G10 gate"
```

---

### Task 4: `transcript.py` — JSONL reading and band detection

**Files:**
- Modify: `src/ccgate/transcript.py` (add reading functions)
- Modify: `tests/test_transcript.py` (add reading tests — append to existing file)

**Interfaces:**
- Produces: `CapabilityBand`, `detect_band(payload: dict) -> CapabilityBand`, `Usage`, `Request`, `read_transcript(path: Path) -> list[Request]`, `find_transcripts(...)`, `infer_ttl_from_usage(requests: list[Request]) -> int` — all consumed by `miss_audit.py` and `statusline.py`.

- [ ] **Step 1: Append failing tests to `tests/test_transcript.py`**

```python
# Append to tests/test_transcript.py
import json
from pathlib import Path
import tempfile
from ccgate.transcript import (
    CapabilityBand, detect_band, read_transcript, infer_ttl_from_usage
)

class TestDetectBand:
    def test_band1_no_prompt_cache(self):
        assert detect_band({}) == CapabilityBand.BAND1_PRE_CACHE
        assert detect_band({"model": {"id": "claude-sonnet-5"}}) == CapabilityBand.BAND1_PRE_CACHE

    def test_band2_prompt_cache_no_miss_causes(self):
        payload = {"prompt_cache": {"warm": True, "hit_ratio": 0.9}}
        assert detect_band(payload) == CapabilityBand.BAND2_CACHE_NO_CAUSES

    def test_band3_full(self):
        payload = {"prompt_cache": {"warm": True, "miss_causes": {}}}
        assert detect_band(payload) == CapabilityBand.BAND3_FULL

    def test_band3_miss_causes_empty_dict_counts(self):
        # Empty dict is still "present" — Band 3
        payload = {"prompt_cache": {"miss_causes": {}}}
        assert detect_band(payload) == CapabilityBand.BAND3_FULL

class TestReadTranscript:
    def _write_jsonl(self, entries: list[dict]) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                        delete=False, encoding="utf-8")
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.close()
        return Path(f.name)

    def _assistant(self, model: str, inp: int, read: int, create: int,
                   out: int = 5, h1: int = 0, h5: int = 0,
                   ts: str = "2026-09-19T10:00:00.000Z") -> dict:
        return {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": inp,
                    "cache_read_input_tokens": read,
                    "cache_creation_input_tokens": create,
                    "output_tokens": out,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": h1,
                        "ephemeral_5m_input_tokens": h5,
                    },
                },
            },
        }

    def _user(self, text: str = "hello") -> dict:
        return {"type": "user", "message": {"role": "user", "content": text}}

    def test_reads_assistant_entries_only(self):
        path = self._write_jsonl([
            self._user("hi"),
            self._assistant("claude-sonnet-5", 1000, 0, 1000),
            self._user("next"),
            self._assistant("claude-sonnet-5", 1200, 1000, 200),
        ])
        reqs = read_transcript(path)
        assert len(reqs) == 2
        assert reqs[0].index == 0
        assert reqs[1].index == 1

    def test_usage_fields_parsed(self):
        path = self._write_jsonl([
            self._assistant("claude-opus-5", 5000, 4500, 500, h1=500),
        ])
        reqs = read_transcript(path)
        u = reqs[0].usage
        assert u.input_tokens == 5000
        assert u.cache_read_input_tokens == 4500
        assert u.cache_creation_input_tokens == 500
        assert u.ephemeral_1h_input_tokens == 500

    def test_model_id_extracted(self):
        path = self._write_jsonl([
            self._assistant("claude-opus-5", 1000, 0, 1000),
        ])
        reqs = read_transcript(path)
        assert reqs[0].model_id == "claude-opus-5"

    def test_skips_malformed_lines(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                        delete=False, encoding="utf-8")
        f.write("not json\n")
        f.write(json.dumps(self._assistant("claude-sonnet-5", 100, 0, 100)) + "\n")
        f.close()
        reqs = read_transcript(Path(f.name))
        assert len(reqs) == 1

class TestInferTtl:
    def _req(self, h1=0, h5=0):
        from ccgate.transcript import Request, Usage
        return Request(0, "", "claude-sonnet-5", Usage(ephemeral_1h_input_tokens=h1,
                                                       ephemeral_5m_input_tokens=h5))

    def test_1h_tokens_means_3600(self):
        assert infer_ttl_from_usage([self._req(h1=1000)]) == 3600

    def test_5m_tokens_means_300(self):
        assert infer_ttl_from_usage([self._req(h5=1000)]) == 300

    def test_no_tokens_defaults_to_300(self):
        assert infer_ttl_from_usage([self._req()]) == 300
```

- [ ] **Step 2: Run to verify new tests fail**

```
pytest tests/test_transcript.py::TestDetectBand tests/test_transcript.py::TestReadTranscript -v
```
Expected: `ImportError` (classes not defined yet).

- [ ] **Step 3: Append reading code to `src/ccgate/transcript.py`**

```python
# Append to src/ccgate/transcript.py (after encode_cwd)
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CapabilityBand(Enum):
    BAND1_PRE_CACHE        = 1
    BAND2_CACHE_NO_CAUSES  = 2
    BAND3_FULL             = 3


def detect_band(payload: dict) -> CapabilityBand:
    """Detect version capability band from a status-line payload (§4.1 discriminator rule)."""
    pc = payload.get("prompt_cache")
    if pc is None:
        return CapabilityBand.BAND1_PRE_CACHE
    if pc.get("miss_causes") is None:
        return CapabilityBand.BAND2_CACHE_NO_CAUSES
    return CapabilityBand.BAND3_FULL


@dataclass
class Usage:
    input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0
    ephemeral_1h_input_tokens: int = 0
    ephemeral_5m_input_tokens: int = 0


@dataclass
class Request:
    index: int
    timestamp: str
    model_id: str
    usage: Usage
    is_expected_rebuild: bool = False


def _parse_usage(msg: dict) -> Usage:
    u = msg.get("usage") or {}
    cc = u.get("cache_creation") or {}
    return Usage(
        input_tokens=                u.get("input_tokens")                or 0,
        cache_read_input_tokens=     u.get("cache_read_input_tokens")     or 0,
        cache_creation_input_tokens= u.get("cache_creation_input_tokens") or 0,
        output_tokens=               u.get("output_tokens")               or 0,
        ephemeral_1h_input_tokens=   cc.get("ephemeral_1h_input_tokens")  or 0,
        ephemeral_5m_input_tokens=   cc.get("ephemeral_5m_input_tokens")  or 0,
    )


def infer_ttl_from_usage(requests: list["Request"]) -> int:
    """Infer session TTL from transcript usage fields (§4.2).

    ephemeral_5m_input_tokens > 0  →  300 s (API-key / cloud)
    ephemeral_1h_input_tokens > 0  →  3600 s (subscription)
    neither                        →  300 s (conservative default)
    """
    for r in requests:
        if r.usage.ephemeral_5m_input_tokens > 0:
            return 300
        if r.usage.ephemeral_1h_input_tokens > 0:
            return 3600
    return 300


def read_transcript(path: Path) -> list["Request"]:
    """Parse a Claude Code JSONL transcript; return only assistant Request entries."""
    requests: list[Request] = []
    with path.open(encoding="utf-8") as fh:
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
            msg = entry.get("message") or {}
            requests.append(Request(
                index=len(requests),
                timestamp=entry.get("timestamp", ""),
                model_id=msg.get("model", "unknown"),
                usage=_parse_usage(msg),
            ))
    return requests


def find_transcripts(
    session_id: str | None = None,
    cwd: str | None = None,
) -> list[Path]:
    """Return JSONL paths for a session, all sessions in a cwd, or all sessions."""
    base = Path.home() / ".claude" / "projects"
    if cwd is not None:
        project_dir = base / encode_cwd(cwd)
        if not project_dir.exists():
            return []
        if session_id:
            p = project_dir / f"{session_id}.jsonl"
            return [p] if p.exists() else []
        return sorted(project_dir.glob("*.jsonl"))
    return sorted(base.rglob("*.jsonl"))
```

- [ ] **Step 4: Run all transcript tests**

```
pytest tests/test_transcript.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/transcript.py tests/test_transcript.py
git commit -m "feat: add JSONL reading, band detection, TTL inference"
```

---

### Task 5: Synthesised test fixtures

**Files:**
- Create: `tests/fixtures/transcripts/d1_model_switch.jsonl`
- Create: `tests/fixtures/transcripts/d1_ttl_expired.jsonl`
- Create: `tests/fixtures/transcripts/d2_compaction.jsonl`
- Create: `tests/fixtures/transcripts/clean_15req.jsonl`
- Create: `tests/fixtures/statusline/band1_payload.json`
- Create: `tests/fixtures/statusline/band2_payload.json`
- Create: `tests/fixtures/statusline/band3_payload.json`

**Interfaces:**
- Produces: fixture files consumed by `test_miss_audit.py`, `tests/determinism.py` (G8), `tests/schema_check.py` (G9), `tests/degrade.py` (G11).

All timestamps are fixed ISO strings — no wall-clock values — to satisfy G8 (byte-identical output).

- [ ] **Step 1: Write `tests/fixtures/transcripts/d1_model_switch.jsonl`**

Turn 1 (sonnet-5, creates 5000 cache tokens). Turn 2 (switches to opus-5, misses — re-creates 5200 tokens).

```jsonl
{"type":"user","message":{"role":"user","content":"task 1"},"uuid":"u1","timestamp":"2026-09-19T10:00:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":5000,"cache_read_input_tokens":0,"cache_creation_input_tokens":5000,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":5000,"ephemeral_5m_input_tokens":0}}},"uuid":"a1","timestamp":"2026-09-19T10:00:05.000Z"}
{"type":"user","message":{"role":"user","content":"task 2"},"uuid":"u2","timestamp":"2026-09-19T10:01:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":5200,"cache_read_input_tokens":0,"cache_creation_input_tokens":5200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":5200,"ephemeral_5m_input_tokens":0}}},"uuid":"a2","timestamp":"2026-09-19T10:01:05.000Z"}
```

- [ ] **Step 2: Write `tests/fixtures/transcripts/d1_ttl_expired.jsonl`**

Turn 1 (5m TTL session, creates 4000 tokens). Turn 2 is 10 minutes later → TTL expired → miss.

```jsonl
{"type":"user","message":{"role":"user","content":"start"},"uuid":"u1","timestamp":"2026-09-19T10:00:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":4000,"cache_read_input_tokens":0,"cache_creation_input_tokens":4000,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":0,"ephemeral_5m_input_tokens":4000}}},"uuid":"a1","timestamp":"2026-09-19T10:00:05.000Z"}
{"type":"user","message":{"role":"user","content":"continue after break"},"uuid":"u2","timestamp":"2026-09-19T10:10:30.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":4100,"cache_read_input_tokens":0,"cache_creation_input_tokens":4100,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":0,"ephemeral_5m_input_tokens":4100}}},"uuid":"a2","timestamp":"2026-09-19T10:10:35.000Z"}
```

- [ ] **Step 3: Write `tests/fixtures/transcripts/d2_compaction.jsonl`**

Turn 1 normal. Turn 2 user sends `/compact`. Turn 3 is a small request (expected rebuild after compaction).

```jsonl
{"type":"user","message":{"role":"user","content":"do work"},"uuid":"u1","timestamp":"2026-09-19T10:00:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":8000,"cache_read_input_tokens":0,"cache_creation_input_tokens":8000,"output_tokens":10,"cache_creation":{"ephemeral_1h_input_tokens":8000,"ephemeral_5m_input_tokens":0}}},"uuid":"a1","timestamp":"2026-09-19T10:00:05.000Z"}
{"type":"user","message":{"role":"user","content":"/compact"},"uuid":"u2","timestamp":"2026-09-19T10:01:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"compacted"}],"usage":{"input_tokens":1200,"cache_read_input_tokens":0,"cache_creation_input_tokens":1200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":1200,"ephemeral_5m_input_tokens":0}}},"uuid":"a2","timestamp":"2026-09-19T10:01:05.000Z"}
{"type":"user","message":{"role":"user","content":"continue"},"uuid":"u3","timestamp":"2026-09-19T10:02:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":1400,"cache_read_input_tokens":1200,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a3","timestamp":"2026-09-19T10:02:05.000Z"}
```

- [ ] **Step 4: Write `tests/fixtures/transcripts/clean_15req.jsonl`**

15 turns, same model (claude-sonnet-5), cache grows cleanly each turn. No misses, no model switches.
Each turn: `cache_read = prev_cache_creation`, `cache_creation = 200` (new content per turn).

```jsonl
{"type":"user","message":{"role":"user","content":"turn 1"},"uuid":"u1","timestamp":"2026-09-19T10:00:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":5000,"cache_read_input_tokens":0,"cache_creation_input_tokens":5000,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":5000,"ephemeral_5m_input_tokens":0}}},"uuid":"a1","timestamp":"2026-09-19T10:00:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 2"},"uuid":"u2","timestamp":"2026-09-19T10:01:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":5200,"cache_read_input_tokens":5000,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a2","timestamp":"2026-09-19T10:01:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 3"},"uuid":"u3","timestamp":"2026-09-19T10:02:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":5400,"cache_read_input_tokens":5200,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a3","timestamp":"2026-09-19T10:02:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 4"},"uuid":"u4","timestamp":"2026-09-19T10:03:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":5600,"cache_read_input_tokens":5400,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a4","timestamp":"2026-09-19T10:03:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 5"},"uuid":"u5","timestamp":"2026-09-19T10:04:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":5800,"cache_read_input_tokens":5600,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a5","timestamp":"2026-09-19T10:04:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 6"},"uuid":"u6","timestamp":"2026-09-19T10:05:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":6000,"cache_read_input_tokens":5800,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a6","timestamp":"2026-09-19T10:05:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 7"},"uuid":"u7","timestamp":"2026-09-19T10:06:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":6200,"cache_read_input_tokens":6000,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a7","timestamp":"2026-09-19T10:06:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 8"},"uuid":"u8","timestamp":"2026-09-19T10:07:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":6400,"cache_read_input_tokens":6200,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a8","timestamp":"2026-09-19T10:07:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 9"},"uuid":"u9","timestamp":"2026-09-19T10:08:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":6600,"cache_read_input_tokens":6400,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a9","timestamp":"2026-09-19T10:08:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 10"},"uuid":"u10","timestamp":"2026-09-19T10:09:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":6800,"cache_read_input_tokens":6600,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a10","timestamp":"2026-09-19T10:09:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 11"},"uuid":"u11","timestamp":"2026-09-19T10:10:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":7000,"cache_read_input_tokens":6800,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a11","timestamp":"2026-09-19T10:10:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 12"},"uuid":"u12","timestamp":"2026-09-19T10:11:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":7200,"cache_read_input_tokens":7000,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a12","timestamp":"2026-09-19T10:11:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 13"},"uuid":"u13","timestamp":"2026-09-19T10:12:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":7400,"cache_read_input_tokens":7200,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a13","timestamp":"2026-09-19T10:12:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 14"},"uuid":"u14","timestamp":"2026-09-19T10:13:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":7600,"cache_read_input_tokens":7400,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a14","timestamp":"2026-09-19T10:13:05.000Z"}
{"type":"user","message":{"role":"user","content":"turn 15"},"uuid":"u15","timestamp":"2026-09-19T10:14:00.000Z"}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":7800,"cache_read_input_tokens":7600,"cache_creation_input_tokens":200,"output_tokens":5,"cache_creation":{"ephemeral_1h_input_tokens":200,"ephemeral_5m_input_tokens":0}}},"uuid":"a15","timestamp":"2026-09-19T10:14:05.000Z"}
```

- [ ] **Step 5: Write statusline fixture payloads**

`tests/fixtures/statusline/band1_payload.json` (no `prompt_cache`):
```json
{
  "model": {"id": "claude-sonnet-5"},
  "context_window": {"used_percentage": 52, "context_window_size": 200000,
    "current_usage": {"input_tokens": 104000, "output_tokens": 1200,
      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}},
  "cost": {"total_cost_usd": 1.24},
  "session_id": "band1-test"
}
```

`tests/fixtures/statusline/band2_payload.json` (`prompt_cache` present, no `miss_causes`):
```json
{
  "model": {"id": "claude-sonnet-5"},
  "context_window": {"used_percentage": 52, "context_window_size": 200000,
    "current_usage": {"input_tokens": 104000, "output_tokens": 1200,
      "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 95000}},
  "prompt_cache": {"warm": true, "caching_observed": true, "ttl": 3600,
    "requests": 8, "misses": 1, "hit_ratio": 0.875,
    "recache_tokens_if_cold": 95000},
  "cost": {"total_cost_usd": 0.48},
  "session_id": "band2-test"
}
```

`tests/fixtures/statusline/band3_payload.json` (full — `miss_causes` present):
```json
{
  "model": {"id": "claude-opus-5"},
  "effort": {"level": "normal"},
  "fast_mode": false,
  "context_window": {"used_percentage": 41, "context_window_size": 200000,
    "current_usage": {"input_tokens": 82000, "output_tokens": 800,
      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 75000}},
  "prompt_cache": {"warm": true, "caching_observed": true, "ttl": 3600,
    "requests": 12, "misses": 0, "hit_ratio": 0.92,
    "miss_causes": {}, "recache_tokens_if_cold": 75000},
  "cost": {"total_cost_usd": 2.10},
  "session_id": "band3-test"
}
```

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/
git commit -m "feat: add synthesised JSONL and statusline fixtures"
```

---

### Task 6: `miss_audit.py` — core algorithm + tests

**Files:**
- Create: `src/ccgate/scripts/miss_audit.py`
- Create: `tests/test_miss_audit.py`

**Interfaces:**
- Consumes: `transcript.read_transcript`, `transcript.infer_ttl_from_usage`, `model.get_model_spec`, `taxonomy.*`, `config.load_config`.
- Produces: `run_audit(paths: list[Path], config: dict) -> dict` returning a JSON-serialisable report dict conforming to `ccgate.report.schema.json`. Entry point: `main()` callable from `dispatch.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_miss_audit.py
from pathlib import Path
from ccgate.scripts.miss_audit import run_audit, classify_requests, attribute_miss
from ccgate.transcript import Request, Usage
from ccgate.config import DEFAULTS
from ccgate import taxonomy

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"

def _req(idx, model, inp, read, create, h1=0, h5=0, ts="2026-09-19T10:00:00.000Z"):
    return Request(idx, ts, model, Usage(
        input_tokens=inp,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=create,
        ephemeral_1h_input_tokens=h1,
        ephemeral_5m_input_tokens=h5,
    ))

class TestClassifyRequests:
    def test_first_turn_is_hit(self):
        reqs = [_req(0, "claude-sonnet-5", 5000, 0, 5000, h1=5000)]
        result = classify_requests(reqs)
        assert result[0][1].value == "HIT"

    def test_large_miss_detected(self):
        reqs = [
            _req(0, "claude-sonnet-5", 5000, 0, 5000, h1=5000),
            _req(1, "claude-sonnet-5", 5000, 0, 5000, h1=5000),  # re-creates all 5000
        ]
        result = classify_requests(reqs)
        assert result[1][1].value == "MISS"

    def test_small_miss_below_threshold_is_hit(self):
        reqs = [
            _req(0, "claude-sonnet-5", 5000, 0, 5000, h1=5000),
            # reads 4900 of 5000 — only 100 tokens missed (< 5% AND < 2000)
            _req(1, "claude-sonnet-5", 5200, 4900, 300, h1=300),
        ]
        result = classify_requests(reqs)
        assert result[1][1].value == "HIT"

    def test_expected_rebuild_not_counted_as_miss(self):
        req = _req(0, "claude-sonnet-5", 1200, 0, 1200, h1=1200)
        req.is_expected_rebuild = True
        result = classify_requests([req])
        assert result[0][1].value == "EXPECTED_REBUILD"

class TestAttributeMiss:
    def test_model_switch_detected(self):
        prev = _req(0, "claude-sonnet-5", 5000, 0, 5000)
        curr = _req(1, "claude-opus-5",   5000, 0, 5000)
        assert attribute_miss(curr, prev, ttl=3600) == taxonomy.D1_MODEL_SWITCH

    def test_same_model_returns_unclassified(self):
        prev = _req(0, "claude-sonnet-5", 5000, 0, 5000)
        curr = _req(1, "claude-sonnet-5", 5000, 0, 5000)
        cause = attribute_miss(curr, prev, ttl=3600)
        assert cause == taxonomy.D1_UNCLASSIFIED

    def test_ttl_expired_detected(self):
        # 10-minute gap, 5-minute TTL → expired
        prev = _req(0, "claude-sonnet-5", 5000, 0, 5000,
                    ts="2026-09-19T10:00:05.000Z")
        curr = _req(1, "claude-sonnet-5", 5000, 0, 5000,
                    ts="2026-09-19T10:10:35.000Z")
        assert attribute_miss(curr, prev, ttl=300) == taxonomy.D1_TTL_EXPIRED

class TestRunAudit:
    def test_model_switch_fixture(self):
        report = run_audit([FIXTURES / "d1_model_switch.jsonl"], DEFAULTS)
        causes = [m["cause"] for m in report["misses"] if m["cause"] != taxonomy.D2_COMPACTION]
        assert taxonomy.D1_MODEL_SWITCH in causes

    def test_clean_session_has_no_avoidable_misses(self):
        report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
        avoidable = [m for m in report["misses"] if m["cause"] in taxonomy.D1_ALL
                     and m["cause"] != taxonomy.D1_UNCLASSIFIED]
        assert sum(m["count"] for m in avoidable) == 0

    def test_report_schema_fields_present(self):
        report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
        assert "sessions" in report
        assert "summary" in report
        assert "misses" in report
        assert "assumptions" in report
        assert "turns_remaining_est" in report["assumptions"]
        assert "turns_remaining_derivation" in report["assumptions"]

    def test_d2_compaction_not_in_avoidable_total(self):
        report = run_audit([FIXTURES / "d2_compaction.jsonl"], DEFAULTS)
        # D2 count should be in misses list but avoidable_usd must not include it
        d2_entries = [m for m in report["misses"] if m["cause"] == taxonomy.D2_COMPACTION]
        # avoidable_usd counts only D1 codes
        d1_cost = sum(
            m["cost_usd"] or 0 for m in report["misses"]
            if m["cause"] in taxonomy.D1_ALL
        )
        assert abs(report["summary"]["avoidable_usd"] - d1_cost) < 1e-9
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/test_miss_audit.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ccgate/scripts/miss_audit.py`**

```python
"""miss_audit.py — cache miss attribution report (Phase 0).

Phase 0 limitation: cause attribution uses transcript-observable signals only
(model_id changes, timestamp gaps vs inferred TTL). Status-line miss_causes
snapshots are not available until Phase 1 hooks ship.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ccgate import taxonomy
from ccgate.model import get_model_spec
from ccgate.transcript import (
    Request, CapabilityBand, Classification,
    classify_requests, infer_ttl_from_usage, read_transcript,
)

# Re-export Classification so test imports from here work.
from ccgate.transcript import Classification  # noqa: F401


_MISS_FRACTION = 0.05
_MISS_MIN_TOKENS = 2000


def attribute_miss(curr: Request, prev: Request | None, ttl: int) -> str:
    """Return the most likely D1 code for a miss, using transcript-only signals."""
    if prev is not None and prev.model_id != curr.model_id:
        return taxonomy.D1_MODEL_SWITCH

    if prev is not None and curr.timestamp and prev.timestamp:
        try:
            t_curr = datetime.fromisoformat(curr.timestamp.replace("Z", "+00:00"))
            t_prev = datetime.fromisoformat(prev.timestamp.replace("Z", "+00:00"))
            gap_s = (t_curr - t_prev).total_seconds()
            if gap_s > ttl:
                return taxonomy.D1_TTL_EXPIRED
        except ValueError:
            pass

    return taxonomy.D1_UNCLASSIFIED


def _turns_remaining_est(requests: list[Request], index: int) -> tuple[float, str]:
    """Estimate remaining turns at request[index] using rolling median (§6 algorithm)."""
    if index < 3:
        return 5.0, f"bootstrap constant (turn {index + 1} of session)"
    gaps = [j - i for i, j in zip(range(index), range(1, index + 1))]
    median = sorted(gaps)[len(gaps) // 2]
    return float(median), f"rolling median of {len(gaps)} observed turn gaps"


def run_audit(paths: list[Path], config: dict) -> dict:
    """Run miss audit over a list of transcript paths; return report dict."""
    all_sessions: list[str] = []
    cause_counts: dict[str, int] = defaultdict(int)
    cause_tokens: dict[str, int] = defaultdict(int)
    cause_cost:   dict[str, float] = defaultdict(float)

    total_requests = 0
    total_misses = 0
    total_rebuilds = 0
    total_usd = 0.0
    avoidable_usd = 0.0

    turns_est, turns_derivation = 5.0, "bootstrap constant"

    for path in sorted(paths):
        all_sessions.append(str(path))
        requests = read_transcript(path)
        if not requests:
            continue

        ttl = infer_ttl_from_usage(requests)
        classified = classify_requests(requests)

        prev_req: Request | None = None
        expected_cache = 0

        for req, cls in classified:
            spec = get_model_spec(req.model_id)
            total_usd += (
                req.usage.cache_creation_input_tokens * spec.rate_in * spec.write_multiplier
                + req.usage.cache_read_input_tokens   * spec.rate_in * spec.read_multiplier
                + (req.usage.input_tokens - req.usage.cache_read_input_tokens
                   - req.usage.cache_creation_input_tokens) * spec.rate_in
            )

            if cls == Classification.EXPECTED_REBUILD:
                total_rebuilds += 1
                cause_counts[taxonomy.D2_COMPACTION] += 1
                re_processed = max(0, expected_cache - req.usage.cache_read_input_tokens)
                cause_tokens[taxonomy.D2_COMPACTION] += re_processed
                expected_cache = req.usage.cache_creation_input_tokens

            elif cls == Classification.MISS:
                total_misses += 1
                re_processed = max(0, expected_cache - req.usage.cache_read_input_tokens)
                cause = attribute_miss(req, prev_req, ttl)
                cause_counts[cause] += 1
                cause_tokens[cause] += re_processed
                miss_cost = (
                    re_processed * spec.rate_in * spec.write_multiplier
                    - re_processed * spec.rate_in * spec.read_multiplier
                )
                cause_cost[cause] += miss_cost
                if cause in taxonomy.D1_ALL:
                    avoidable_usd += miss_cost
                expected_cache = req.usage.cache_creation_input_tokens

            else:  # HIT
                expected_cache = (req.usage.cache_read_input_tokens
                                  + req.usage.cache_creation_input_tokens)

            turns_est, turns_derivation = _turns_remaining_est(requests, req.index)
            total_requests += 1
            prev_req = req

    hit_ratio = (
        (total_requests - total_misses) / total_requests if total_requests > 0 else 1.0
    )

    misses_list = []
    for cause in sorted(cause_counts.keys()):
        count = cause_counts[cause]
        tokens = cause_tokens[cause]
        cost = cause_cost.get(cause)
        misses_list.append({
            "cause":           cause,
            "count":           count,
            "recached_tokens": tokens,
            "cost_usd":        round(cost, 6) if cost else None,
            "fix":             taxonomy.FIX_HINTS.get(cause, ""),
        })
    # Sort by cost descending (D1 first), then by cause name for determinism.
    misses_list.sort(key=lambda m: (-(m["cost_usd"] or 0), m["cause"]))

    return {
        "sessions": all_sessions,
        "summary": {
            "total_requests":    total_requests,
            "total_misses":      total_misses,
            "expected_rebuilds": total_rebuilds,
            "hit_ratio":         round(hit_ratio, 6),
            "avoidable_usd":     round(avoidable_usd, 6),
            "total_usd":         round(total_usd, 6),
        },
        "misses": misses_list,
        "assumptions": {
            "turns_remaining_est":        turns_est,
            "turns_remaining_derivation": turns_derivation,
        },
    }


def _render_table(report: dict) -> str:
    lines = []
    s = report["summary"]
    session_count = len(report["sessions"])
    lines.append(f"\nMISS AUDIT — {session_count} session(s)\n")
    lines.append(f"  {'cause':<30} {'misses':>6}  {'re-cached':>10}  {'cost':>8}  fix")
    lines.append("  " + "-" * 70)
    for m in report["misses"]:
        tokens = m["recached_tokens"]
        tok_str = f"{tokens/1e6:.1f}M" if tokens >= 1e6 else f"{tokens/1e3:.0f}K"
        cost_str = f"${m['cost_usd']:.2f}" if m["cost_usd"] else "—"
        lines.append(
            f"  {m['cause']:<30} {m['count']:>6}  {tok_str:>10}  {cost_str:>8}  {m['fix']}"
        )
    lines.append("")
    lines.append(
        f"  avoidable: ${s['avoidable_usd']:.2f} of ${s['total_usd']:.2f} session spend"
        f" ({s['avoidable_usd']/s['total_usd']*100:.0f}%)"
        if s["total_usd"] > 0 else "  no spend recorded"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse
    from ccgate.config import load_config

    parser = argparse.ArgumentParser(prog="ccgate audit")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--session", metavar="ID")
    parser.add_argument("--since", metavar="DURATION")
    parser.add_argument("--json", action="store_true", dest="emit_json")
    parser.add_argument("--assert", action="store_true", dest="assert_mode",
                        help="Exit 1 if avoidable misses exist")
    args = parser.parse_args(argv)

    config = load_config()
    paths: list[Path] = list(args.paths)

    if not paths and args.session:
        from ccgate.transcript import find_transcripts
        paths = find_transcripts(session_id=args.session)
    if not paths:
        from ccgate.transcript import find_transcripts
        paths = find_transcripts()

    if not paths:
        print("ccgate audit: no transcripts found", file=sys.stderr)
        sys.exit(0)

    report = run_audit(paths, config)

    if args.emit_json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_table(report))

    if args.assert_mode and report["summary"]["avoidable_usd"] > 0:
        sys.exit(1)
```

**Important:** The `classify_requests` function needs to live in `transcript.py` so it is importable from there. Add it to `src/ccgate/transcript.py`:

```python
# Append to src/ccgate/transcript.py

from enum import Enum

class Classification(Enum):
    HIT              = "HIT"
    MISS             = "MISS"
    EXPECTED_REBUILD = "EXPECTED_REBUILD"

_MISS_FRACTION  = 0.05
_MISS_MIN_TOKENS = 2_000
_COMPACT_SIGNAL  = "/compact"
_CLEAR_SIGNAL    = "/clear"

def _is_compact_turn(path: Path, turn_index: int) -> bool:
    """Check if the user message preceding turn_index contained /compact or /clear."""
    user_turns: list[str] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") == "user":
                    content = e.get("message", {}).get("content", "")
                    user_turns.append(content if isinstance(content, str) else "")
                elif e.get("type") == "assistant":
                    if len(user_turns) > turn_index:
                        break
    except OSError:
        pass
    if turn_index < len(user_turns):
        msg = user_turns[turn_index]
        return _COMPACT_SIGNAL in msg or _CLEAR_SIGNAL in msg
    return False


def classify_requests(requests: list["Request"],
                      transcript_path: Path | None = None) -> list[tuple["Request", Classification]]:
    """Classify each assistant request as HIT, MISS, or EXPECTED_REBUILD.

    EXPECTED_REBUILD: the user sent /compact or /clear before this turn, OR
    req.is_expected_rebuild is set (from external signal, Phase 1+).
    MISS: re-processed > 5% of expected_cache AND > 2000 tokens.
    HIT: everything else.
    """
    results: list[tuple[Request, Classification]] = []
    expected_cache = 0

    for req in requests:
        # Check for expected rebuild via user message signal or explicit flag
        is_rebuild = req.is_expected_rebuild
        if not is_rebuild and transcript_path is not None:
            is_rebuild = _is_compact_turn(transcript_path, req.index)

        if is_rebuild:
            results.append((req, Classification.EXPECTED_REBUILD))
            expected_cache = req.usage.cache_creation_input_tokens
            continue

        if expected_cache == 0:
            results.append((req, Classification.HIT))
            expected_cache = (req.usage.cache_read_input_tokens
                              + req.usage.cache_creation_input_tokens)
            continue

        re_processed = max(0, expected_cache - req.usage.cache_read_input_tokens)
        is_miss = (
            re_processed > _MISS_FRACTION * expected_cache
            and re_processed >= _MISS_MIN_TOKENS
        )

        if is_miss:
            results.append((req, Classification.MISS))
            expected_cache = req.usage.cache_creation_input_tokens
        else:
            results.append((req, Classification.HIT))
            expected_cache = (req.usage.cache_read_input_tokens
                              + req.usage.cache_creation_input_tokens)

    return results
```

- [ ] **Step 4: Run miss_audit tests**

```
pytest tests/test_miss_audit.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/transcript.py src/ccgate/scripts/miss_audit.py tests/test_miss_audit.py
git commit -m "feat: implement miss classification, attribution, and audit report"
```

---

### Task 7: G8 (determinism) and G9 (schema conformance) gate scripts

**Files:**
- Create: `tests/determinism.py` (G8)
- Create: `tests/schema_check.py` (G9)

**Interfaces:**
- Consumes: `miss_audit.run_audit` and all three schema files.

- [ ] **Step 1: Write `tests/determinism.py`**

```python
#!/usr/bin/env python3
"""G8 — Determinism gate: same transcript set → byte-identical miss_audit output x3.
Exits 0 on pass, 1 on fail."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.scripts.miss_audit import run_audit
from ccgate.config import DEFAULTS

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
PATHS = sorted(FIXTURES.glob("*.jsonl"))

if not PATHS:
    print("G8 FAIL — no fixture transcripts found")
    sys.exit(1)

outputs = []
for _ in range(3):
    report = run_audit(PATHS, DEFAULTS)
    outputs.append(json.dumps(report, sort_keys=True))

if len(set(outputs)) != 1:
    print("G8 FAIL — miss_audit output is not deterministic across 3 runs")
    for i, o in enumerate(outputs):
        print(f"  run {i+1}: {o[:120]}...")
    sys.exit(1)

print(f"G8 PASS — output is byte-identical across 3 runs ({len(PATHS)} fixtures)")
sys.exit(0)
```

- [ ] **Step 2: Run G8 gate**

```
python tests/determinism.py
```
Expected: `G8 PASS`.

- [ ] **Step 3: Write `tests/schema_check.py`**

```python
#!/usr/bin/env python3
"""G9 — Schema conformance: all emitted JSON validates against schema/*.json.
Exits 0 on pass, 1 on fail.

Uses stdlib json only — no jsonschema package. Validates required keys and
types rather than full draft-07 compliance (sufficient for catching regressions).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.scripts.miss_audit import run_audit
from ccgate.config import DEFAULTS

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
SCHEMA_DIR = Path(__file__).parent.parent / "schema"

def _check_report(report: dict) -> list[str]:
    errors = []
    for key in ("sessions", "summary", "misses", "assumptions"):
        if key not in report:
            errors.append(f"report missing required key '{key}'")
    s = report.get("summary", {})
    for key in ("total_requests", "total_misses", "expected_rebuilds",
                "hit_ratio", "avoidable_usd", "total_usd"):
        if key not in s:
            errors.append(f"summary missing '{key}'")
    a = report.get("assumptions", {})
    for key in ("turns_remaining_est", "turns_remaining_derivation"):
        if key not in a:
            errors.append(f"assumptions missing '{key}'")
    for m in report.get("misses", []):
        for key in ("cause", "count", "recached_tokens", "cost_usd", "fix"):
            if key not in m:
                errors.append(f"miss entry missing '{key}'")
    return errors

paths = sorted(FIXTURES.glob("*.jsonl"))
if not paths:
    print("G9 FAIL — no fixture transcripts found")
    sys.exit(1)

report = run_audit(paths, DEFAULTS)
errors = _check_report(report)

if errors:
    print("G9 FAIL — schema violations:")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)

print(f"G9 PASS — report conforms to schema ({len(paths)} fixtures, {len(report['misses'])} miss entries)")
sys.exit(0)
```

- [ ] **Step 4: Run G9 gate**

```
python tests/schema_check.py
```
Expected: `G9 PASS`.

- [ ] **Step 5: Commit**

```bash
git add tests/determinism.py tests/schema_check.py
git commit -m "feat: add G8 determinism and G9 schema conformance gates"
```

---

### Task 8: `shape.py` — static config lint + G2/G3 tests

**Files:**
- Create: `src/ccgate/scripts/shape.py`
- Create: `tests/test_shape.py`

**Interfaces:**
- Consumes: `config.load_config`, `model.get_model_spec` (for window size in G3).
- Produces: `run_shape(cwd: str, config: dict) -> list[dict]` — list of finding dicts with keys `check`, `severity`, `message`. Entry point: `main()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shape.py
import tempfile
from pathlib import Path
from ccgate.scripts.shape import run_shape, _check_claudemd_lines, _check_skill_listing
from ccgate.config import DEFAULTS

class TestClaudemdLines:
    def test_file_within_limit_passes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                         delete=False) as f:
            f.write("line\n" * 100)
            path = Path(f.name)
        findings = _check_claudemd_lines([path])
        assert not any(f["check"] == "claudeMdLines" and f["severity"] == "error"
                       for f in findings)

    def test_file_over_limit_produces_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                         delete=False) as f:
            f.write("line\n" * 201)
            path = Path(f.name)
        findings = _check_claudemd_lines([path])
        assert any(f["check"] == "claudeMdLines" and f["severity"] == "error"
                   for f in findings)

class TestSkillListing:
    def test_small_descriptions_pass(self):
        # 100 chars of descriptions, budget fraction 0.01, window 200K chars ≈ 2000 budget
        findings = _check_skill_listing(
            total_desc_chars=100,
            window_tokens=200_000,
            budget_fraction=0.01,
        )
        assert not any(f["check"] == "skillListing" and f["severity"] == "error"
                       for f in findings)

    def test_over_budget_produces_warning(self):
        # 5000 chars > 1% of 200K tokens × 4 chars/token = 8000 chars budget? 
        # Actually budget = 0.01 × window_tokens × 4 chars/token = 8000
        # Let's use 10000 chars > 8000 budget
        findings = _check_skill_listing(
            total_desc_chars=10_000,
            window_tokens=200_000,
            budget_fraction=0.01,
        )
        assert any(f["check"] == "skillListing" for f in findings)
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_shape.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ccgate/scripts/shape.py`**

```python
"""shape.py — static config lint (Phase 0). Checks: G2 (claudeMdLines), G3 (skillListing)."""
import json
import sys
from pathlib import Path
from typing import Any

# Approximate chars per token for description budget estimation.
_CHARS_PER_TOKEN = 4

_CLAUDEMD_MAX_LINES = 200


def _check_claudemd_lines(paths: list[Path]) -> list[dict]:
    """G2: each loaded CLAUDE.md must be ≤ 200 lines."""
    findings = []
    for path in paths:
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except OSError:
            continue
        if lines > _CLAUDEMD_MAX_LINES:
            findings.append({
                "check": "claudeMdLines",
                "severity": "error",
                "message": (
                    f"{path} has {lines} lines (limit {_CLAUDEMD_MAX_LINES}). "
                    "Run /doctor for trim suggestions."
                ),
            })
    return findings


def _find_claudemd_files(cwd: str | None) -> list[Path]:
    """Return CLAUDE.md files that Claude Code would load for a given cwd."""
    candidates: list[Path] = []
    global_path = Path.home() / ".claude" / "CLAUDE.md"
    if global_path.exists():
        candidates.append(global_path)
    if cwd:
        project = Path(cwd)
        for parent in [project, *project.parents]:
            for name in ("CLAUDE.md", ".claude/CLAUDE.md"):
                p = parent / name
                if p.exists():
                    candidates.append(p)
            if parent == Path(parent.anchor):
                break
    return candidates


def _check_skill_listing(
    total_desc_chars: int,
    window_tokens: int,
    budget_fraction: float,
) -> list[dict]:
    """G3: predicted description chars must be ≤ skillListingBudgetFraction × window chars."""
    budget_chars = budget_fraction * window_tokens * _CHARS_PER_TOKEN
    if total_desc_chars > budget_chars:
        return [{
            "check": "skillListing",
            "severity": "warning",
            "message": (
                f"Skill descriptions total ~{total_desc_chars} chars; "
                f"budget is {budget_chars:.0f} chars "
                f"({budget_fraction*100:.1f}% of {window_tokens:,}-token window). "
                "Run /skill-doctor to identify large descriptions."
            ),
        }]
    return []


def _measure_skill_descriptions(cwd: str | None) -> int:
    """Sum character lengths of all skill description fields in loaded SKILL.md files."""
    search_roots = [Path.home() / ".claude"]
    if cwd:
        search_roots.append(Path(cwd) / ".claude")
    total = 0
    for root in search_roots:
        for skill_file in root.rglob("SKILL.md"):
            try:
                content = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            # Extract YAML frontmatter description field
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    frontmatter = content[3:end]
                    for line in frontmatter.splitlines():
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip("\"'")
                            total += len(desc)
    return total


def run_shape(cwd: str | None = None, config: dict | None = None) -> list[dict]:
    """Run all static checks; return a list of finding dicts."""
    if config is None:
        from ccgate.config import DEFAULTS
        config = DEFAULTS

    findings: list[dict] = []

    # G2 — CLAUDE.md line counts
    claudemd_paths = _find_claudemd_files(cwd)
    findings.extend(_check_claudemd_lines(claudemd_paths))

    # G3 — skill listing budget
    from ccgate.model import DEFAULT_WINDOW_TOKENS
    window_tokens = DEFAULT_WINDOW_TOKENS
    total_chars = _measure_skill_descriptions(cwd)
    budget_fraction = config.get("skillListingBudgetFraction", 0.01)
    findings.extend(_check_skill_listing(total_chars, window_tokens, budget_fraction))

    return findings


def main(argv: list[str] | None = None) -> None:
    import argparse
    from ccgate.config import load_config

    parser = argparse.ArgumentParser(prog="ccgate shape")
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--json", action="store_true", dest="emit_json")
    parser.add_argument("--assert", action="store_true", dest="assert_mode",
                        help="Exit 1 on any error-severity finding")
    args = parser.parse_args(argv)

    config = load_config(args.cwd)
    findings = run_shape(args.cwd, config)

    if args.emit_json:
        print(json.dumps(findings, indent=2))
    else:
        if not findings:
            print("ccgate shape: no issues found")
        for f in findings:
            icon = "✖" if f["severity"] == "error" else "⚠"
            print(f"  {icon} [{f['check']}] {f['message']}")

    if args.assert_mode and any(f["severity"] == "error" for f in findings):
        sys.exit(1)
```

- [ ] **Step 4: Run shape tests**

```
pytest tests/test_shape.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/scripts/shape.py tests/test_shape.py
git commit -m "feat: implement shape.py static lint (G2, G3)"
```

---

### Task 9: `statusline.py` — live budget and cache line

**Files:**
- Create: `src/ccgate/scripts/statusline.py`
- Create: `tests/test_statusline.py`

**Interfaces:**
- Consumes: `model.get_model_spec`, `transcript.detect_band`, `config.load_config`.
- Produces: `render(payload: dict, config: dict) -> str` — a single line ≤ COLUMNS chars. Entry point: `main()` reads JSON from stdin, writes to stdout.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statusline.py
import json
from pathlib import Path
from ccgate.scripts.statusline import render
from ccgate.config import DEFAULTS

FIXTURES = Path(__file__).parent / "fixtures" / "statusline"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestRender:
    def test_band3_normal_line_format(self):
        payload = _load("band3_payload.json")
        line = render(payload, DEFAULTS)
        assert "[Opus" in line or "Opus" in line
        assert "%" in line
        assert "$" in line
        assert "\n" not in line

    def test_band1_renders_without_cache_ratio(self):
        payload = _load("band1_payload.json")
        line = render(payload, DEFAULTS)
        assert isinstance(line, str)
        assert "\n" not in line

    def test_band2_shows_hit_ratio(self):
        payload = _load("band2_payload.json")
        line = render(payload, DEFAULTS)
        # Band 2 has prompt_cache.hit_ratio
        assert "cache" in line.lower() or "%" in line

    def test_cold_cache_escalation(self):
        payload = _load("band3_payload.json")
        payload["prompt_cache"]["warm"] = False
        payload["prompt_cache"]["recache_tokens_if_cold"] = 200_000
        line = render(payload, DEFAULTS)
        assert "COLD" in line

    def test_low_hit_ratio_escalation(self):
        payload = _load("band3_payload.json")
        payload["prompt_cache"]["hit_ratio"] = 0.61
        payload["prompt_cache"]["requests"] = 15
        line = render(payload, DEFAULTS)
        assert "↓" in line or "61%" in line or "cache" in line.lower()

    def test_compact_advise_escalation(self):
        payload = _load("band3_payload.json")
        payload["context_window"]["used_percentage"] = 82
        line = render(payload, DEFAULTS)
        assert "/compact" in line

    def test_no_cache_reported_escalation(self):
        payload = _load("band3_payload.json")
        payload["prompt_cache"]["caching_observed"] = False
        payload["prompt_cache"]["requests"] = 5
        line = render(payload, DEFAULTS)
        assert "NO CACHE" in line or "cache" in line.lower()

    def test_line_respects_columns(self, monkeypatch):
        import os
        monkeypatch.setenv("COLUMNS", "60")
        payload = _load("band3_payload.json")
        line = render(payload, DEFAULTS)
        assert len(line) <= 60
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/test_statusline.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ccgate/scripts/statusline.py`**

```python
"""statusline.py — live cache budget line for Claude Code's status bar.

Reads a JSON payload from stdin (from Claude Code's statusLine hook),
writes a single ≤ COLUMNS line to stdout.
"""
import json
import os
import sys

# Model short-name map for display.
_SHORT_NAMES: dict[str, str] = {
    "claude-opus-5":             "Opus 5",
    "claude-sonnet-5":           "Sonnet 5",
    "claude-fable-5-1":          "Fable 5.1",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "claude-opus-4":             "Opus 4",
    "claude-sonnet-4":           "Sonnet 4",
    "claude-haiku-4":            "Haiku 4",
}

_BAR_FILL  = "▓"
_BAR_EMPTY = "░"
_BAR_WIDTH = 10


def _model_label(payload: dict) -> str:
    model_id = (payload.get("model") or {}).get("id", "")
    for key, label in _SHORT_NAMES.items():
        if model_id.startswith(key):
            return label
    if model_id:
        # Use last segment of model id as fallback
        return model_id.split("-")[-1].capitalize()
    return "?"


def _context_bar(used_pct: float) -> str:
    filled = round(_BAR_WIDTH * used_pct / 100)
    return _BAR_FILL * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)


def _safe(payload: dict, *keys, default=None):
    """Navigate nested dict keys; return default on missing/None at any level."""
    v = payload
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k)
        if v is None:
            return default
    return v


def render(payload: dict, config: dict) -> str:
    """Build the status line string. Always ≤ COLUMNS chars, no newlines."""
    cols = int(os.environ.get("COLUMNS", "80"))

    pc = payload.get("prompt_cache") or {}
    cw = payload.get("context_window") or {}

    model_label   = _model_label(payload)
    used_pct      = float(cw.get("used_percentage") or 0)
    bar           = _context_bar(used_pct)
    cost          = _safe(payload, "cost", "total_cost_usd", default=0.0)
    hit_ratio     = pc.get("hit_ratio")
    requests      = int(pc.get("requests") or 0)
    warm          = pc.get("warm", True)
    recache       = pc.get("recache_tokens_if_cold") or 0
    caching_obs   = pc.get("caching_observed", True)

    # Base line: [Model] ▓▓▓▓▓░░░░░ 52%  ·  cache 91%  ·  $1.24
    cache_part = ""
    if hit_ratio is not None:
        cache_part = f"  ·  cache {hit_ratio*100:.0f}%"

    base = f"[{model_label}] {bar} {used_pct:.0f}%{cache_part}  ·  ${cost:.2f}"

    # Escalation: pick the highest-priority actionable condition.
    escalation = ""

    if not caching_obs and requests >= 3:
        escalation = "  NO CACHE REPORTED"

    elif used_pct >= config.get("compactAdviseAt", 0.80) * 100:
        escalation = "  → /compact"

    elif (not warm and isinstance(recache, (int, float))
          and recache > config.get("coldRecacheWarnTokens", 100_000)):
        tok_k = f"{recache/1000:.0f}K" if recache < 1_000_000 else f"{recache/1e6:.1f}M"
        escalation = f"  COLD — next turn re-caches {tok_k}"

    elif (hit_ratio is not None
          and hit_ratio < config.get("hitRatioFloor", 0.85)
          and requests >= config.get("minRequestsForRatio", 10)):
        escalation = f"  cache {hit_ratio*100:.0f}% ↓"

    line = base + escalation
    return line[:cols]


def main() -> None:
    from ccgate.config import load_config
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        payload = {}
    config = load_config()
    print(render(payload, config))
```

- [ ] **Step 4: Run statusline tests**

```
pytest tests/test_statusline.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/scripts/statusline.py tests/test_statusline.py
git commit -m "feat: implement statusline renderer with escalation conditions"
```

---

### Task 10: G11 — version floor degradation gate

**Files:**
- Create: `tests/degrade.py` (G11 gate — exits 0/1)

**Interfaces:**
- Consumes: `statusline.render`, `transcript.detect_band` with Band 1 and Band 2 fixture payloads.

- [ ] **Step 1: Write `tests/degrade.py`**

```python
#!/usr/bin/env python3
"""G11 — Version floor degradation gate.

Band 1: prompt_cache absent → statusline renders without traceback.
Band 2: prompt_cache present, miss_causes absent → statusline renders; hit_ratio shown.
Both bands must not raise any exception.
Exits 0 on pass, 1 on fail.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.scripts.statusline import render
from ccgate.transcript import detect_band, CapabilityBand
from ccgate.config import DEFAULTS

FIXTURES = Path(__file__).parent / "fixtures" / "statusline"

failures: list[str] = []

for band_name, filename, expected_band in [
    ("Band 1", "band1_payload.json", CapabilityBand.BAND1_PRE_CACHE),
    ("Band 2", "band2_payload.json", CapabilityBand.BAND2_CACHE_NO_CAUSES),
]:
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))

    # Verify band detection
    band = detect_band(payload)
    if band != expected_band:
        failures.append(f"{band_name}: detect_band returned {band}, expected {expected_band}")
        continue

    # Verify render does not raise
    try:
        line = render(payload, DEFAULTS)
    except Exception:
        failures.append(f"{band_name}: render() raised:\n{traceback.format_exc()}")
        continue

    if not isinstance(line, str) or not line:
        failures.append(f"{band_name}: render() returned empty or non-string: {line!r}")
        continue

    if "\n" in line:
        failures.append(f"{band_name}: render() output contains newline")
        continue

    print(f"  {band_name}: OK → {line!r}")

# Band 2 specific: hit_ratio must appear in the rendered line when present
band2 = json.loads((FIXTURES / "band2_payload.json").read_text(encoding="utf-8"))
band2_line = render(band2, DEFAULTS)
if "%" not in band2_line:
    failures.append("Band 2: hit_ratio not reflected in rendered line")

if failures:
    print("G11 FAIL:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)

print("G11 PASS — Band 1 and Band 2 degrade gracefully without traceback")
sys.exit(0)
```

- [ ] **Step 2: Run G11 gate**

```
python tests/degrade.py
```
Expected: `G11 PASS — Band 1 and Band 2 degrade gracefully without traceback`.

- [ ] **Step 3: Commit**

```bash
git add tests/degrade.py
git commit -m "feat: add G11 version floor degradation gate"
```

---

### Task 11: `bin/ccgate`, `dispatch.py`, plugin manifest, and skill

**Files:**
- Create: `src/ccgate/dispatch.py`
- Create: `bin/ccgate` (executable)
- Create: `.claude-plugin/plugin.json`
- Create: `skills/ccgate/SKILL.md`

**Interfaces:**
- Consumes: `scripts.statusline.main`, `scripts.miss_audit.main`, `scripts.shape.main`.
- Produces: a single `ccgate` entry point that lazy-imports and dispatches.

- [ ] **Step 1: Write `src/ccgate/dispatch.py`**

```python
"""dispatch.py — single entry point; lazy-imports handlers to minimize startup cost."""
import sys


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        _usage()
        sys.exit(0)

    subcmd = args[0]

    if subcmd in ("statusline", "status"):
        from ccgate.scripts.statusline import main as _main
        _main()

    elif subcmd in ("audit", "miss-audit"):
        from ccgate.scripts.miss_audit import main as _main
        _main(args[1:])

    elif subcmd in ("shape", "lint"):
        from ccgate.scripts.shape import main as _main
        _main(args[1:])

    elif subcmd in ("--help", "-h", "help"):
        _usage()

    else:
        print(f"ccgate: unknown subcommand '{subcmd}'", file=sys.stderr)
        _usage()
        sys.exit(1)


def _usage() -> None:
    print(
        "usage: ccgate <subcommand> [options]\n"
        "\n"
        "subcommands:\n"
        "  statusline        Read status-line JSON from stdin, write one line to stdout\n"
        "  audit [paths]     Miss-cause attribution report\n"
        "  shape             Static config and CLAUDE.md lint\n"
    )
```

- [ ] **Step 2: Create `bin/ccgate`**

On POSIX this is a shebang script. Since the project targets Windows too, also provide a `.cmd` wrapper. For now, create the POSIX version and document the Windows install step.

```bash
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ccgate.dispatch import main
main()
```

Make it executable: `chmod +x bin/ccgate`.

On Windows, install with `pip install -e .` to get `ccgate.exe` from the `[project.scripts]` entry point instead.

- [ ] **Step 3: Create `.claude-plugin/plugin.json`**

Phase 0 has no hooks. The manifest declares the plugin exists; hooks are added in Phase 1.

```json
{
  "name": "ccgate",
  "version": "0.1.0",
  "description": "Deterministic context and prompt-cache gate for Claude Code",
  "hooks": []
}
```

- [ ] **Step 4: Create `skills/ccgate/SKILL.md`**

```markdown
---
name: ccgate
description: Show cache miss-cause audit and config lint for Claude Code sessions. Usage: /ccgate audit [--since 7d] | /ccgate shape [--assert]
disable-model-invocation: true
---

Run the requested ccgate subcommand and display the result.
```

- [ ] **Step 5: Smoke test the dispatcher**

```
python -m ccgate.dispatch audit tests/fixtures/transcripts/clean_15req.jsonl
```
Expected: audit table printed, zero avoidable misses.

```
python -m ccgate.dispatch shape
```
Expected: lint output or "no issues found".

- [ ] **Step 6: Run all tests and all gate scripts**

```
pytest tests/ -v
python tests/paths.py
python tests/determinism.py
python tests/schema_check.py
python tests/degrade.py
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ccgate/dispatch.py bin/ccgate .claude-plugin/ skills/
git commit -m "feat: add dispatcher, plugin manifest, and ccgate skill (Phase 0 complete)"
```

---

## Phase 0 acceptance check

Run all gates in one shot:

Install the package in editable mode first (one-time):

```bash
pip install -e .
```

Then run all gates:

```bash
python tests/paths.py && \
python tests/determinism.py && \
python tests/schema_check.py && \
python tests/degrade.py && \
ccgate shape --assert
```

All should exit 0. If G2 or G3 fail on the local environment, `ccgate shape` output names the offending file or skill — fix there before marking Phase 0 done.
