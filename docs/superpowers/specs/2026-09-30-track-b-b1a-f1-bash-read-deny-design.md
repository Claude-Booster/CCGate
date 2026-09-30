# Track B / B1a — F1 richer `.contextignore` + Bash-read deny (design)

**Status:** design (brainstormed 2026-09-30). Successor to B0 (Read-only F1 deny) and B1b (F3 Bash output truncation). Precedes B1c (F2 read cache).

## 1. Goal

Close the completeness gap where a `.contextignore`'d file — meant to be kept out of context to save tokens — can still be read through Bash (`cat`/`head`/`sed`…) even though F1 denies it via the Read tool. Today F1 only matches the Read tool; once Bash is available (B1b/F3), the file's contents can re-enter context via a shell reader. B1a adds a PreToolUse **Bash** deny for the common readers, and upgrades `.contextignore` matching so the Read and Bash paths share one predicate.

### This is a token-efficiency feature, not a security control

`.contextignore` exists to stop the model burning tokens on a lockfile or a build artifact — not to protect secrets. The model is not an adversary; it is an agent taking the obvious path, and blocking the obvious path is sufficient. B1a denies the *common* readers, **best-effort**, and never claims a file cannot be read. If the spec said "cannot," someone would eventually rely on it.

### Success criteria (testable)

1. A Track B run that would `cat`/`head`/`tail`/`less`/`more`/`sed`/`awk` a `.contextignore`'d file has that command **denied** with a *cost-excluded* reason — proven red→green in CI.
2. Commands that only *name* an ignored file without reading its contents (`rm`, `git add`, `ls`) are **not** denied.
3. The Read path and Bash path use the **same** `path_is_ignored` predicate (one source of truth).
4. The F1 ledger records **both** surfaces (Read deny and Bash deny), so Track A's F1 total is not silently understated.

### Limitations (documented, not failures)

The Bash matcher **misses** `python -c "print(open('x').read())"`, `grep . x`, `od -c x`, `base64 x`, `while read l; do … done < x`, `tar cf - x`, `sh -c 'cat x'`, redirection-based reads, and anything else that reads a file without a recognized reader prefix. Enumerating the ways a shell reads a file is a non-goal — B1b established that parsing shell grammar is a trap. **On a miss, do nothing:** a creative reader wastes some tokens, which is exactly the cost that existed before F1. No escalation, no attempt to "harden."

**Quoted paths with spaces:** `cat "my file.lock"` is whitespace-split into `"my` and `file.lock"`. B1a strips surrounding quotes per token (§5), so a glob like `*.lock` still matches `file.lock` — but a `.contextignore` pattern that *itself contains a space* will not match, because tokenization has already split the path. A documented miss, acceptable for a best-effort feature.

**B1a's real value** is closing the case where the model reaches for `cat`/`head`/`sed` on a literal path out of habit — the obvious path. It is *not* a meaningful reduction in the number of ways a file can reach context. The success criteria are scoped to the habitual-reader case deliberately; the plan must not drift into `grep`/redirection handling.

## 2. Mechanism

A **PreToolUse hook with `matcher="Bash"`** returning `permissionDecision: "deny"`. This is the same deny primitive B0 uses for Read; B1a adds a second matcher on the same event. The two PreToolUse matchers (`Read`, `Bash`) target different tools, so a given tool call matches exactly one — the SDK runs multiple matchers on one event concurrently, and there is no ordering or interaction hazard here.

## 3. Scope

**In:** shared `run/shellcmd.py`; PreToolUse Bash-read deny; richer `.contextignore` matching (shared predicate); F1 recording for both surfaces; the `bashCapEnabled` → `bashEnabled` config rename.

**Out (deferred):**
- **F2 read cache** → B1c (efficiency feature, different success criteria — a savings measurement, not a deny-correctness proof).
- **1h-TTL env pin on the runner** → standalone one-line commit, verified by a trace (not spec material). See `project-track-b-ttl-decision`.
- **`!` negation (last-match-wins)** — would change `path_is_ignored` from a pure "any match → ignored" predicate to ordered semantics, touching every test, for an edge use. YAGNI.
- **Global `~/.claude/.contextignore` (second location)** — convenience, unrelated to the gap; adds merge/precedence complexity.
- **B1b runner-strip retrofit** — B1b's `command_matches` is defeated by `sudo pytest`/`time pytest` (a missed *truncation*, not a missed deny — costs some tokens once, no correctness consequence). Retrofit when B1b is next touched; B1a leaves a discoverable comment at `bashcap.command_matches` and tests `strip_runner_prefixes` against B1b's inputs so the retrofit is a one-line call change.

## 4. Architecture & modules

### `run/shellcmd.py` (new — shared pure helpers)
- `strip_runner_prefixes(command: str) -> str` — recursively strip a leading runner and return the effective command. Handles `sudo` (with flags, e.g. `sudo -u foo`), `env` (with `FOO=1` assignments, and bare `env`), `time`, `command`, `nice`. Recurses (`sudo env FOO=1 cat x`) under a small iteration cap. Stops there — deeper handling is shell-grammar parsing, a non-goal.
- **Relocated** from `bashcap.py`: `compile_prefixes`, `command_matches`, `_METACHARS` (pure move; `bashcap.py` imports them back). Rationale: the shared module must not depend on the feature module; runner-strip belongs next to the prefix matching it complements.

### `run/policy.py` (extend — the one source of truth)
- `load_contextignore(root)` — explicit CRLF-safe parse (`.strip()`); patterns kept literal.
- `path_is_ignored(path, patterns)` — extended, still a pure **"any match → ignored"** predicate:
  - Normalize the input path `\` → `/` **inside this function**, so every caller (Read and Bash) inherits it.
  - Existing glob semantics (fnmatch on full path OR basename) retained.
  - **Trailing-`/` directory rule:** a pattern `X/` matches iff `X` is a complete path segment, computed as `("/" + normalized_path + "/")` containing `"/" + X + "/"`. Matches the dir and everything beneath at any depth (`node_modules/foo`, `src/node_modules/bar`); excludes substrings (`mynode_modules/x`).
- `make_read_deny_hook(patterns, recorder=None)` — recorder is **optional and defaults to `None`** so B0's existing call site and tests keep working unchanged; when a recorder is provided it records an F1 Read-deny event on deny (see §6). Otherwise unchanged; inherits richer matching for free.
- `make_bash_read_deny_hook(patterns, reader_prefixes, recorder)` (new) — a **stateful** PreToolUse Bash callback (like `BashCapHook`) that owns the `matcher_errors` counter and the bash deny count and exposes `summary()`; algorithm in §5.

### `config.py`
- **Rename** `bashCapEnabled` → `bashEnabled` (master switch: grants Bash + registers *both* the F1 Bash-read deny and F3 truncation). `bashCap*` (head/tail/debug-loop/prefixes) remain the **truncation knobs beneath it**.
- Add `bashReadPrefixes` (default `["cat","head","tail","less","more","sed","awk"]`), validated via `compile_prefixes` (metachar rejection at load), list-merge like `bashCapPrefixes`.
- Each feature **self-gates on its prefix list**: empty `bashCapPrefixes` → no truncation; empty `bashReadPrefixes` (or empty `.contextignore`) → no bash deny.

### `run/cli.py` wiring
When `enforce` **and `bashEnabled`**: register `PreToolUse = [HookMatcher("Read", [read_deny]), HookMatcher("Bash", [bash_read_deny])]` and `PostToolUse = [HookMatcher("Bash", [bashcap])]`. When not `bashEnabled`: `PreToolUse = [HookMatcher("Read", [read_deny])]` only, no Bash tool. `_factory` translates the raw-callback dict to `ClaudeAgentOptions`; the two-PreToolUse-matcher shape is exercised by the factory-drift test (the seam untested in B0).

## 5. Detection algorithm (Bash-read deny)

```
strip runner prefixes → effective command
if not command_matches(effective, reader_prefixes):  return {}   # not a reader → allow
tokens = effective.split()                                        # whitespace only; no shell parse
for tok in tokens:
    tok = tok.strip("'\"")                                        # strip surrounding quotes: cat "x.lock"
    if path_is_ignored(tok, patterns):                           # normalizes \→/, trailing-/ rule
        record F1 bash-deny event (matched_pattern, matched_token)
        return deny(cost-excluded reason)
return {}
```

**Any token matches → deny the whole command** (not partial — partial denial isn't a thing). This correctly handles `cat foo.lock > out.txt` (deny; `>`/`out.txt` are just non-matching tokens) and `cat a.lock b.txt` (deny the whole command; `matched_token` records `a.lock`). The token scan passes raw tokens straight to `path_is_ignored`, so a Windows path `cat C:\repo\foo.lock` normalizes and matches `*.lock`.

**Deny-reason wording (both surfaces):** state that the file is `.contextignore`'d and **excluded because it is expensive and rarely useful** — never "blocked"/"forbidden"/"access denied." A model told "excluded because expensive" moves on; one told "blocked" routes around.

## 6. Recording (honest ledger)

Both F1 surfaces record to the run record so Track A's F1 total is complete:
- Read deny: `{"type":"ccgate_event","rule":"F1","surface":"read","matched_pattern":<pat>,"matched_token":<file_path>}`
- Bash deny: `{"type":"ccgate_event","rule":"F1","surface":"bash","command_prefix":<first 40 chars>,"matched_pattern":<pat>,"matched_token":<tok>}`
- Run summary: F1 deny counts per surface **plus `matcher_errors`** (see §7).

`matched_pattern` (which pattern fired) **and** `matched_token` (which file triggered) are both recorded — a `cat a.lock b.txt` deny must say which file, not just that a pattern matched.

Backfilling Read-deny recording is in scope: a ledger with Bash denies but not Read denies would misreport F1's total **downward** (an I3/I7 honesty defect), and doing it later leaves a window where Track A's F1 numbers are silently partial.

## 7. Error handling

Fail-open, like `BashCapHook`: the matcher body is wrapped so any exception returns `{}` — a best-effort efficiency feature must never crash a run; a miss is cheaper than a crash. **But not silently:** increment a `matcher_errors` counter. Silent wrong answers are this project's whole failure history; the counter turns a broken matcher into a visible signal instead of an invisible miss.

**Owner:** `matcher_errors` lives on the stateful Bash-read matcher object (§4), the same place `BashCapHook` keeps its per-run counters; `run_task` calls the matcher's `summary()` at finish to append the F1 bash summary event (deny count + `matcher_errors`) to the `RunRecorder`, exactly as F3 does. **Order:** the `except` handler increments `matcher_errors` *first*, then returns `{}` — so a caught exception always produces both, never one without the other.

Empty `.contextignore` or empty `bashReadPrefixes` → no-op `{}`. Malformed/absent command → no match → allow. `bashReadPrefixes` with regex metachars → `ValueError` at config load via `compile_prefixes`.

## 8. Data flow

Bash call: model emits `command` → PreToolUse Bash matcher → `strip_runner_prefixes` → `command_matches` gate → tokenize → any `path_is_ignored` → deny (record) or `{}`. Read call: unchanged path through the same `path_is_ignored`, now also recording on deny. One predicate, two surfaces.

## 9. Testing

**In-process (agent-runnable, no auth):**
- `strip_runner_prefixes`: `sudo cat x`, `sudo -u foo cat x`, `env FOO=1 cat x`, bare `env cat x`, `time cat x`, `command cat x`, `nice cat x`, recursive `sudo env FOO=1 cat x`, iteration cap — **plus B1b's inputs** (`sudo pytest`, `env CI=1 cargo test`, `time npm test`) so the B1b retrofit is later a one-line call change.
- `path_is_ignored` richer: trailing-`/` segment match (`node_modules/` hits `node_modules/foo`, `src/node_modules/bar`; misses `mynode_modules/x`), `\`→`/` normalization, existing globs and `#` comments.
- `make_bash_read_deny_hook`: `cat <ignored>`→deny; `cat <allowed>`→allow; `head -100 <ignored>`→deny; `sed -n 1,5p <ignored>`→deny; `rm <ignored>`→allow (not a reader); `cat a.lock b.txt`→deny-whole (`matched_token`==`a.lock`); `cat foo.lock > out.txt`→deny; `sudo cat <ignored>`→deny; **quoted `cat "x.lock"` vs `*.lock`**→deny (surrounding quotes stripped); non-Bash→`{}`; **substring `foo.lock.bak` vs `*.lock`** asserting token-match == Read-match (same predicate); **backslash `cat C:\repo\foo.lock` vs `*.lock`**→deny; fail-open on an injected exception asserts **both** hold — returns `{}` **and** `matcher_errors` incremented (except handler increments before returning).
- `make_read_deny_hook`: with `recorder=None` (B0 call site) denies and its existing tests pass unchanged; with a recorder, writes the F1 read event on deny.
- Recording: Read deny and Bash deny both write their event; summary counts both surfaces + `matcher_errors`.
- Wiring: `bashEnabled` registers two PreToolUse matchers + PostToolUse; disabled → Read matcher only, no Bash tool. **Factory-drift: `_factory` builds real `ClaudeAgentOptions` with two PreToolUse `HookMatcher`s** (the B0-untested seam).
- Config: `bashReadPrefixes` default + metachar rejection; `bashEnabled` rename consumed by `run_task`.

**CI-only (`scripts/b1a_ci_probe.py` + workflow + neutral-named sentinel fixture; both runs exit 0):**
- **RED** — baseline (bash deny absent) `cat`s the fixture; `SENTINEL_B1A_READ_OK` appears in output (the read happened).
- **GREEN** — treatment (bash deny present); pass condition is the **deterministic F1 bash-deny event in the run record**, with sentinel-absent as corroboration.
- Fixture uses a **neutral filename** (never `*secret*`/`*credential*`) — Claude Code's sensitive-filename guard hard-denies such reads regardless of config and would confound the baseline. See `feedback-verify-mechanism-not-green`.

## 10. Deferred / follow-ups

F2 read cache (B1c); 1h-TTL env pin (standalone commit); `!` negation; global `.contextignore`; B1b runner-strip retrofit (with the discoverable comment landed in B1a).

## 11. Branch reconciliation (execution note, not spec content)

B1a branches from `trackb-b1b-bash-cap` (it needs B0 + B1b). But `trackb` lacks plan1's B0 CI fixes (fixture rename `target_secret`→`target_marker`, spec updates, the bypass add/revert that nets to no-bypass, `.gitignore`). **The plan's first task must merge/cherry-pick plan1's B0 fixes into the B1a branch**, or the b1a probe rediscovers the sensitive-filename guard. The b1b probe must also re-run once under the `bashEnabled` key (the existing green run used `bashCapEnabled`, a config that no longer exists after the rename — until re-run, F3 is unverified).

## 12. Review focus (for the plan's reviewer)

- The two-PreToolUse-matcher seam actually constructs under the real SDK (factory-drift test), and Read vs Bash never both fire on one call.
- `path_is_ignored` is genuinely one predicate — the Bash token scan and the Read path give identical answers on the same path (the substring test pins this).
- Deny wording is cost-framed on both surfaces (no "blocked"/"forbidden").
- The F1 ledger is complete: Read deny recording is actually wired, not just Bash.
- `matcher_errors` is incremented on fail-open, not swallowed silently.
- Best-effort limitations are documented and the misses (`python -c`, redirection, etc.) are deliberate, not bugs.
