"""statusline.py — live cache budget line for Claude Code's status bar.

Reads a JSON payload from stdin (from Claude Code's statusLine hook),
writes a single ≤ COLUMNS line to stdout.
"""
import json
import os
import sys

_SHORT_NAMES: dict[str, str] = {
    "claude-opus-5":             "Opus 5",
    "claude-sonnet-5":           "Sonnet 5",
    "claude-fable-5-1":          "Fable 5.1",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "claude-opus-4":             "Opus 4",
    "claude-sonnet-4":           "Sonnet 4",
    "claude-haiku-4":            "Haiku 4",
}

_BAR_FILL  = "▓"
_BAR_EMPTY = "░"
_BAR_WIDTH = 10


def _model_label(payload: dict) -> str:
    model_id = (payload.get("model") or {}).get("id", "")
    for key, label in _SHORT_NAMES.items():
        if model_id.startswith(key):
            return label
    if model_id:
        return model_id.split("-")[-1].capitalize()
    return "?"


def _context_bar(used_pct: float) -> str:
    filled = round(_BAR_WIDTH * used_pct / 100)
    return _BAR_FILL * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)


def _safe(payload: dict, *keys, default=None):
    """Navigate nested dict keys; return default on missing/None at any level."""
    v = payload
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k)
        if v is None:
            return default
    return v


def _persist_snapshot(payload: dict) -> None:
    """Write payload snapshot to ~/.ccgate/sessions/{session_id}-statusline.json."""
    from ccgate.state import ccgate_home
    sid = payload.get("session_id")
    if not sid:
        return
    dest = ccgate_home() / "sessions" / f"{sid}-statusline.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, dest)


def render(payload: dict, config: dict) -> str:
    """Build the status line string. Always ≤ COLUMNS chars, no newlines."""
    cols = int(os.environ.get("COLUMNS", "80"))

    pc = payload.get("prompt_cache") or {}
    cw = payload.get("context_window") or {}

    model_label  = _model_label(payload)
    used_pct     = float(cw.get("used_percentage") or 0)
    bar          = _context_bar(used_pct)
    cost         = _safe(payload, "cost", "total_cost_usd", default=0.0)
    hit_ratio    = pc.get("hit_ratio")
    requests     = int(pc.get("requests") or 0)
    warm         = pc.get("warm", True)
    recache      = pc.get("recache_tokens_if_cold") or 0
    caching_obs  = pc.get("caching_observed", True)

    low_ratio = (
        hit_ratio is not None
        and hit_ratio < config.get("hitRatioFloor", 0.85)
        and requests >= config.get("minRequestsForRatio", 10)
    )

    # Suppress the inline cache% when the escalation already shows it (avoids duplication).
    cache_part = ""
    if hit_ratio is not None and not low_ratio:
        cache_part = f"  ·  cache {hit_ratio*100:.0f}%"

    base = f"[{model_label}] {bar} {used_pct:.0f}%{cache_part}  ·  ${cost:.2f}"

    escalation = ""

    if not caching_obs and requests >= 3:
        escalation = "  NO CACHE REPORTED"

    elif used_pct >= config.get("compactAdviseAt", 0.80) * 100:
        escalation = "  → /compact"

    elif (not warm and isinstance(recache, (int, float))
          and recache > config.get("coldRecacheWarnTokens", 100_000)):
        tok_k = f"{recache/1000:.0f}K" if recache < 1_000_000 else f"{recache/1e6:.1f}M"
        escalation = f"  COLD — next turn re-caches {tok_k}"

    elif low_ratio:
        miss_causes = pc.get("miss_causes")
        suffix = ""
        if isinstance(miss_causes, dict) and miss_causes:
            top = sorted(miss_causes.items(), key=lambda kv: -kv[1])[:2]
            suffix = " (" + ", ".join(f"{c} ×{n}" for c, n in top) + ")"
        escalation = f"  cache {hit_ratio*100:.0f}% ↓{suffix}"

    line = base + escalation
    return line[:cols]


def main() -> None:
    # Windows cp1252 cannot encode ▓░ — force UTF-8 so block chars render correctly
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from ccgate.config import load_config
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        payload = {}
    config = load_config()
    print(render(payload, config))
    _persist_snapshot(payload)
