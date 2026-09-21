# Phase 3 — CI and Fleet Design

**Date:** 2026-09-21
**Status:** approved
**Implements:** BUILD-SPEC §4.5, §5 (baseline.py), §9 (G1), §10 (Phase 3)

---

## 1. Purpose

Phase 3 adds four independently usable tools that extend ccgate from a single-user measurement plugin into a CI-gate and team-sharing system:

1. **`baseline.py`** — G1 gate: asserts ccgate's own startup overhead stays ≤ 12 000 first-turn input tokens.
2. **`digest.py`** — exports learned file-access patterns to a portable, safe-to-share JSON digest; imports a teammate's digest and merges it into local state.
3. **`otel_reader.py`** — receives `claude_code.token.usage` metric exports via OTLP HTTP or a pre-exported file and writes ccgate-compatible session accounting files.
4. **`clear_eval.py`** — calls the Anthropic `count_tokens` API with `clear_tool_uses_20250919` to produce a *measured* (not estimated) token-savings figure for a session.

All four are independent CLI scripts. The hooks and core pipeline remain stdlib-only. Phase 3 tools that require optional dependencies guard their imports and degrade gracefully.

---

## 2. Architecture

**Approach:** independent scripts, shared infrastructure. Each script uses `ccgate.config`, `ccgate.state`, `ccgate.model`, and `ccgate.transcript` as needed. None call each other.

**New files:**

```
src/ccgate/scripts/
  baseline.py          G1 gate
  digest.py            export + import learned path patterns
  otel_reader.py       OTLP collector → ccgate session accounting
  clear_eval.py        count_tokens + clear_tool_uses evaluation

schema/
  ccgate.digest.schema.json

tests/
  fixtures/
    baseline/
      first_turn.jsonl       single assistant turn, input_tokens ≈ 8 000
    otlp_export.json         static OTLP JSON fixture
  test_baseline.py
  test_digest.py
  test_otel_reader.py
  test_clear_eval.py
```

**Modified files:**

| File | Change |
|---|---|
| `src/ccgate/config.py` | Add `startupTokenCap`, `otelPort`, `digestMaxPaths` to DEFAULTS |
| `schema/ccgate.config.schema.json` | Add same three keys with range validation |

**Data flows:**

```
baseline.py   ←  tests/fixtures/baseline/first_turn.jsonl
                  reads usage.input_tokens of first assistant entry
              →  stdout: "startup: N tokens  [PASS|FAIL ≤ cap]"
              →  exit 0 (pass) or exit 1 (fail)

digest export ←  ~/.ccgate/sessions/{id}-read-cache.json  (file_log, reads{})
                  ~/.ccgate/sessions/{id}.json             (miss_cause counts)
              →  digest.json  (relative paths + counts; absolute paths rejected)

digest import ←  digest.json  (audited: relative only, no .. traversal)
              →  ~/.ccgate/patterns.json  (merged, atomic write)

otel_reader   ←  OTLP HTTP POST /v1/metrics
                  OR --file otlp_export.json
              →  ~/.ccgate/sessions/{session_id}-otel.json
                  { "input": N, "output": N,
                    "cache_read": N, "cache_creation": N }

clear_eval    ←  transcript JSONL (--session ID or --transcript PATH)
              →  anthropic.beta.messages.count_tokens(
                    context_management={"type":"clear_tool_uses_20250919"})
              →  stdout: original / cleared / savings tokens + ~USD
```

---

## 3. New Config Keys

Three keys added to `DEFAULTS` in `config.py` and to `schema/ccgate.config.schema.json`:

| Key | Default | Range | Effect |
|---|---|---|---|
| `startupTokenCap` | `12000` | 1–200 000 | G1 threshold for `baseline.py` |
| `otelPort` | `4318` | 1024–65535 | OTLP HTTP server port |
| `digestMaxPaths` | `500` | 1–10 000 | Max paths written to a digest export |

Environment overrides follow the existing `CCGATE_<UPPER_SNAKE>` pattern.

---

## 4. `baseline.py` — G1 Gate

### 4.1 Algorithm

```
1. Locate fixture: --fixture PATH, else <repo-root>/tests/fixtures/baseline/first_turn.jsonl
2. Read JSONL line by line; skip lines that are not valid JSON (comment lines,
   blank lines); find first entry where:
     entry["type"] == "assistant"
     entry["message"]["usage"]["input_tokens"] is present
3. Extract input_tokens (integer)
4. Load config → startupTokenCap (default 12 000)
5. Print: "startup: {tokens} tokens  [PASS|FAIL ≤ {cap}]"
6. Exit 0 if tokens ≤ cap; exit 1 otherwise
```

### 4.2 CLI

```
python -m ccgate.scripts.baseline [--fixture PATH] [--cap N] [--json]
```

`--json` emits `{"tokens": N, "cap": N, "pass": true|false}` for machine consumption in CI.
`--cap N` overrides config at runtime (useful for local experiments without editing config).

### 4.3 Fixture Design

`tests/fixtures/baseline/first_turn.jsonl`: a single JSON line representing the first assistant message of a minimal ccgate session. `usage.input_tokens` is hand-set to **8 000** — reflecting a realistic startup footprint (plugin manifest + one CLAUDE.md + skills listing) with comfortable headroom under the 12 K cap.

The file opens with a comment block (as a non-JSONL line that the parser skips) documenting:
- what the 8 000 figure represents
- what to update if ccgate's footprint grows
- the date it was last calibrated

### 4.4 Tests

| Test | Assertion |
|---|---|
| `test_passes_on_fixture` | Bundled fixture → exit 0 |
| `test_fails_above_cap` | Fixture with `input_tokens=13000`, default cap → exit 1 |
| `test_custom_cap_flag` | `--cap 7000` on 8 K fixture → exit 1 |
| `test_json_output` | `--json` emits valid JSON with `tokens`, `cap`, `pass` |
| `test_missing_fixture` | Missing path → exit 1, clear error message to stderr |

---

## 5. `digest.py` — Pattern Digest Export/Import

### 5.1 Source Data

The export reads from Phase 2 read-cache state files:

- `~/.ccgate/sessions/{id}-read-cache.json` — `file_log` (ordered list of paths read per session; frequency = count of appearances) and `reads{}` (per-key mtime tracking, used to derive cache-block counts)
- `~/.ccgate/sessions/{id}.json` — per-request `miss_cause` classifications

**Cache-block count derivation:** a path appears in `reads{}` at a given `idx`. If the same key appears again in `file_log` after `idx` without a mtime change between the two appearances, that is one read-cache block event. The digest records the count of such events per path.

### 5.2 Export Algorithm

```
1. Enumerate all ~/.ccgate/sessions/{id}-read-cache.json
2. For each file, aggregate:
     file_log  → per abs_path read_count (count of appearances)
     reads{}   → per abs_path cache_block_count (blocked re-reads)
3. Path validation — for each abs_path:
     rel = os.path.relpath(abs_path, cwd_at_export)
     if os.path.isabs(rel):       skip + stderr warning
     if ".." in rel.split(sep):   skip + stderr warning
4. Merge across sessions: sum counts for the same rel path
5. Sort by read_count descending; cap at digestMaxPaths entries
6. Assign digest_id = uuid4(), recorded_at = now ISO-8601
7. Write digest.json atomically (os.replace)
```

### 5.3 Import Algorithm

```
1. Read digest.json; validate against ccgate.digest.schema.json
2. For every path_stats entry:
     if os.path.isabs(entry["path"]): ABORT — do not write partial state
     if ".." in entry["path"].split(sep): ABORT — do not write partial state
   Note: always reject, never sanitise. One bad entry aborts the entire import.
3. Load ~/.ccgate/patterns.json (empty dict if absent)
4. Merge: for each entry, add read_count and cache_block_count to existing
5. Record import metadata: timestamp, source digest_id
6. Write patterns.json atomically (os.replace)
7. Print: "imported N paths from {digest_id}; M entries rejected"
```

### 5.4 CLI

```
python -m ccgate.scripts.digest export [--out PATH] [--cwd CWD]
python -m ccgate.scripts.digest import PATH
```

`--cwd CWD` overrides the base for relative-path conversion (default: `os.getcwd()`).

### 5.5 Digest Schema (`schema/ccgate.digest.schema.json`)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["digest_id", "exported_at", "ccgate_version",
               "path_stats", "sessions_analyzed"],
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
          "path":             { "type": "string", "pattern": "^(?!/)(?!.*\\.\\.).*" },
          "read_count":       { "type": "integer", "minimum": 0 },
          "cache_block_count":{ "type": "integer", "minimum": 0 }
        }
      }
    }
  }
}
```

The `path` pattern rejects leading `/` (absolute) and `..` sequences at the schema level as a backstop.

### 5.6 Tests

| Test | Assertion |
|---|---|
| `test_export_produces_relative_paths` | All exported paths are relative |
| `test_export_rejects_absolute_paths` | Absolute-path entries skipped; rest exported |
| `test_export_rejects_dotdot` | `../secret` entries skipped |
| `test_export_caps_at_digestMaxPaths` | Output has ≤ 500 entries (default cap) |
| `test_export_schema_valid` | Output validates against ccgate.digest.schema.json |
| `test_import_merges_counts` | read_count + cache_block_count accumulate correctly |
| `test_import_rejects_absolute_path` | Aborts on first absolute path; patterns.json unchanged |
| `test_import_rejects_dotdot` | Aborts on `..` traversal; patterns.json unchanged |
| `test_import_atomic` | Simulated crash during write leaves patterns.json unchanged |
| `test_round_trip` | export → import → export produces stable counts |

---

## 6. `otel_reader.py` — OTLP Collector

### 6.1 OTLP JSON Shape

Claude Code emits OTLP JSON metrics in this shape (the only shape the reader handles):

```json
{
  "resourceMetrics": [{
    "resource": {
      "attributes": [{"key": "session.id",
                      "value": {"stringValue": "<session-id>"}}]
    },
    "scopeMetrics": [{
      "metrics": [{
        "name": "claude_code.token.usage",
        "sum": {
          "dataPoints": [
            {"attributes": [{"key": "type",
                             "value": {"stringValue": "input"}}],
             "asInt": "12345"},
            {"attributes": [{"key": "type",
                             "value": {"stringValue": "cache_read"}}],
             "asInt": "9800"}
          ]
        }
      }]
    }]
  }]
}
```

Known `type` labels: `input`, `output`, `cache_read`, `cache_creation`. Unknown labels are stored under `"unknown"` — never dropped.

### 6.2 Parser Algorithm (shared by both modes)

```
1. Extract session_id from resource.attributes[key="session.id"].stringValue
   → fall back to "unknown" if absent or malformed
2. Find all metrics where name == "claude_code.token.usage"
3. For each dataPoint:
     type_label = attribute[key="type"].stringValue
     value      = int(dataPoint["asInt"])   # asDouble used if asInt absent
4. Accumulate into {input, output, cache_read, cache_creation, unknown}
5. Write ~/.ccgate/sessions/{session_id}-otel.json atomically (os.replace):
   {
     "session_id": "...",
     "collected_at": "<ISO-8601>",
     "source": "otlp_http" | "otlp_file",
     "tokens": {
       "input": N, "output": N,
       "cache_read": N, "cache_creation": N
     }
   }
```

### 6.3 Server Mode (`serve`)

A minimal `http.server.HTTPServer` subclass:
- Listens on `otelPort` (default 4318)
- Accepts `POST /v1/metrics` with `Content-Type: application/json`
- Returns `200 {}` on success; `400` on JSON parse error; `404` on all other paths/methods
- Single-threaded (Claude Code sends sequentially)
- Runs until `KeyboardInterrupt`; no daemon threads, no signal handling beyond keyboard

### 6.4 File Mode (`read`)

Reads `--file PATH`, parses with the shared algorithm, writes session file, exits 0.

### 6.5 CLI

```
python -m ccgate.scripts.otel_reader serve [--port N]
python -m ccgate.scripts.otel_reader read  --file PATH
```

### 6.6 Tests

| Test | Assertion |
|---|---|
| `test_parse_standard_payload` | All four type labels extracted correctly |
| `test_unknown_type_label` | Unknown label stored under `"unknown"`, not dropped |
| `test_missing_session_id` | Falls back to `"unknown"` session, no exception |
| `test_asDouble_fallback` | `asDouble` used when `asInt` absent |
| `test_file_mode_writes_session` | `read --file` produces correct `{id}-otel.json` |
| `test_atomic_write` | Output written via temp + os.replace |
| `test_server_post_returns_200` | HTTP POST to `/v1/metrics` returns 200 |
| `test_server_bad_json_returns_400` | Malformed body returns 400 |
| `test_server_wrong_path_returns_404` | GET or wrong path returns 404 |

Fixture: `tests/fixtures/otlp_export.json` — static OTLP JSON with realistic token counts across all four type labels plus one unknown label.

---

## 7. `clear_eval.py` — Tool-Use Clearing Evaluation

### 7.1 Dependency

```python
try:
    import anthropic
except ImportError:
    sys.exit(
        "clear_eval requires the anthropic package.\n"
        "Install with: pip install anthropic"
    )
```

API key read from `ANTHROPIC_API_KEY` environment variable. If absent, exits 1 with a clear message. Never hardcoded.

### 7.2 Algorithm

```
1. Load transcript JSONL:
     --session ID  → resolved via transcript.py path-encoding logic
     --transcript PATH → read directly
2. Reconstruct messages[] from transcript entries
   (reuses existing transcript.py reader — no new parsing)
3. Extract last_model_id from the final assistant entry's model field
4. Call count_tokens (baseline):
     client.beta.messages.count_tokens(
         model=last_model_id,
         messages=messages,
     )
     original_tokens = response.input_tokens

5. Call count_tokens (with clearing):
     client.beta.messages.count_tokens(
         model=last_model_id,
         messages=messages,
         context_management={"type": "clear_tool_uses_20250919"}
     )
     cleared_tokens   = response.input_tokens
     original_before  = response.context_management.original_input_tokens

6. savings_tokens = original_before - cleared_tokens
   savings_pct    = savings_tokens / original_before * 100  (0 if original_before == 0)
   savings_usd    = savings_tokens * model_spec.rate_in     (labelled ~ per I4)

7. Print (default):
     original : {original_before:,} tokens
     cleared  : {cleared_tokens:,} tokens
     savings  : {savings_tokens:,} tokens  ({savings_pct:.1f}%)  ~${savings_usd:.4f}

   Or --json:
     {
       "original_tokens": N,
       "cleared_tokens": N,
       "savings_tokens": N,
       "savings_pct": F,
       "savings_usd_approx": F
     }
```

Zero or negative savings are reported as `0` — never as a negative number. The `~` prefix on the USD figure is mandatory per BUILD-SPEC I4.

### 7.3 CLI

```
python -m ccgate.scripts.clear_eval --session <id>
python -m ccgate.scripts.clear_eval --transcript <path>
python -m ccgate.scripts.clear_eval --session <id> --json
```

### 7.4 I5 Constraint Note

`clear_eval.py` is the only Phase 3 script that makes a network call and requires a non-stdlib package. It is an opt-in CLI tool — never called by a hook, never runs automatically. The `anthropic` import is fully guarded. The hooks and core pipeline remain stdlib-only. This is consistent with BUILD-SPEC §4.5 scoping this to "Agent SDK path only."

### 7.5 Tests

All tests mock `anthropic.Anthropic` — no live API calls in CI.

| Test | Assertion |
|---|---|
| `test_savings_computed_correctly` | Mocked responses; savings math verified |
| `test_json_output_fields` | `--json` emits all required keys |
| `test_missing_api_key_exits_1` | No `ANTHROPIC_API_KEY` → clean error, exit 1 |
| `test_missing_anthropic_package` | ImportError path → clear install message, exit 1 |
| `test_zero_savings_reported` | cleared ≥ original → savings = 0, not negative |
| `test_model_id_from_last_turn` | Uses model id from final assistant entry |

---

## 8. Gate Coverage

| ID | Gate | Threshold | Script | Phase |
|---|---|---|---|---|
| **G1** | Startup overhead | first-turn input tokens ≤ `startupTokenCap` (12 000) | `baseline.py` | **3** |

G1 is the only new gate in Phase 3. All existing gates (G2–G11) remain unchanged.

---

## 9. Testing Plan

| File | Scope |
|---|---|
| `tests/test_baseline.py` | 5 pytest tests |
| `tests/test_digest.py` | 10 pytest tests |
| `tests/test_otel_reader.py` | 9 pytest tests |
| `tests/test_clear_eval.py` | 6 pytest tests (all mock anthropic) |

Total: 30 new pytest tests added to the existing suite.

`baseline.py` itself is the G1 standalone gate script — no separate perf script is needed.

---

## 10. Files Created / Modified

| Action | Path |
|---|---|
| CREATE | `src/ccgate/scripts/baseline.py` |
| CREATE | `src/ccgate/scripts/digest.py` |
| CREATE | `src/ccgate/scripts/otel_reader.py` |
| CREATE | `src/ccgate/scripts/clear_eval.py` |
| CREATE | `schema/ccgate.digest.schema.json` |
| CREATE | `tests/fixtures/baseline/first_turn.jsonl` |
| CREATE | `tests/fixtures/otlp_export.json` |
| CREATE | `tests/test_baseline.py` |
| CREATE | `tests/test_digest.py` |
| CREATE | `tests/test_otel_reader.py` |
| CREATE | `tests/test_clear_eval.py` |
| MODIFY | `src/ccgate/config.py` (add 3 keys to DEFAULTS) |
| MODIFY | `schema/ccgate.config.schema.json` (add 3 keys) |

---

## 11. Global Constraints

All BUILD-SPEC invariants apply. Key ones for Phase 3:

- **I1** — no hook behavior depends on prose; all Phase 3 features are CLI tools, not hooks
- **I3** — `clear_eval.py` savings figures are net of ccgate's own cost (not applicable here — clear_eval measures session savings, not ccgate overhead); savings_usd carries `~`
- **I4** — USD figures from `clear_eval.py` carry `~` prefix
- **I5** — hooks and core pipeline remain stdlib only; `clear_eval.py` optional dep is guarded
- Python 3.11+, stdlib only for hooks; `anthropic` package optional for `clear_eval.py`
- `CCGATE_HOME` env var overrides `~/.ccgate` in all state functions
- `os.replace(tmp, target)` for all atomic writes
- Exit 0 always in hooks; `baseline.py` and `digest.py` exit 1 on gate failure or bad input
