# `shape` + native deny rules — design sketch

**Status:** design only — nothing built. Dated 2026-10-01.

Goal: have `shape` produce native scoped `permissions.deny` rules, so F1's job (block
expensive reads) runs inside the live Claude Code session with no hook, plugin, or SDK loop.
Held to the same evidence bar as the rest of the week.

Context — the finding that motivates this: a scoped `permissions.deny` rule (`Read(**/*.lock)`)
was verified to block **both** the `Read` tool and a Bash `cat` in a live session
(cross-surface), while a control file in the same dir read fine. `permissions.deny` is a
permission *rule*, not a *hook* — a different policy surface than the org-blocked hooks, which
is why it works where F1's hooks didn't. See memory `project_native_deny_finding`.

---

## 0. The decision: which feature? (settle before specs)

Two candidates:

- **Feature A — translation layer:** `.contextignore` patterns → `Read()` deny rules.
- **Feature B — scanner (recommended):** scan the repo, find lockfiles / `node_modules` /
  `dist` / generated clients, and propose **both** the `.contextignore` entries **and** the
  deny rules. This is the existing **D4 check extended**.

**Recommendation: build B first, demote A to downstream.** Reasons:

1. **A produces nothing on the repos it's for.** The repos where this pays off (React Native
   work, a monorepo) likely have **no `.contextignore`** — nobody wrote one. A translates an
   artifact that isn't there.
2. **B dissolves the parity problem for the common case.** When the *scanner* is the source,
   each finding is a canonical type (`lockfile`, `vendored-dir`, `min-js`, `generated-client`)
   rendered into *both* a `.contextignore` line and a verified `Read()` rule from a **small
   fixed vocabulary** — no `fnmatch`→gitignore translation of arbitrary user input. The parity
   burden shrinks from "any pattern a user could write" to "~6 finding-types, each with one
   live anchor."
3. **A becomes secondary/best-effort** — useful only for a repo that already has a hand-written
   `.contextignore`, which is the minority case.

The rest of this doc keeps the translation-layer design (§1–§2) because B reuses its parity
discipline and its rule vocabulary; it's just no longer the primary.

### Feature B spec notes

**Failure mode: a wrong proposal denies something the model needed.** The scanner proposes
*ignoring* files; a false positive breaks a session, and the cost is asymmetric — a *missed*
candidate costs some tokens, a *bad* candidate costs a broken session. So the shipped vocabulary
must be **narrow and conservative**. Safe: lockfiles, `node_modules`, `dist`/`build` output.
**Risky, exclude from v1: "generated clients"** — a generated API client is often exactly what
the model needs to read to call the API correctly. Prefer under-proposing.

**The scanner needs a corpus — and this repo isn't it (by construction).** On CCGate the scanner
finds nothing. Two decisions about *how* to do the recon:

1. **Do it agentless.** Enumerating lockfiles / `node_modules` / `dist` / vendored dirs is a
   filesystem walk — a plain script, **no Claude Code session, no API calls, nothing read into a
   context window.** Cheaper, and it sidesteps the "a Claude session reading client source"
   concern entirely (nothing leaves the machine). Script: `scripts/scan_readpath_candidates.py`.
   A read-only *agent* recon over a **client** repo is different in kind from running over CCGate;
   avoid it. If a repo must be touched by an agent later, prefer one that's ours.

2. **Existence ≠ value — and this is the same gap as F1's zero fires, one level up.** The walk
   tells you which finding-types *exist*; it canNOT tell you whether those files are in the **read
   path** — a monorepo can hold 40,000 files under `node_modules` that no session ever opens. The
   existence scan gives the **vocabulary**; only **transcripts from real sessions on that repo**
   tell you whether denying them saves anything. The spec must state this split plainly:
   *vocabulary comes from existence, evidence of value comes from transcripts.*

---

## 1. The core is a pure function, and the parity test is its spec

```python
def deny_rules_from_contextignore(patterns: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """patterns -> (emittable Read() rules, [(skipped_pattern, reason)]). Pure; no I/O."""
```

The acceptance test isn't "does it produce nice-looking rules" — it's **behavioral
equivalence to `path_is_ignored` over a corpus**:

```python
# for every FILE path p in the corpus, the two engines must agree:
assert path_is_ignored(p, patterns) == any(read_glob_matches(p, r) for r in rules)
# any disagreement is a BUG, not a style difference.
```

**The trap this guards against, and its limit.** Two engines are in play and **neither is
Claude Code's**: `path_is_ignored` uses `fnmatch` (`*` crosses `/`) + basename fallback + the
trailing-slash segment rule; `read_glob_matches` must mirror Claude Code's **gitignore-style**
globs (`*` within a segment, `**` across). So the parity test guards *our two* implementations
against each other — and **it can pass while both diverge from the real engine** (the same
shape as the Track A fixture-tautology: a green where both sides are wrong together). That's why
it needs **one live anchor per pattern class** (deny-test–style fixture, run in a real session)
to validate `read_glob_matches` against Claude Code's actual matcher *once*. After that, the
unit parity test guards regressions.

**Each live anchor is TWO observations, not one** — Read *and* Bash. Cross-surface blocking is
verified only for the `Read(**/*.lock)` *file* pattern so far; a *directory* pattern
(`**/node_modules/**/*`) intercepting `cat node_modules/x/y.js` is **unverified** and may depend
on how Claude Code resolves the path from the command.

## 2. The translation table (only what's provably equivalent)

| `.contextignore` class | `path_is_ignored` behavior | Emitted `Read()` rule |
|---|---|---|
| `node_modules/` (trailing slash, dir) | `/node_modules/` as a contiguous segment, anywhere, + everything beneath | `Read(**/node_modules/**/*)` — see note |
| `*.lock` (extension glob) | fnmatch full-path/basename → any `.lock` anywhere | `Read(**/*.lock)` — verified live (both surfaces) |
| `pnpm-lock.yaml` (bare basename) | basename match anywhere (+ exact full path) | `Read(**/pnpm-lock.yaml)` |
| `src/**/*.log`, `build/*` (slash **and** wildcard) | fnmatch where `*` crosses `/` — no clean gitignore equivalent | **SKIPPED + reported** |
| `!keep.lock` (negation) | treated as a literal pattern (§3: no negation) | **SKIPPED + reported** |

**Directory-rule suffix — partly settled (2026-10-01).** Recorded project guidance (shape's
`denyReads` check) required directory patterns to end `/**/*` rather than `/**`, so the directory
stays **listable**. The live anchor only *partly* confirms this: for the **Glob tool**, both
suffixes behave identically — denied files are filtered out either way, non-denied siblings stay
visible, no `/**` vs `/**/*` difference (see Facts below). So for the Glob tool the guidance's
premise is **unreproduced, not disproven** — it may have been about `ls`-via-**Bash** (untested,
broken nested shell → user), or it may have been wrong; the shell anchor settles it. Until then,
emit `/**/*` as the conservative default — it's the suffix that can only be *more* permissive on
the listing question, so it's at worst equal to `/**`. Parity wrinkle stands: `path_is_ignored`
matches the directory node itself (`path="node_modules"` → True), but `/**/*` does not — treat
"read the dir node" as out of scope in the parity corpus.

The skipped classes aren't hand-waved — the generator **refuses to emit** anything it can't
prove equivalent and returns them in `skipped`, so `shape` can print *"3 patterns couldn't be
safely translated: …"*. A silently-wrong deny rule is worse than an un-translated one. Two known
parity quirks to document, not hide: a file literally named `node_modules` at root, and one
literally named `!keep.lock`.

## 3. Scoped-only, enforced in code

- The generator **asserts** every rule it emits contains a path argument, and **refuses** a
  match-everything glob (`Read(**)`, `Read(**/**)`) — that's as cache-destroying as a bare `Read`.
- `shape` additionally **scans the existing deny list** and flags any entry that is a bare tool
  name (`"Read"`, `"Bash"`, `"WebFetch"`) or a match-all glob, since those invalidate the
  prompt-cache prefix — the exact cost F1 exists to save.

## 4. It's a config write → next session (say so, like the env block)

`shape` output must read like the `ENABLE_PROMPT_CACHING_1H` write does:

> Wrote 4 deny rule(s) to `.claude/settings.json`. **Takes effect in your next Claude Code
> session** (not the current one).

**Resolved by test (2026-10-01):** an unknown top-level key in `settings.json` (tried
`ccgateGenerated`) is **tolerated** — file loads, no error, no warning, and the existing deny
rule still fires alongside it. So a marked/sidecar region for "rules shape generated" does **not**
risk a rejected settings file (the bad failure mode). **Residual unknown:** whether Claude Code
*strips* the unknown key if it ever rewrites the file itself — test before relying on round-trip
persistence.

**Retraction gap (flagged, not solved):** simplest v1 is dedup-union — add generated rules,
never clobber the user's manual deny entries. The cost: removing a pattern won't auto-remove its
rule next run (no marker in a JSON array to identify "ours"). The sidecar key above is the
candidate fix now that rejection is ruled out; same shape as the list-merge "can't empty the
defaults via project config" gotcha on record.

## 5. Gate it behind an explicit flag, not the default `--fix`

Writing *permission rules* changes what the agent may read — more consequential than writing env
vars. So a separate opt-in (`shape --emit-deny-rules` / a `shape rules` subcommand) with a
confirm step, not folded into `shape --fix` silently.

## 6. The caveat travels *with* the feature

On **this** repo, there is effectively nothing to deny in the real read path (F1 fired 0× in the
A/B — see memory `project_track_b_measurement`). `shape` should say exactly that:

> 0 of the generated rules match any path under this repo. This feature pays off on a repo with
> lockfiles / `node_modules` in the read path — build the evidence there, not here.

The feature is correct; the evidence that it *pays off* is still owed, and only a lockfile-heavy
repo can supply it. (This is also the argument for Feature B: on a no-`.contextignore` monorepo,
the scanner is what actually produces anything.)

**Recon results (2026-10-01).**
- **Real repos: two-for-two empty.** `scan_readpath_candidates.py` found ZERO finding-types on
  both CCGate and `ccmem` — both Python/docs repos. The scanner works correctly; these repos
  simply have nothing to deny. **This is F1's zero-fires lesson one level up:** the feature keeps
  having nothing to act on in the repos in reach.
- **Dummy repo: instrument validated, value NOT.** A synthetic `dummy-node-repo` (monorepo shape:
  root + nested `node_modules`, root + nested lockfiles, `dist`/`build`, `vendor`, a `.min.js`
  outside build dirs, plus real `src/`) was scanned: all four finding-types detected correctly,
  nesting caught, pruning works, real source not flagged. **But this proves the INSTRUMENT, not
  the value** — I planted the files to match the vocabulary, so "scanner finds what I planted" is
  tautological (the Track A fixture-tautology shape). It does NOT clear the spec-hold bar, whose
  purpose is a *real* demonstrated target.

**Recommendation: do NOT write the Feature B spec until the recon finds something on a REAL**
JS/node repo (React Native work / a JS monorepo — `node_modules`/lockfiles actually in the read
path). Status upgraded from "scanner untested" to "scanner verified correct, waiting only on a
real corpus." The sketch stands as the reasoned design; the spec waits on real evidence.

---

## Facts established by running it (2026-10-01)

- Scoped `Read(**/*.lock)` blocks **both** Read tool and Bash `cat` (cross-surface) — one file
  pattern only.
- Control file in same dir reads fine → it's the pattern, not a directory block.
- `python -c "print('X'*20000)"` returned FULL (no native truncation) → F3 is non-redundant.
- Unknown top-level settings key tolerated (no reject/warn; deny still fires).
- `claude -p` runs headless from the Bash tool (earlier "can't" was stale recall).

### Directory-pattern anchors (deny-dir-test fixture, 2026-10-01)

- **Dir rule covers reads beneath (Read surface): YES.** `Read vendored/sub/deep.js` denied
  under both `Read(**/vendored/**)` and `Read(**/vendored/**/*)`.
- **Deny filters the Glob TOOL, not just Read.** Control proves Glob works (`**/*.txt` →
  `outside.txt`), yet the denied `deep.js` is filtered out (`**/*.js` → 0; `vendored/**` → 0).
  Non-denied siblings stay visible.
- **No observable `/**` vs `/**/*` difference for the Glob tool** — `deep.js`/`vendored/**`
  hidden under both. So the recorded `denyReads` premise ("`/**/*` keeps it listable, `/**`
  breaks it") is **unreproduced, not disproven** for the Glob tool in this version (2.1.286) —
  suffix looks *free* here. **Caveat:** the guidance may have meant `ls`-via-**Bash** (untested,
  broken nested shell), or it may have been wrong; the shell anchor below settles which.

### Shell anchors (deny-dir-test, user's interactive session, 2026-10-01)

- **`ls vendored` → `sub` (RESOLVED, the clean win).** Under `Read(**/vendored/**/*)` the directory
  **lists** (you can see `sub/` exists) while file reads inside are denied. Reconciles with the
  Glob-tool result: `Glob vendored/**` → 0 because it enumerates *files* (denied); `ls` returns
  *directory entries* (not a file read), so it's allowed. **Net: `/**/*` denies contents but keeps
  structure navigable — the ideal, and the recorded guidance holds in spirit. Suffix decision
  SETTLED: emit `/**/*`.**
- **Dir rule blocks Bash `cat` beneath it (strongly indicated).** `cat vendored/sub/deep.js` was
  denied; Read of the same path returns the deny message. Wording "denied at the permission prompt"
  leaves a sliver of doubt vs. a declined Bash-approval prompt. Airtight discriminator if wanted:
  `cat outside.txt` (not under vendored/) — allowed ⇒ rule did it; denied ⇒ it was the Bash prompt.

## Open question still outstanding (interactive-only → user)

- Does Claude Code's **own settings-writer strip** an unknown key (`ccgateGenerated`) when *it*
  rewrites `settings.json`? The key *survives passively* (nothing has rewritten the file). The real
  test needs a Claude-Code-triggered write — an interactive `/permissions` add, then re-open the
  file. **Verified NOT doable headlessly** (no `claude config set` CLI — `config` is treated as a
  prompt), so this one is genuinely user-only. Decides whether the sidecar marker (→ retraction)
  is viable.

---

**If built (Feature B):** clean superpowers unit (brainstorm ≈ done here → spec → plan → Native
execute). Spine = the fixed finding-type → (`.contextignore` line, `Read()` rule) vocabulary,
each type with one two-surface live anchor; reuse §1's parity harness for any hand-written
`.contextignore` that Feature A later translates.
