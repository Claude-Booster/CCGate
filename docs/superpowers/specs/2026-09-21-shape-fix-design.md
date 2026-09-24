# ccgate `shape --fix` Design Spec

**Date:** 2026-09-21
**Companion to:** `BUILD-SPEC.md` v1.0, as amended by `PATCH-010-phases.md`
**Scope:** `shape --fix` and `shape --apply` — staged autofix for Phase 0 static findings
**Status:** approved for implementation

---

## 1. Context

`shape.py` already implements all eight static lint checks (G2, G3, and six supporting
checks). All 25 existing tests pass. `miss_audit.py` and `statusline.py` are also
complete. What remains from PATCH-010's Phase 0 definition is:

> `shape --fix` — safe-subset autofix targeting D4 exclusively.
> Exit criterion: measured startup token count before and after `--fix`, recorded in
> `reports/`.

This spec designs that missing piece.

---

## 2. Inherited invariants

All invariants from `BUILD-SPEC.md §1.2` apply. The three that bind hardest here:

- **I2 — Silent by default.** `--fix` produces no output unless run explicitly. The
  staged fix file is not injected into context.
- **I4 — Ground truth over estimation.** Startup token figures in the report are
  character-count proxies (chars ÷ 4), labelled `_approx` throughout. The real G1
  measurement requires `baseline.py` (API call); the report estimate is the Phase 0
  exit criterion check only.
- **I5 — No network, no telemetry, no dependencies.** Stdlib only. All writes under
  `~/.ccgate/` or the project directory.

---

## 3. CLI modes

Four modes, all routed through the existing `ccgate shape` subcommand:

```
ccgate shape                          # existing: lint, print findings
ccgate shape --fix [--fix-skills]     # stage proposed fixes → report file, print diff
ccgate shape --apply                  # apply most recent staged fix file
ccgate shape --fix --apply            # stage + immediately apply (CI use)
```

`--fix` never writes to settings.json or project files. It writes only the staged fix
file to `~/.ccgate/reports/fix-YYYYMMDD-HHMMSS.json`.

`--apply` reads the most recent staged fix file (newest mtime in `~/.ccgate/reports/`)
and applies each fix.

`--fix-skills` is a separate flag that enables SKILL.md frontmatter injection. It is
off by default even when `--fix` is passed, because it modifies files that may be owned
by marketplace plugins.

`--fix --apply` is equivalent to running both in sequence. Intended for CI pipelines
where there is no interactive review step.

---

## 4. Fix inventory

### 4.1 Fixable checks

| Check | Action | Target file | Notes |
|---|---|---|---|
| `cacheTtl` | Set `"promptCacheTtl": "1h"` | `~/.claude/settings.json` | Only when `apiKeyHelper` or `cloudProviderId` present and `promptCacheTtl` absent |
| `denyReads` | Append `"Read(dist/**/*)"` etc. to `permissions.deny` | `.claude/settings.json` | One entry per flagged directory and per flagged `*.generated.*` pattern |
| `worktreeSparse` | Set `"worktree": {"sparsePaths": [...]}` | `.claude/settings.json` | Detected package parent dirs used as path list; user expected to review before `--apply` |
| `outputCaps` | Set `"bashOutputMaxChars": 30000` | `~/.claude/settings.json` | Currently dormant (check is a Phase 0 stub); fix infra is present for Phase 1 |
| `claudeMdExcludes` | Create `# <package-name>\n` stub | `<pkg>/CLAUDE.md` | One stub per unscoped sub-package; does not overwrite existing files |
| `skillSideEffects` | Insert `disable-model-invocation: true` into SKILL.md frontmatter | `skills/*/SKILL.md` | Requires `--fix-skills` flag; skipped otherwise |

### 4.2 Non-fixable checks

| Check | Reason | Output |
|---|---|---|
| `claudeMdLines` | Truncating content requires human judgment | Appears in `skipped` with a `/doctor` hint |
| `skillListing` | Reducing description text requires human judgment | Appears in `skipped` with a `/skill-doctor` hint |

---

## 5. Staged fix file format

Written to `~/.ccgate/reports/fix-YYYYMMDD-HHMMSS.json`. Validated against
`schema/ccgate.fix-report.schema.json` (G9).

```json
{
  "generated": "2026-09-21T10:34:00Z",
  "cwd": "/path/to/project",
  "ccgate_version": "0.1.0",
  "fixes": [
    {
      "check": "cacheTtl",
      "file": "~/.claude/settings.json",
      "action": "json_set",
      "key": "promptCacheTtl",
      "value": "1h",
      "description": "Set prompt cache TTL to 1 hour"
    },
    {
      "check": "denyReads",
      "file": ".claude/settings.json",
      "action": "json_append",
      "key": "permissions.deny",
      "value": "Read(dist/**/*)",
      "description": "Deny reads of dist/ directory"
    },
    {
      "check": "claudeMdExcludes",
      "file": "packages/api/CLAUDE.md",
      "action": "create",
      "content": "# api\n",
      "description": "Create stub CLAUDE.md for package api"
    }
  ],
  "skipped": [
    {
      "check": "claudeMdLines",
      "reason": "Cannot auto-fix: requires manual content reduction"
    }
  ],
  "applied": false,
  "applied_at": null,
  "startup_chars_before": null,
  "startup_chars_after": null,
  "startup_tokens_before_approx": null,
  "startup_tokens_after_approx": null,
  "startup_delta_approx": null
}
```

### 5.1 Action types

| Action | Semantics |
|---|---|
| `json_set` | Parse target JSON file, set `key` to `value`, re-serialize |
| `json_append` | Parse target JSON file, append `value` to array at `key` (create array if absent) |
| `create` | Write `content` to `file` path; skip if file already exists |
| `frontmatter_set` | Parse YAML frontmatter block of target file, set `key` to `value`, write back |

### 5.2 `json_append` idempotency

Before appending, check if `value` is already present in the array. If so, skip — do
not duplicate. This makes `--apply` safe to run twice.

---

## 6. Write safety

Every file write follows this sequence:

1. Read existing content (if file exists).
2. Write a backup to `<path>.ccgate-bak` (overwriting any prior backup).
3. Parse and mutate in memory.
4. Write result to `<path>.tmp`.
5. `os.replace(<path>.tmp, <path>)` — atomic on POSIX; atomic on Windows within the
   same volume.

If any step raises, the original file is unchanged. The `.ccgate-bak` is left in place
as the recovery artifact.

`create` actions skip the backup step (file does not exist yet) and skip the write if
the target already exists.

---

## 7. Startup measurement and exit criterion

`--apply` measures startup overhead before applying fixes and again after:

```
startup_chars = sum(len(claudemd.read_text()) for claudemd in loaded_files)
              + _measure_skill_descriptions(cwd)
startup_tokens_approx = startup_chars // 4
```

This uses the same helpers already in `shape.py`. No API call, no network, fully
deterministic.

After applying, it prints:

```
Fixes applied. Startup overhead reduced by ~6,200 tokens (estimated).
  → Meaningful reduction. Phase 2 baseline captured in reports/fix-20260921-103400.json.
```

or:

```
Fixes applied. Startup overhead reduced by ~1,840 tokens (estimated).
  → Delta under 2,000 tokens. Repo was already lean — verify Phase 2 ROI before building.
```

The 2,000-token threshold comes from PATCH-010's exit criterion. It is not configurable
(it is a spec threshold, not a user preference).

---

## 8. Gates and tests

Phase 0 gates: **G2, G3, G9, G10**.

G2 and G3 are covered by the 25 existing `test_shape.py` tests.

G9 (schema conformance) requires a new schema file: `schema/ccgate.fix-report.schema.json`.

New test file: `tests/test_shape_fix.py`, 13 tests:

| Test | Gate |
|---|---|
| `test_stage_cachettl` | `--fix` emits `json_set` entry for cacheTtl when absent |
| `test_stage_deny_reads` | `--fix` emits `json_append` entries for each flagged dir |
| `test_stage_claudemd_stub` | `--fix` emits `create` entry per unscoped package |
| `test_skipped_claudemd_lines` | Over-limit CLAUDE.md appears in `skipped`, not `fixes` |
| `test_apply_writes_global_settings` | `--apply` patches `~/.claude/settings.json` |
| `test_apply_writes_project_settings` | `--apply` patches `.claude/settings.json` |
| `test_apply_creates_stub_claudemd` | `--apply` creates the two-line stub file |
| `test_apply_atomic_write` | Original settings file unchanged if `os.replace` raises |
| `test_apply_idempotent` | Running `--apply` twice does not duplicate `permissions.deny` entries |
| `test_fix_skills_gated` | Skill fix absent without `--fix-skills`; present with it |
| `test_report_startup_delta` | `startup_tokens_after_approx` < `startup_tokens_before_approx` after apply |
| `test_report_schema` | Report JSON validates against `schema/ccgate.fix-report.schema.json` | G9 |
| `test_report_paths_windows` | Report `file` fields use forward slashes on Windows fixtures | G10 |

---

## 9. Build order

```
schema/ccgate.fix-report.schema.json   ← define first; tests import it
tests/test_shape_fix.py                ← write failing tests (TDD red)
shape.py: stage_fixes()                ← implement staging logic
shape.py: apply_fixes()                ← implement apply logic
shape.py: main() --fix / --apply args  ← wire CLI args
                                          tests go green
```

---

## 10. Out of scope

- `.contextignore` creation — this is Phase 2 (F1). Not included in `--fix`.
- `--fix --dry-run` as a separate flag — `--fix` without `--apply` is already dry-run by
  definition (staged file written, nothing applied).
- Interactive per-fix confirmation — the staged file is the review step. Apply is all-or-nothing.
- Rollback command — the `.ccgate-bak` files are the rollback mechanism. No separate
  `shape --rollback` command in this phase.
