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
        print(f"startup: {tokens:,} tokens  [{label} <= {cap:,}]")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
