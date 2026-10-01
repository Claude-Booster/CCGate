#!/usr/bin/env python3
"""scan_readpath_candidates.py — agentless recon for Feature B (the scanner).

Walks a repo and reports which deny-rule FINDING-TYPES *exist* in it: lockfiles,
vendored/dependency dirs, build-output dirs, minified assets. It reads NO file
contents, makes NO API calls, and starts NO Claude Code session — it is a plain
filesystem walk, so running it over a client repo leaks nothing off the machine.

IMPORTANT — existence is not value. This tells you which finding-types occur, which
is the shipped *vocabulary*. It CANNOT tell you whether those files are in the read
path (a monorepo can hold 40k files under node_modules no session ever opens). Only
transcripts from real sessions on the repo show whether denying them saves tokens.
Vocabulary comes from existence; evidence of value comes from transcripts.

Usage:
    python scripts/scan_readpath_candidates.py <repo_root> [--sizes] [--samples N]

--sizes    also sum bytes under each matched dir (slower; walks the pruned dirs).
--samples  how many example paths to show per finding-type (default 3).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Conservative v1 vocabulary. "generated clients" is deliberately EXCLUDED: a
# generated API client is often exactly what the model must read, and a bad
# proposal costs a broken session while a missed one costs only some tokens.
LOCKFILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "npm-shrinkwrap.json",
    "Cargo.lock", "poetry.lock", "Pipfile.lock", "composer.lock",
    "Gemfile.lock", "go.sum",
}
VENDORED_DIRS = {"node_modules", "vendor", "bower_components", ".venv", "venv"}
BUILD_DIRS = {"dist", "build", ".next", "out", "coverage", ".nuxt", ".svelte-kit"}
MIN_SUFFIXES = (".min.js", ".min.css")

# Finding-type -> (list of relative paths, total_bytes).
Findings = dict


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def scan(root: Path, with_sizes: bool) -> dict[str, dict]:
    findings: dict[str, dict] = {
        name: {"paths": [], "bytes": 0}
        for name in ("lockfile", "vendored-dir", "build-dir", "min-asset")
    }

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)

        # Match + PRUNE vendored/build dirs: record the dir, don't descend into it.
        pruned = []
        for d in list(dirnames):
            bucket = None
            if d in VENDORED_DIRS:
                bucket = "vendored-dir"
            elif d in BUILD_DIRS:
                bucket = "build-dir"
            if bucket:
                full = here / d
                rel = full.relative_to(root)
                findings[bucket]["paths"].append(str(rel))
                if with_sizes:
                    findings[bucket]["bytes"] += _dir_size(full)
                pruned.append(d)
        # prune so we never walk INTO node_modules et al.
        dirnames[:] = [d for d in dirnames if d not in pruned]

        for fn in filenames:
            if fn in LOCKFILES:
                full = here / fn
                rel = full.relative_to(root)
                findings["lockfile"]["paths"].append(str(rel))
                if with_sizes:
                    try:
                        findings["lockfile"]["bytes"] += full.stat().st_size
                    except OSError:
                        pass
            elif fn.endswith(MIN_SUFFIXES):
                full = here / fn
                rel = full.relative_to(root)
                findings["min-asset"]["paths"].append(str(rel))
                if with_sizes:
                    try:
                        findings["min-asset"]["bytes"] += full.stat().st_size
                    except OSError:
                        pass
    return findings


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", help="repo root to scan")
    ap.add_argument("--sizes", action="store_true", help="sum bytes per match (slower)")
    ap.add_argument("--samples", type=int, default=3, help="example paths per type")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    findings = scan(root, args.sizes)

    print(f"# read-path candidate scan: {root}")
    print("# EXISTENCE ONLY -- not proof of value. Which types occur = the vocabulary;")
    print("# whether denying them saves tokens must come from real-session transcripts.\n")

    any_found = False
    for name, data in findings.items():
        count = len(data["paths"])
        if count == 0:
            print(f"{name:14s}  0")
            continue
        any_found = True
        size = f"  ({_human(data['bytes'])})" if args.sizes else ""
        print(f"{name:14s}  {count}{size}")
        for p in data["paths"][: args.samples]:
            print(f"                 - {p}")
        if count > args.samples:
            print(f"                 ... and {count - args.samples} more")

    if not any_found:
        print("\n(no finding-types present -- like CCGate itself, this repo has nothing to deny)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
