"""ledger.py — net token accounting (§6). Pure computation; no I/O except pricing.json read."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ccgate.state import ccgate_home


def _load_price(model: str | None) -> float:
    """Return input price per token from ~/.ccgate/pricing.json. 0.0 if absent."""
    if not model:
        return 0.0
    home = ccgate_home()
    pricing_path = home / "pricing.json"
    if not pricing_path.exists():
        return 0.0
    try:
        table = json.loads(pricing_path.read_text(encoding="utf-8"))
        per_mtok = float(table.get(model, {}).get("input_per_mtok", 0.0))
        return per_mtok / 1_000_000
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0.0


def compute_net(session_data: dict, session_input_tokens: int = 0) -> dict:
    """Compute net token accounting from session data. tokens_avoided == 0 in Phase 1."""
    tokens_avoided = (session_data.get("ledger") or {}).get("tokens_avoided", 0)
    tokens_injected = sum(
        c.get("notice_bytes", 0) // 4
        for c in (session_data.get("tool_calls") or [])
        if c.get("notice_bytes", 0) > 0
    )
    net = tokens_avoided - tokens_injected
    price = _load_price(session_data.get("model"))
    return {
        "tokens_avoided": tokens_avoided,
        "tokens_injected": tokens_injected,
        "net": net,
        "net_usd": round(net * price, 6),
    }


def format_headline(session_data: dict) -> str:
    """Format net accounting headline for human display."""
    ledger = session_data.get("ledger") or {}
    net = ledger.get("net", 0)
    avoided = ledger.get("tokens_avoided", 0)
    injected = ledger.get("tokens_injected", 0)
    sign = "+" if net >= 0 else ""
    base = f"net {sign}{net:,} tok (avoided {avoided:,}, injected {injected:,})"
    if net < 0:
        return base.replace("tok", "tok ↑ self-cost exceeds savings", 1)
    return base


def assert_gate(session_data: dict, session_input_tokens: int) -> bool:
    """G6: tokens_injected <= 2% of session input AND net > 0."""
    ledger = session_data.get("ledger") or {}
    net = ledger.get("net", 0)
    injected = ledger.get("tokens_injected", 0)
    return net > 0 and injected <= 0.02 * session_input_tokens


def main() -> None:
    """CLI: python -m ccgate.ledger --assert <session.json> <input_tokens>"""
    args = sys.argv[1:]
    if "--assert" not in args:
        print("Usage: python -m ccgate.ledger --assert <session.json> <input_tokens>",
              file=sys.stderr)
        sys.exit(1)
    idx = args.index("--assert")
    try:
        session_file = args[idx + 1]
        input_tokens = int(args[idx + 2]) if idx + 2 < len(args) else 0
    except (IndexError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    session_data = json.loads(Path(session_file).read_text(encoding="utf-8"))
    result = compute_net(session_data, input_tokens)
    session_data["ledger"] = result
    headline = format_headline(session_data)
    if assert_gate(session_data, input_tokens):
        print(f"G6 PASS: {headline}")
        sys.exit(0)
    else:
        print(f"G6 FAIL: {headline}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
