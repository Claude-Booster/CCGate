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
        # Reject truly absolute paths AND rooted paths (e.g. /etc/passwd on Windows
        # is not "absolute" per ntpath.isabs in Python 3.12+ but still dangerous).
        if os.path.isabs(path) or path.startswith("/") or path.startswith("\\"):
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
