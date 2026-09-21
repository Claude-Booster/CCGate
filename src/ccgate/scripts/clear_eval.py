"""clear_eval.py — measure token savings from clear_tool_uses_20250919."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ccgate.model import get_model_spec
from ccgate.transcript import read_transcript


def _error(msg: str) -> tuple[int, str]:
    return 1, msg


def _run_eval(transcript_path: str, json_out: bool) -> tuple[int, str]:
    """Run the evaluation; return (returncode, output_string)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _error(
            "clear_eval: ANTHROPIC_API_KEY environment variable not set.\n"
            "Set it with: export ANTHROPIC_API_KEY=sk-..."
        )

    try:
        import anthropic
        if anthropic is None:
            raise ImportError("anthropic is None")
    except (ImportError, TypeError):
        return _error(
            "clear_eval: anthropic package not installed.\n"
            "Install with: pip install anthropic"
        )

    path = Path(transcript_path)
    if not path.exists():
        return _error(f"clear_eval: transcript not found: {path}")

    requests = read_transcript(path)
    if not requests:
        return _error("clear_eval: no assistant turns found in transcript")

    last_model = requests[-1].model_id

    # Reconstruct messages list from transcript entries
    messages: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_type = entry.get("type")
            msg = entry.get("message") or {}
            content = msg.get("content") or ""
            # Keep structured content (including tool_use/tool_result blocks) intact
            # so clear_tool_uses_20250919 has actual tool-use content to evaluate
            if not isinstance(content, (str, list)):
                content = str(content)
            if entry_type == "user":
                messages.append({"role": "user", "content": content})
            elif entry_type == "assistant":
                messages.append({"role": "assistant", "content": content})

    if not messages:
        return _error("clear_eval: no messages found in transcript")

    client = anthropic.Anthropic(api_key=api_key)

    baseline_resp_obj = client.beta.messages.count_tokens(
        model=last_model,
        messages=messages,
    )
    original_before: int = baseline_resp_obj.input_tokens

    cleared_resp = client.beta.messages.count_tokens(
        model=last_model,
        messages=messages,
        context_management={"type": "clear_tool_uses_20250919"},
    )
    cleared_tokens: int = cleared_resp.input_tokens

    raw_savings = original_before - cleared_tokens
    savings_tokens = max(0, raw_savings)
    savings_pct = (savings_tokens / original_before * 100) if original_before > 0 else 0.0

    model_spec = get_model_spec(last_model)
    savings_usd = savings_tokens * model_spec.rate_in if savings_tokens > 0 else 0.0

    if json_out:
        out = json.dumps({
            "original_tokens": original_before,
            "cleared_tokens": cleared_tokens,
            "savings_tokens": savings_tokens,
            "savings_pct": round(savings_pct, 2),
            "savings_usd_approx": round(savings_usd, 6),
        })
    else:
        out = (
            f"original : {original_before:,} tokens\n"
            f"cleared  : {cleared_tokens:,} tokens\n"
            f"savings  : {savings_tokens:,} tokens  ({savings_pct:.1f}%)  ~${savings_usd:.4f}"
        )

    return 0, out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure token savings from clear_tool_uses_20250919"
    )
    parser.add_argument("--transcript", metavar="PATH", required=True,
                        help="Path to a JSONL transcript file")
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Emit JSON output instead of human-readable")
    args = parser.parse_args(argv)

    rc, out = _run_eval(args.transcript, args.json_out)
    if rc == 0:
        print(out)
    else:
        print(out, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
