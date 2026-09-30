# Track B / B1a — F1 richer `.contextignore` + Bash-read deny Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deny the common shell readers (`cat`/`head`/`sed`…) of `.contextignore`'d files in the `ccgate run` loop, and upgrade `.contextignore` matching so the Read and Bash paths share one predicate — closing the completeness gap where an ignored file re-enters context via Bash.

**Architecture:** A new shared `run/shellcmd.py` (runner-strip + the prefix helpers relocated from `bashcap.py`); an extended `run/policy.py` (`path_is_ignored` gains `\`→`/` normalization + a trailing-`/` directory rule and stays a pure "any match → ignored" predicate; a new stateful Bash-read deny matcher; Read-deny gains optional recording); a `bashCapEnabled`→`bashEnabled` config rename with a new `bashReadPrefixes`; `cli.py` wires two PreToolUse matchers (Read, Bash) plus the F3 PostToolUse; and a CI probe proving the Bash deny red→green.

**Tech Stack:** Python 3.11+, `claude-agent-sdk` (optional `run` extra), pytest, stdlib `fnmatch`/`shlex`-free tokenizing (`str.split`).

**Spec:** `docs/superpowers/specs/2026-09-30-track-b-b1a-f1-bash-read-deny-design.md`

## Global Constraints

- **This is a token-efficiency feature, not a security control.** Deny the *common* readers, best-effort; never claim a file cannot be read. On a miss, do nothing (spec §1).
- **One predicate:** the Read path and Bash path must call the *same* `path_is_ignored`; the substring test pins that they agree (spec §1 criterion 3).
- **Deny wording is cost-framed** on both surfaces — "excluded because expensive and rarely useful," never "blocked"/"forbidden" (spec §5).
- **`path_is_ignored` stays a pure "any match → ignored" predicate** — no negation, no ordered last-match-wins (spec §3, deferred).
- **Fail-open but not silent:** a caught exception increments `matcher_errors` *first*, then returns `{}` (spec §7).
- **Reader/prefix lists reject regex metachars at config load** via `compile_prefixes` (literal matching only) (spec §4).
- **Fixtures never named like secrets** (`*secret*`/`*credential*`/`.env`) — Claude Code's sensitive-filename guard hard-denies such reads regardless of config and confounds the baseline (spec §9; `feedback-verify-mechanism-not-green`).
- **No git hook bypass; no hardcoded absolute paths; TDD.** The SDK loop and Bash cannot run on-desk (org sandbox) — in-process tests are agent-runnable; the real Bash deny proof is CI (spec §9).

## Review Focus

- **`lessc styles.less` / `catalog x`** (reader-prefix false positives) → the reader gate uses **first-token equality**, not `startswith`, so `lessc`/`catalog`/`header` are NOT treated as `less`/`cat`/`head`. → Task 5 test.
- **Two PreToolUse matchers construct under the real SDK** — nothing has registered two matchers on one event before; `_factory` must build `[HookMatcher("Read",…), HookMatcher("Bash",…)]` without drift. → Task 7 factory-drift test.
- **Read-path vs Bash-path agreement** — `path_is_ignored("foo.lock.bak", ["*.lock"])` must give the *same* answer whether reached via Read `file_path` or a Bash token. → Task 5 substring test.
- **Fail-open increments the counter** — an exception inside the matcher returns `{}` AND `matcher_errors` rises; never one without the other. → Task 5 test.
- **Read-deny recording actually fires** — `recorder=None` keeps B0 green, but when a recorder IS passed the F1 read event must be written (the honesty fix must not be a no-op). → Task 6 test.

---

## Task 1: Reconcile plan1's B0 fixes into the B1a branch

**Files:** (merge — no single file authored)

**Interfaces:** none (git integration). Produces a base tree that has both B1b (from `trackb`) and B0's CI fixes (from `plan1-fix-miss-audit`): the `target_secret.txt`→`target_marker.txt` fixture rename, the hardened `scripts/b0_ci_probe.py`, and the B0 spec updates.

Rationale (spec §11): B1a branches from `trackb-b1b-bash-cap` (it needs B1b), but `trackb` lacks plan1's post-`3db569f` B0 fixes. Bring them forward so the codebase is consistent and the b1a probe doesn't rediscover the sensitive-filename guard.

- [ ] **Step 1: Confirm the branch and starting state**

Run: `git branch --show-current`
Expected: `trackb-b1a-f1-bash-read`

- [ ] **Step 2: Merge plan1's B0 fixes**

Run: `git merge plan1-fix-miss-audit`
Expected: merges cleanly, OR conflicts only in files both branches changed after `3db569f`. Likely-clean files (plan1-only changes): `tests/fixtures/run/target_marker.txt` (rename), `scripts/b0_ci_probe.py`, `docs/superpowers/specs/2026-09-28-track-b-b0-run-harness-design.md`. `.gitignore` — both added identical lines → no conflict.

- [ ] **Step 3: If `src/ccgate/run/cli.py` conflicts, resolve to trackb's version**

plan1's net change to `cli.py` after `3db569f` is ~zero (bypass was added then reverted). trackb's `cli.py` has the B1b wiring and no bypass — the correct end state. Resolve any `cli.py` conflict by **keeping trackb's B1b version** (`git checkout --ours src/ccgate/run/cli.py` if trackb is `ours`, then re-verify no `permission_mode`/`bypassPermissions` remains). Confirm with: `grep -n "permission_mode\|bypass" src/ccgate/run/cli.py` → no matches.

- [ ] **Step 4: Run the full suite on the merged tree**

Run: `python -m pytest -q`
Expected: PASS (all tests green — the merge must not regress B0 or B1b).

- [ ] **Step 5: Commit the merge**

```bash
git commit --no-edit   # completes the merge commit if not already created
```
(If the merge auto-committed, this is a no-op; do not amend.)

---

## Task 2: `run/shellcmd.py` — relocate prefix helpers + `strip_runner_prefixes`

**Files:**
- Create: `src/ccgate/run/shellcmd.py`
- Modify: `src/ccgate/run/bashcap.py` (import the relocated helpers back)
- Test: `tests/test_shellcmd.py`

**Interfaces:**
- Produces: `_METACHARS`; `compile_prefixes(prefixes: list[str]) -> list[str]`; `command_matches(command: str, prefixes: list[str]) -> bool`; `first_token(command: str) -> str`; `strip_runner_prefixes(command: str, max_iter: int = 6) -> str`.
- Consumes (bashcap): imports `compile_prefixes`, `command_matches`, `_METACHARS` from `shellcmd`.

- [ ] **Step 1: Write the failing tests for `strip_runner_prefixes` and `first_token`**

```python
# tests/test_shellcmd.py
import pytest
from ccgate.run.shellcmd import (
    compile_prefixes, command_matches, first_token, strip_runner_prefixes,
)


def test_strip_bare_runners():
    assert strip_runner_prefixes("sudo cat x") == "cat x"
    assert strip_runner_prefixes("time cat x") == "cat x"
    assert strip_runner_prefixes("command cat x") == "cat x"
    assert strip_runner_prefixes("nice cat x") == "cat x"


def test_strip_env_assignments_and_bare_env():
    assert strip_runner_prefixes("env FOO=1 cat x") == "cat x"
    assert strip_runner_prefixes("env FOO=1 BAR=2 cat x") == "cat x"
    assert strip_runner_prefixes("env cat x") == "cat x"
    assert strip_runner_prefixes("env -i cat x") == "cat x"


def test_strip_sudo_value_flags():
    assert strip_runner_prefixes("sudo -u foo cat x") == "cat x"
    assert strip_runner_prefixes("sudo -n cat x") == "cat x"


def test_strip_recursive_with_cap():
    assert strip_runner_prefixes("sudo env FOO=1 cat x") == "cat x"


def test_strip_leaves_non_runner_untouched():
    assert strip_runner_prefixes("cat x") == "cat x"
    assert strip_runner_prefixes("pytest -q") == "pytest -q"
    assert strip_runner_prefixes("") == ""


def test_strip_b1b_inputs_for_future_retrofit():
    # B1b's F3 truncation is defeated by these today; the retrofit will call this helper.
    assert strip_runner_prefixes("sudo pytest") == "pytest"
    assert strip_runner_prefixes("env CI=1 cargo test") == "cargo test"
    assert strip_runner_prefixes("time npm test") == "npm test"


def test_first_token():
    assert first_token("cat foo.lock") == "cat"
    assert first_token("  head  -100 x") == "head"
    assert first_token("") == ""


def test_compile_prefixes_rejects_metachars():
    with pytest.raises(ValueError):
        compile_prefixes(["cat", "gr.p"])
    assert compile_prefixes(["cat", "head"]) == ["cat", "head"]


def test_command_matches_startswith():
    assert command_matches("cargo test --all", ["cargo test"]) is True
    assert command_matches("ls -la", ["cat"]) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shellcmd.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccgate.run.shellcmd'`.

- [ ] **Step 3: Create `shellcmd.py` with the relocated helpers + new functions**

```python
# src/ccgate/run/shellcmd.py
"""shellcmd.py — shared, pure shell-command helpers for Track B matchers.

Relocated prefix helpers (were in bashcap.py) live here so the shared module does not
depend on a feature module. strip_runner_prefixes peels a leading runner (sudo/env/
time/command/nice) so a reader hidden behind it is still seen — best-effort, no shell
grammar parsing (spec §4)."""
from __future__ import annotations

_METACHARS = set(".*+?[(")
_RUNNERS = {"sudo", "env", "time", "command", "nice"}
_SUDO_VALUE_FLAGS = {"-u", "-g", "-U", "-C", "-p", "-r", "-t", "-h", "-R", "-D"}


def compile_prefixes(prefixes: list[str]) -> list[str]:
    """Return prefixes unchanged, or raise ValueError if any contains a regex metachar
    (literal-prefix matching only)."""
    for p in prefixes:
        bad = _METACHARS & set(p)
        if bad:
            raise ValueError(f"prefix entry {p!r} contains regex metachar(s) {sorted(bad)}; "
                             "literal prefixes only")
    return list(prefixes)


def command_matches(command: str, prefixes: list[str]) -> bool:
    """True if command starts with any prefix (multi-word prefixes like 'cargo test')."""
    return any(command.startswith(p) for p in prefixes)


def first_token(command: str) -> str:
    """The first whitespace-delimited token, or '' for an empty command."""
    toks = command.split()
    return toks[0] if toks else ""


def strip_runner_prefixes(command: str, max_iter: int = 6) -> str:
    """Peel leading runner prefixes so a reader behind them is visible.

    Handles: bare sudo/time/command/nice; sudo value-flags (-u foo); env with KEY=VAL
    assignments and bare env; simple leading flags. Recurses (sudo env FOO=1 cat x) under
    a small iteration cap. Deeper handling would be shell-grammar parsing — a non-goal."""
    for _ in range(max_iter):
        toks = command.split()
        if not toks or toks[0] not in _RUNNERS:
            return command
        runner = toks[0]
        rest = toks[1:]
        i = 0
        while i < len(rest):
            t = rest[i]
            if runner == "env" and "=" in t and not t.startswith("-"):
                i += 1
                continue
            if t.startswith("-"):
                i += 1
                if runner == "sudo" and t in _SUDO_VALUE_FLAGS and i < len(rest):
                    i += 1  # also skip the flag's value (sudo -u foo)
                continue
            break
        command = " ".join(rest[i:])
    return command
```

- [ ] **Step 4: Update `bashcap.py` to import the relocated helpers and leave a retrofit breadcrumb**

Replace the local `_METACHARS`, `compile_prefixes`, `command_matches` definitions in `src/ccgate/run/bashcap.py` with an import, and annotate the runner-prefix gap:

```python
from ccgate.run.shellcmd import compile_prefixes, command_matches  # noqa: F401  (re-exported for callers)
```

At `command_matches`'s call site inside `BashCapHook.__call__`, add the breadcrumb comment (spec §3 deferred retrofit):

```python
        # NOTE: startswith is correct HERE — B1b prefixes like "cargo test" must match
        # "cargo test --all"; do NOT switch this to first-token equality (that is B1a's
        # reader gate, a different problem). The only gap is that runner prefixes
        # (sudo/time/env) defeat it, so `sudo pytest` is not truncated. Fix when B1b is
        # next touched: command = shellcmd.strip_runner_prefixes(command) before matching.
        if not command_matches(command, self.prefixes):
            return {}
```

- [ ] **Step 5: Run shellcmd + bashcap tests**

Run: `python -m pytest tests/test_shellcmd.py tests/test_bashcap.py -q`
Expected: PASS (shellcmd new tests pass; bashcap's existing tests still pass via the import — no behavior change).

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/run/shellcmd.py src/ccgate/run/bashcap.py tests/test_shellcmd.py
git commit -m "feat(run): shellcmd shared helpers — relocate prefix funcs + strip_runner_prefixes"
```

---

## Task 3: `run/policy.py` — richer `path_is_ignored`

**Files:**
- Modify: `src/ccgate/run/policy.py` (`path_is_ignored`)
- Test: `tests/test_run_policy.py`

**Interfaces:**
- Produces: `path_is_ignored(path: str, patterns: list[str]) -> bool` — now normalizes `\`→`/` and supports trailing-`/` directory patterns; still "any match → ignored".

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_run_policy.py
from ccgate.run.policy import path_is_ignored


def test_backslash_paths_normalized():
    assert path_is_ignored("C:\\repo\\foo.lock", ["*.lock"]) is True
    assert path_is_ignored("C:\\repo\\src\\app.py", ["*.lock"]) is False


def test_trailing_slash_directory_segment_match():
    pats = ["node_modules/"]
    assert path_is_ignored("node_modules/pkg/index.js", pats) is True
    assert path_is_ignored("src/node_modules/a.js", pats) is True
    assert path_is_ignored("node_modules", pats) is True
    assert path_is_ignored("mynode_modules/x.js", pats) is False   # substring, not a segment
    assert path_is_ignored("src/app.py", pats) is False


def test_existing_glob_and_basename_still_work():
    assert path_is_ignored("a/b/package-lock.json", ["package-lock.json"]) is True
    assert path_is_ignored("x/y/foo.min.js", ["*.min.js"]) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_run_policy.py -k "backslash or trailing_slash or existing_glob" -v`
Expected: FAIL — `test_trailing_slash_directory_segment_match` fails (current fnmatch does not do segment matching); `test_backslash_paths_normalized` may fail on the backslash path.

- [ ] **Step 3: Implement richer matching**

Replace `path_is_ignored` in `src/ccgate/run/policy.py`:

```python
def path_is_ignored(path: str, patterns: list[str]) -> bool:
    """True if path matches any pattern. Normalizes \\ -> / so Windows paths and forward-
    slash patterns agree. A trailing-slash pattern 'X/' matches iff X is a complete path
    segment (the dir itself and everything beneath, at any depth). Otherwise fnmatch on the
    full path or basename. Pure 'any match -> ignored' — no negation (spec §3)."""
    norm = path.replace("\\", "/")
    name = norm.rsplit("/", 1)[-1]
    wrapped = "/" + norm + "/"
    for pat in patterns:
        p = pat.replace("\\", "/")
        if p.endswith("/"):
            seg = p[:-1]
            if seg and ("/" + seg + "/") in wrapped:
                return True
            continue
        if fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(name, p):
            return True
    return False
```

(Keep the `import fnmatch` at the top of the file.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_run_policy.py -q`
Expected: PASS (new tests pass; existing policy tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/policy.py tests/test_run_policy.py
git commit -m "feat(run): richer path_is_ignored — backslash normalization + trailing-slash dirs"
```

---

## Task 4: `config.py` — `bashCapEnabled`→`bashEnabled` rename + `bashReadPrefixes`

**Files:**
- Modify: `src/ccgate/config.py`
- Modify: `src/ccgate/run/cli.py` (gate `config.get("bashEnabled")`)
- Modify: `scripts/b1b_ci_probe.py` (`{"bashCapEnabled": true}` → `{"bashEnabled": true}`)
- Test: `tests/test_config.py`, `tests/test_run_cli.py`

**Interfaces:**
- Produces: config key `bashEnabled` (bool, default `False`) replacing `bashCapEnabled`; `bashReadPrefixes` (list, default `["cat","head","tail","less","more","sed","awk"]`), list-merged and metachar-validated.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_config.py
from ccgate.config import load_config


def test_bashenabled_replaces_bashcapenabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    cfg = load_config()
    assert "bashEnabled" in cfg and cfg["bashEnabled"] is False
    assert "bashCapEnabled" not in cfg


def test_bashreadprefixes_default_and_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashReadPrefixes": ["xxd"]}', encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert "cat" in cfg["bashReadPrefixes"] and "xxd" in cfg["bashReadPrefixes"]  # merged, not replaced
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_config.py -k "bashenabled or bashreadprefixes" -v`
Expected: FAIL — `bashEnabled` absent; `bashReadPrefixes` absent.

- [ ] **Step 3: Rename the key and add `bashReadPrefixes`**

In `src/ccgate/config.py` DEFAULTS: replace `"bashCapEnabled": False` with `"bashEnabled": False`, and add:

```python
    "bashReadPrefixes": ["cat", "head", "tail", "less", "more", "sed", "awk"],
```

Add `"bashReadPrefixes"` to `_LIST_KEYS` (alongside `"bashRewriteRules"`, `"bashCapPrefixes"`).

- [ ] **Step 4: Update consumers of the old key**

In `src/ccgate/run/cli.py`, change the gate `config.get("bashCapEnabled")` → `config.get("bashEnabled")`. In `scripts/b1b_ci_probe.py`, change the treatment config write `{"bashCapEnabled": True, ...}` → `{"bashEnabled": True, ...}`. In `tests/test_run_cli.py`, update the fixture in `test_bashcap_enabled_adds_bash_and_posttool_hook` to write `'{"bashEnabled": true}'`.

- [ ] **Step 5: Run config + run-cli tests**

Run: `python -m pytest tests/test_config.py tests/test_run_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/config.py src/ccgate/run/cli.py scripts/b1b_ci_probe.py tests/test_config.py tests/test_run_cli.py
git commit -m "feat(config): rename bashCapEnabled->bashEnabled; add bashReadPrefixes"
```

- [ ] **Step 7: USER/CI VERIFICATION — re-run the b1b probe under `bashEnabled`**

The b1b green run used `bashCapEnabled`, a key that no longer exists. Until re-run, F3 is unverified. After this branch is pushed and the workflows are dispatchable, run `gh workflow run b1b-bash-cap-integration.yml --ref trackb-b1a-f1-bash-read` and confirm `PROBE_BOTH_CLEAN=True`, `PROBE_TREATMENT_TRUNCATED=True`. Record here:

Evidence log (fill in): `PROBE_BOTH_CLEAN=____ PROBE_TREATMENT_TRUNCATED=____ run=____`

---

## Task 5: `run/policy.py` — Bash-read deny matcher (stateful)

**Files:**
- Modify: `src/ccgate/run/policy.py` (add `make_bash_read_deny_hook`)
- Test: `tests/test_run_policy.py`

**Interfaces:**
- Consumes: `strip_runner_prefixes`, `first_token` (shellcmd, Task 2); `path_is_ignored` (Task 3); `RunRecorder.append_event` (existing).
- Produces: `make_bash_read_deny_hook(patterns, reader_prefixes, recorder) -> BashReadDeny` — a stateful async callable with `.matcher_errors`, `.denies`, and `summary() -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_run_policy.py
import asyncio
from ccgate.run.policy import make_bash_read_deny_hook
from ccgate.run.record import RunRecorder


def _bash_hook(tmp_path, monkeypatch, patterns=("*.lock",),
               readers=("cat", "head", "tail", "less", "more", "sed", "awk")):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-b1a")
    return make_bash_read_deny_hook(list(patterns), list(readers), rec), rec


def _call(hook, command):
    inp = {"tool_name": "Bash", "tool_input": {"command": command}}
    return asyncio.run(hook(inp, "tuid", None))


def _is_deny(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_bash_deny_common_readers(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _is_deny(_call(hook, "cat foo.lock"))
    assert _is_deny(_call(hook, "head -100 foo.lock"))
    assert _is_deny(_call(hook, "sed -n 1,5p foo.lock"))


def test_bash_allow_non_reader_and_allowed_file(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _call(hook, "rm foo.lock") == {}          # not a reader — allow
    assert _call(hook, "git add foo.lock") == {}     # not a reader — allow
    assert _call(hook, "cat app.py") == {}           # reader, allowed file — allow


def test_bash_deny_whole_command_mixed_files(tmp_path, monkeypatch):
    hook, rec = _bash_hook(tmp_path, monkeypatch)
    out = _call(hook, "cat a.lock b.txt")
    assert _is_deny(out)
    ev = [e for e in _events(rec) if e.get("rule") == "F1" and e.get("surface") == "bash"]
    assert ev and ev[-1]["matched_token"] == "a.lock"


def test_bash_deny_redirection_and_runner_and_quotes(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _is_deny(_call(hook, "cat foo.lock > out.txt"))
    assert _is_deny(_call(hook, "sudo cat foo.lock"))
    assert _is_deny(_call(hook, 'cat "x.lock"'))          # surrounding quotes stripped
    assert _is_deny(_call(hook, "cat C:\\repo\\foo.lock"))  # backslash normalized


def test_reader_gate_is_first_token_equality_not_startswith(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _call(hook, "lessc styles.lock") == {}    # 'lessc' != 'less' — allow
    assert _call(hook, "catalog foo.lock") == {}     # 'catalog' != 'cat' — allow


def test_bash_matches_read_path_on_substring(tmp_path, monkeypatch):
    from ccgate.run.policy import path_is_ignored
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    # foo.lock.bak does NOT match *.lock in the Read predicate...
    assert path_is_ignored("foo.lock.bak", ["*.lock"]) is False
    # ...and the Bash path agrees (same predicate) — allow.
    assert _call(hook, "cat foo.lock.bak") == {}


def test_non_bash_passthrough(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "foo.lock"}}, "t", None)) == {}


def test_fail_open_increments_matcher_errors(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    # tool_input is a non-dict truthy value -> `(123 or {}).get(...)` raises AttributeError
    # inside the body -> caught, counted, {} returned (fail-open, not silent — spec §7).
    out = asyncio.run(hook({"tool_name": "Bash", "tool_input": 123}, "t", None))
    assert out == {}
    assert hook.matcher_errors == 1


def test_summary_shape(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    _call(hook, "cat foo.lock")
    s = hook.summary()
    assert s["rule"] == "F1" and s["surface"] == "bash"
    assert s["denies"] == 1 and s["matcher_errors"] == 0
```

Add the `_events` helper near the top of `tests/test_run_policy.py` if not present:

```python
import json
from pathlib import Path

def _events(rec):
    return [json.loads(l) for l in Path(rec.path).read_text(encoding="utf-8").splitlines()]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_run_policy.py -k "bash or reader_gate or fail_open or summary" -v`
Expected: FAIL — `make_bash_read_deny_hook` not defined.

- [ ] **Step 3: Implement the stateful matcher**

Add to `src/ccgate/run/policy.py`:

```python
from ccgate.run.shellcmd import first_token, strip_runner_prefixes

_COST_REASON = ("{tok} is listed in .contextignore and is excluded because reading it is "
                "expensive and rarely useful; skip it and continue.")


class BashReadDeny:
    """Stateful PreToolUse Bash callback: deny common readers of a .contextignore'd path.
    Best-effort efficiency, not a control (spec §1). Owns matcher_errors (spec §7)."""

    def __init__(self, patterns, reader_prefixes, recorder):
        self.patterns = patterns
        self.readers = set(reader_prefixes)   # first-token equality, not startswith (§Review)
        self.recorder = recorder
        self.denies = 0
        self.matcher_errors = 0

    async def __call__(self, input_data, tool_use_id, context) -> dict:
        try:
            if input_data.get("tool_name") != "Bash":
                return {}
            command = (input_data.get("tool_input") or {}).get("command", "")
            effective = strip_runner_prefixes(command)
            if first_token(effective) not in self.readers:
                return {}
            for tok in effective.split():
                tok = tok.strip("'\"")
                if path_is_ignored(tok, self.patterns):
                    self.denies += 1
                    self.recorder.append_event({
                        "type": "ccgate_event", "rule": "F1", "surface": "bash",
                        "command_prefix": command[:40],
                        "matched_token": tok,
                    })
                    return {"hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": _COST_REASON.format(tok=tok),
                    }}
            return {}
        except Exception:
            self.matcher_errors += 1   # increment FIRST, then return (spec §7)
            return {}

    def summary(self) -> dict:
        return {"type": "ccgate_event", "rule": "F1", "surface": "bash", "summary": True,
                "denies": self.denies, "matcher_errors": self.matcher_errors}


def make_bash_read_deny_hook(patterns, reader_prefixes, recorder) -> BashReadDeny:
    return BashReadDeny(patterns, reader_prefixes, recorder)
```

Note: the test records `matched_token` only; `matched_pattern` (which pattern fired) is added in Task 6 alongside the Read-path recording, where both surfaces gain the field together. (If you prefer, add `matched_pattern` here now by having `path_is_ignored` return the matching pattern — but that changes its signature; Task 6 keeps `path_is_ignored` pure and derives the pattern via a helper. Implement `matched_pattern` in Task 6.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_run_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/policy.py tests/test_run_policy.py
git commit -m "feat(run): F1 Bash-read deny matcher — first-token gate, fail-open counter"
```

---

## Task 6: `run/policy.py` — F1 recording on Read deny + `matched_pattern` on both surfaces

**Files:**
- Modify: `src/ccgate/run/policy.py` (`make_read_deny_hook`, add `matched_pattern` helper)
- Test: `tests/test_run_policy.py`

**Interfaces:**
- Produces: `make_read_deny_hook(patterns, recorder=None)` — records an F1 read event when a recorder is supplied; `first_matching_pattern(path, patterns) -> str | None`.
- Consumes: `RunRecorder.append_event`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_run_policy.py
from ccgate.run.policy import make_read_deny_hook, first_matching_pattern


def test_first_matching_pattern():
    assert first_matching_pattern("a/foo.lock", ["*.txt", "*.lock"]) == "*.lock"
    assert first_matching_pattern("a/app.py", ["*.lock"]) is None


def test_read_deny_records_event_when_recorder_given(tmp_path, monkeypatch):
    hook, rec = _bash_hook(tmp_path, monkeypatch)  # reuse recorder fixture
    # build a read hook sharing the same recorder:
    from ccgate.run.record import RunRecorder
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    r = RunRecorder("run-read")
    read_hook = make_read_deny_hook(["*.lock"], recorder=r)
    out = asyncio.run(read_hook({"tool_name": "Read", "tool_input": {"file_path": "a/foo.lock"}}, "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    ev = [e for e in _events(r) if e.get("rule") == "F1" and e.get("surface") == "read"]
    assert ev and ev[-1]["matched_pattern"] == "*.lock" and ev[-1]["matched_token"] == "a/foo.lock"


def test_read_deny_recorder_none_still_denies(tmp_path, monkeypatch):
    read_hook = make_read_deny_hook(["*.lock"])  # no recorder — B0 call site
    out = asyncio.run(read_hook({"tool_name": "Read", "tool_input": {"file_path": "a/foo.lock"}}, "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
```

Also extend the Task-5 Bash test to assert `matched_pattern`:

```python
def test_bash_deny_records_matched_pattern(tmp_path, monkeypatch):
    hook, rec = _bash_hook(tmp_path, monkeypatch)
    _call(hook, "cat foo.lock")
    ev = [e for e in _events(rec) if e.get("rule") == "F1" and e.get("surface") == "bash"]
    assert ev[-1]["matched_pattern"] == "*.lock"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_run_policy.py -k "matching_pattern or read_deny or matched_pattern" -v`
Expected: FAIL — `first_matching_pattern` / recorder param / `matched_pattern` field missing.

- [ ] **Step 3: Add the pattern helper, record in both hooks**

Add to `src/ccgate/run/policy.py`:

```python
def first_matching_pattern(path: str, patterns: list[str]) -> str | None:
    """The first pattern that makes path ignored, or None. Uses path_is_ignored per pattern
    so it agrees exactly with the deny decision."""
    for pat in patterns:
        if path_is_ignored(path, [pat]):
            return pat
    return None
```

Update `make_read_deny_hook` to accept `recorder=None` and record on deny:

```python
def make_read_deny_hook(patterns, recorder=None):
    async def _hook(input_data: dict, tool_use_id, context) -> dict:
        if input_data.get("tool_name") != "Read":
            return {}
        target = (input_data.get("tool_input") or {}).get("file_path", "")
        if target and path_is_ignored(target, patterns):
            if recorder is not None:
                recorder.append_event({
                    "type": "ccgate_event", "rule": "F1", "surface": "read",
                    "matched_pattern": first_matching_pattern(target, patterns),
                    "matched_token": target,
                })
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _COST_REASON.format(tok=target),
            }}
        return {}
    return _hook
```

In `BashReadDeny.__call__`, add `matched_pattern` to the recorded event:

```python
                    self.recorder.append_event({
                        "type": "ccgate_event", "rule": "F1", "surface": "bash",
                        "command_prefix": command[:40],
                        "matched_pattern": first_matching_pattern(tok, self.patterns),
                        "matched_token": tok,
                    })
```

(Note: the Read deny reason now uses the same cost-framed `_COST_REASON` — this replaces B0's "is listed in .contextignore" wording. Update any B0 test asserting the old reason string to match the cost-framed text.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_run_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ccgate/run/policy.py tests/test_run_policy.py
git commit -m "feat(run): record F1 on both surfaces (read+bash) with matched_pattern+token"
```

---

## Task 7: `run/cli.py` — wire two PreToolUse matchers

**Files:**
- Modify: `src/ccgate/run/cli.py` (`_build_options`, `_factory`, `run_task`)
- Test: `tests/test_run_cli.py`

**Interfaces:**
- Consumes: `make_read_deny_hook` (recorder), `make_bash_read_deny_hook` (Task 5/6), `BashCapHook` (existing), `compile_prefixes` (shellcmd).
- Produces: options dict with `hooks.PreToolUse = [read_deny, bash_read_deny?]` (raw callbacks) and `_factory` translating them to two `HookMatcher`s.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_run_cli.py
def test_bashenabled_wires_two_pretooluse_matchers(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / ".contextignore").write_text("*.lock\n", encoding="utf-8")
    (tmp_path / "config.json").write_text('{"bashEnabled": true}', encoding="utf-8")
    asyncio.run(run_task("t", enforce=True, cwd=tmp_path, client_factory=_FakeClient))
    opts = _FakeClient.last_options
    pre = opts["hooks"]["PreToolUse"]
    assert len(pre) == 2                      # read deny + bash-read deny
    assert "Bash" in opts["tools"]


def test_disabled_has_single_read_matcher(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / ".contextignore").write_text("*.lock\n", encoding="utf-8")
    asyncio.run(run_task("t", enforce=True, cwd=tmp_path, client_factory=_FakeClient))
    pre = _FakeClient.last_options["hooks"]["PreToolUse"]
    assert len(pre) == 1                      # read deny only
```

Update the factory-drift test to build two PreToolUse matchers:

```python
@pytest.mark.skipif(not _sdk_installed(), reason="claude-agent-sdk not installed")
def test_factory_builds_two_pretooluse_matchers():
    from ccgate.run.cli import _build_options, _factory
    from ccgate.config import load_config
    cfg = load_config(); cfg["bashEnabled"] = True
    # two raw PreToolUse callbacks -> two HookMatchers, must construct without raising:
    assert _factory(_build_options(["*.lock"], enforce=True, config=cfg, recorder=None)) is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_run_cli.py -k "two_pretooluse or single_read or factory" -v`
Expected: FAIL — `_build_options`/`_factory` build only one PreToolUse matcher (and signature differs).

- [ ] **Step 3: Rework `_build_options`/`_factory`/`run_task`**

In `src/ccgate/run/cli.py`. `_build_options` now returns a list of raw PreToolUse callbacks and a list of raw PostToolUse callbacks:

```python
def _build_options(patterns, enforce: bool, config: dict, recorder):
    from ccgate.run.policy import make_read_deny_hook, make_bash_read_deny_hook
    from ccgate.run.bashcap import BashCapHook
    from ccgate.run.shellcmd import compile_prefixes
    pre, post, tools = [], [], []
    if enforce:
        pre.append(("Read", make_read_deny_hook(patterns, recorder)))
        tools.append("Read")
        if config.get("bashEnabled"):
            tools.append("Bash")
            readers = compile_prefixes(config["bashReadPrefixes"])
            pre.append(("Bash", make_bash_read_deny_hook(patterns, readers, recorder)))
            post.append(("Bash", BashCapHook(
                compile_prefixes(config["bashCapPrefixes"]),
                config["bashCapHeadChars"], config["bashCapTailChars"],
                config["bashCapDebugLoopCalls"], recorder)))
    return {
        "hooks": {"PreToolUse": pre, "PostToolUse": post},
        "setting_sources": [], "tools": tools, "allowed_tools": list(tools),
    }
```

The hook objects live in the returned dict's `PreToolUse`/`PostToolUse` lists; `run_task` (below) collects their `summary()` at finish via `hasattr` — no globals, no function attributes. The read-deny closure has no `summary`, so it is skipped automatically.

`_factory` builds one `HookMatcher` per (matcher, callback):

```python
def _factory(options):
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
    hooks = {}
    pre = [HookMatcher(matcher=m, hooks=[cb]) for m, cb in options["hooks"]["PreToolUse"]]
    post = [HookMatcher(matcher=m, hooks=[cb]) for m, cb in options["hooks"]["PostToolUse"]]
    if pre:
        hooks["PreToolUse"] = pre
    if post:
        hooks["PostToolUse"] = post
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        hooks=hooks, setting_sources=options["setting_sources"],
        tools=options["tools"], allowed_tools=options["allowed_tools"]))
```

`run_task` passes the recorder into `_build_options`, and at finish writes the summary of any stateful hooks (bash-read deny, bashcap):

```python
async def run_task(task_prompt, *, enforce, cwd, client_factory, config=None):
    if config is None:
        config = load_config(str(cwd))
    patterns = load_contextignore(cwd)
    recorder = RunRecorder(make_run_id(str(cwd)))
    options = _build_options(patterns, enforce, config, recorder)
    summaries = [cb for _, cb in options["hooks"]["PostToolUse"]]
    summaries += [cb for m, cb in options["hooks"]["PreToolUse"] if hasattr(cb, "summary")]
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
    for h in summaries:
        recorder.append_event(h.summary())
    recorder.finish()
    return recorder.path
```

Update existing `test_run_cli.py` tests whose signatures changed (`_build_options(..., config=..., bashcap_hook=None)` → `_build_options(..., config=..., recorder=None)`; the old `bashcap_hook` param is gone).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_run_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ccgate/run/cli.py tests/test_run_cli.py
git commit -m "feat(run): wire two PreToolUse matchers (Read+Bash deny) under bashEnabled"
```

---

## Task 8: CI probe — Bash-read deny red→green

**Files:**
- Create: `tests/fixtures/run_b1a/task.txt`, `tests/fixtures/run_b1a/target_marker.txt`, `tests/fixtures/run_b1a/.contextignore`
- Create: `scripts/b1a_ci_probe.py`
- Create: `.github/workflows/b1a-bash-read-integration.yml`
- Test: `tests/test_b1a_probe.py`

**Interfaces:** none (integration). `evaluate(...)` + `_parse_bash_denies(...)` mirror the B1b probe, unit-tested in-process.

- [ ] **Step 1: Create the fixture (neutral filename)**

`tests/fixtures/run_b1a/target_marker.txt`:
```
FIXTURE-ONLY marker content: SENTINEL_B1A_READ_OK. Nothing real here.
```
`tests/fixtures/run_b1a/task.txt`:
```
Run exactly this bash command and then stop: cat target_marker.txt
```
`tests/fixtures/run_b1a/.contextignore`:
```
target_marker.txt
```

- [ ] **Step 2: Write the probe's pure decision logic + tests**

`tests/test_b1a_probe.py`:
```python
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from b1a_ci_probe import evaluate, _parse_bash_denies  # noqa: E402

MARK = "SENTINEL_B1A_READ_OK"


def test_green_when_baseline_reads_and_treatment_denies():
    assert evaluate(0, MARK, 0, "I'll skip it.", 1) == 0


def test_fails_if_baseline_did_not_read():
    assert evaluate(0, "no marker here", 0, "skipped", 1) != 0     # red state absent


def test_fails_if_treatment_not_denied():
    assert evaluate(0, MARK, 0, MARK, 0) != 0                      # no deny event


def test_fails_if_treatment_crashed():
    assert evaluate(0, MARK, 1, "traceback", 1) != 0


def test_parse_bash_denies_counts_f1_bash_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = tmp_path / "r.jsonl"
    rec.write_text(json.dumps({"type": "ccgate_event", "rule": "F1", "surface": "bash",
                               "summary": True, "denies": 2}) + "\n", encoding="utf-8")
    assert _parse_bash_denies(f"run record: {rec}\n") == 2
```

- [ ] **Step 3: Run to verify failure, then implement the probe**

Run: `python -m pytest tests/test_b1a_probe.py -q` → FAIL (`No module named 'b1a_ci_probe'`).

`scripts/b1a_ci_probe.py`:
```python
"""b1a_ci_probe.py — CI-only: prove the F1 Bash-read deny blocks `cat <ignored>`.

Baseline (bashReadPrefixes empty) lets cat run -> the fixture SENTINEL appears (the read
happened). Treatment (bashReadPrefixes set) denies -> the run record carries an F1 bash-deny
event and the SENTINEL is absent. Pass condition is the deterministic event (not stdout prose)
plus the baseline sentinel (spec §9; feedback-verify-mechanism-not-green)."""
import json, subprocess, sys
from pathlib import Path

MARK = "SENTINEL_B1A_READ_OK"
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "run_b1a"


def _parse_bash_denies(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("run record:"):
            p = Path(line.split(":", 1)[1].strip())
            if not p.exists():
                return 0
            for jl in p.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(jl)
                    if (e.get("rule") == "F1" and e.get("surface") == "bash"
                            and e.get("summary")):
                        return int(e.get("denies", 0))
                except (json.JSONDecodeError, ValueError):
                    pass
    return 0


def evaluate(base_rc, base_out, treat_rc, treat_out, treat_denies) -> int:
    both = base_rc == 0 and treat_rc == 0
    ok = both and (MARK in base_out) and (treat_denies > 0) and (MARK not in treat_out)
    print(f"PROBE_BOTH_CLEAN={both}")
    print(f"PROBE_BASELINE_READ={MARK in base_out}")
    print(f"PROBE_TREATMENT_DENIED={treat_denies > 0}")
    return 0 if ok else 1


def _run(readers_enabled: bool) -> tuple[int, str]:
    cfgdir = FIX / ".ccgate"
    cfg = cfgdir / "config.json"
    cfgdir.mkdir(exist_ok=True)
    body = {"bashEnabled": True, "bashReadPrefixes": (["cat"] if readers_enabled else [])}
    cfg.write_text(json.dumps(body), encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-m", "ccgate.dispatch", "run", "--task", "task.txt"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=str(FIX))
        return r.returncode, r.stdout + r.stderr
    finally:
        if cfg.exists():
            cfg.unlink()


def main() -> int:
    b_rc, b_out = _run(readers_enabled=False)
    t_rc, t_out = _run(readers_enabled=True)
    return evaluate(b_rc, b_out, t_rc, t_out, _parse_bash_denies(t_out))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verify probe logic passes**

Run: `python -m pytest tests/test_b1a_probe.py -q`
Expected: PASS (5).

- [ ] **Step 5: Write the workflow**

`.github/workflows/b1a-bash-read-integration.yml`:
```yaml
name: b1a-bash-read-integration
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
      - name: F1 Bash-read deny probe
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        run: python scripts/b1a_ci_probe.py
```

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/run_b1a scripts/b1a_ci_probe.py .github/workflows/b1a-bash-read-integration.yml tests/test_b1a_probe.py
git commit -m "test(run): B1a CI probe — Bash-read deny red->green"
```

- [ ] **Step 7: USER/CI VERIFICATION (agent cannot run this)**

Register the workflow on `main` (PR → verify → merge, like B0/B1b), then `gh workflow run b1a-bash-read-integration.yml --ref trackb-b1a-f1-bash-read`. Expected: `PROBE_BOTH_CLEAN=True`, `PROBE_BASELINE_READ=True`, `PROBE_TREATMENT_DENIED=True`.

Evidence log (fill in): `PROBE_BOTH_CLEAN=____ PROBE_BASELINE_READ=____ PROBE_TREATMENT_DENIED=____ run=____`

---

## Sequencing note

Tasks 2–7 are agent-runnable in-process (TDD). Task 1 is a git merge. Task 8's real Bash-deny proof is CI-only (on-desk Bash is org-sandboxed — spec §9). Order is strict 1→2→3→4→5→6→7→8. Task 4 Step 7 and Task 8 Step 7 are USER/CI-verified, not agent-runnable.
