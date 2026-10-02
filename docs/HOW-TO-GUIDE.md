# CCGate — A Plain-English How-To Guide

*For anyone who uses Claude Code in VSCode and wants it to run cheaper and faster — no technical background needed.*

---

## 1. What is CCGate, in one sentence?

CCGate is a little helper that checks how Claude Code is set up and points out things that are quietly wasting your time and money — then offers to fix the easy ones.

**A everyday analogy:** think of it like a fuel-efficiency check for your car. Your car still runs without it, but a quick inspection finds the under-inflated tyre and the roof-rack you forgot to take off — the things making you burn more fuel than you need to. CCGate does that for Claude Code.

---

## 2. Why would I care? (What's actually being wasted)

When you chat with Claude, every message carries along a lot of background text — your project files, your settings, your past messages. That background is measured in **tokens**. More tokens means:

- **Higher cost** (you pay per token), and
- **Slower replies** (there's more for Claude to read each time).

Three common, invisible ways tokens get wasted:

1. **Claude re-reads the same background over and over.** There's a "memory" feature (a *cache*) that avoids this — but it switches off after a few idle minutes unless it's set up correctly. CCGate can turn the longer-lasting setting on.
2. **Huge files get pulled in that nobody needed.** Things like a 500 KB "lock file" or a giant minified script can swallow a big chunk of your budget in a single read. CCGate spots these and suggests keeping them out.
3. **Your setup files are bloated.** Overly long instruction files or too many tools loaded at once eat into the budget before you've even typed anything.

You don't need to understand *why* each of these matters — CCGate names them for you in plain words.

---

## 3. One-time setup

You only do this once. Open the **Terminal** in VSCode (top menu → *Terminal* → *New Terminal*), then copy-paste this and press Enter:

```
pip install -e .
```

That installs the `ccgate` command. If it worked, typing `ccgate` on its own will show a short list of what it can do.

> If you see an error, that's usually a Python-not-installed issue — ask whoever set up your machine, or skip to section 8.

---

## 4. The three things you'll actually use

Everything below is typed into the VSCode Terminal. You can always just copy-paste.

### A. "Check my setup" — `ccgate shape`

```
ccgate shape
```

This looks over your Claude Code configuration and prints a list of suggestions, each with a plain-English reason. Nothing is changed — it only *looks* and *tells you*. Think of it as the inspection report.

You'll see lines like:
- *"This setting would let Claude's memory last an hour instead of 5 minutes."*
- *"This file is very large and rarely useful to read whole — consider keeping it out."*
- *"Your instructions file is longer than recommended."*

### B. "Fix the easy ones for me" — `ccgate shape --fix` then `--apply`

```
ccgate shape --fix
```

This **proposes** a set of safe fixes and shows you exactly what it would change — but still doesn't touch anything yet. Read the list. If you're happy:

```
ccgate shape --apply
```

Now it makes the changes.

> **Important:** config changes take effect the **next time you start Claude Code**, not instantly. So after applying, close and reopen Claude Code (or start a new session) for them to kick in.

### C. "Where did my tokens go?" — `ccgate audit`

```
ccgate audit
```

This reads your recent Claude Code session history and reports what *caused* the waste — for example, how often the memory/cache was missed and how many tokens that cost. It's the "where's my money going" report.

---

## 5. The large-file warning (new) — and why it never touches anything

When you run `ccgate shape`, you may see a warning like:

> `pnpm-lock.yaml is 512 KB (~131,000 tokens, approx) and rarely useful to read whole. Consider adding Read(**/pnpm-lock.yaml) to permissions.deny — not auto-applied; add it yourself if you want. (Suggestion is based on file size, not evidence the file is read.)`

In plain terms: *"There's a big file here. If Claude ever reads the whole thing, it'll cost a lot. Here's a one-line rule you could add to tell Claude to skip it. We're only suggesting — you decide."*

Two things worth knowing:

- **It will never add that rule for you.** This warning is deliberately "advice only." Some big files (like a library's source code) are occasionally *worth* reading, so CCGate won't block them behind your back. It just points and suggests.
- **It's based on size, not proof.** The warning fires because the file is large, not because anyone confirmed Claude keeps reading it. Treat it as a prompt to think, not a command to obey.

If you *do* want to act on it, copy the suggested `Read(...)` line into your project's `.claude/settings.json` under `permissions` → `deny`. (Or ask a technical teammate — it's a one-line edit.)

---

## 6. How to read the results

Each suggestion has a severity marker:

- **`x` (error):** something that's clearly costing you — worth fixing.
- **`!` (warning):** worth a look; often an easy win.
- **info:** just context, nothing to do.

A good routine: run `ccgate shape` now and then (say, when a project feels sluggish or pricey), apply the safe fixes, restart Claude Code, and get on with your day.

---

## 7. What CCGate does *not* do (honest limits)

- **It's not magic and not a monitor.** It checks when you ask it to; it doesn't run constantly in the background watching you.
- **It can't make Claude smarter** — only leaner. It reduces waste; it doesn't change answers.
- **Some fixes need a restart** to take effect (see section 4B).
- **The large-file suggestions are advice, not proof** (section 5).
- **There's an advanced "enforcement" mode** (`ccgate run`) that actively blocks wasteful reads during an automated task. It's experimental, runs *outside* the normal editor, and is really for technical users — most people never need it. You can safely ignore it.

---

## 8. Mini-glossary (for when a word is unfamiliar)

- **Token:** the unit Claude reads and bills in — very roughly, a few characters of text. More tokens = more cost and more time.
- **Context window:** the total amount of text Claude can hold in mind at once. A budget. Waste fills it up faster.
- **Cache (the "memory"):** a feature that avoids re-reading the same background every message. Works best when it's set to last an hour instead of the default few minutes — one of the fixes CCGate offers.
- **Lock file / minified bundle:** big machine-generated files in software projects. Useful to the computer, almost never useful for Claude to read in full.
- **`.claude/settings.json`:** the configuration file CCGate inspects and (with your OK) tweaks.

---

## 9. The 30-second version

1. `ccgate shape` — see what's wasteful.
2. `ccgate shape --fix` then `ccgate shape --apply` — fix the safe stuff.
3. Restart Claude Code so the fixes take effect.
4. `ccgate audit` — see where tokens went.
5. Big-file warnings are *suggestions you choose to act on*, never automatic.

That's it. You don't need to understand the internals — CCGate's whole job is to tell you, in plain words, what's worth changing.
