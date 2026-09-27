from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    rate_in: float          # USD per input token
    write_multiplier: float # cache write cost as multiple of rate_in
    read_multiplier: float  # cache read cost as multiple of rate_in
    window_tokens: int      # context window in tokens


# Hand-maintained pricing table. Last updated: 2026-09-19.
# Source: console.anthropic.com/settings/billing
# Add new models here when Anthropic publishes them.
PRICING: dict[str, ModelSpec] = {
    "claude-opus-5":             ModelSpec(15e-6,  1.25, 0.10, 200_000),
    "claude-sonnet-5":           ModelSpec(3e-6,   1.25, 0.10, 200_000),
    "claude-fable-5-1":          ModelSpec(3e-6,   1.25, 0.10, 200_000),
    "claude-haiku-4-5-20251001": ModelSpec(0.8e-6, 1.25, 0.10, 200_000),
    "claude-opus-4":             ModelSpec(15e-6,  1.25, 0.10, 200_000),
    "claude-sonnet-4":           ModelSpec(3e-6,   1.25, 0.10, 200_000),
    "claude-haiku-4":            ModelSpec(0.8e-6, 1.25, 0.10, 200_000),
}

_FALLBACK = ModelSpec(3e-6, 1.25, 0.10, 200_000)

DEFAULT_WINDOW_TOKENS: int = 200_000  # used by shape.py for G3 budget calculation


def get_model_spec(model_id: str) -> ModelSpec:
    """Return ModelSpec for model_id. Falls back to Sonnet-class defaults for unknowns."""
    if model_id in PRICING:
        return PRICING[model_id]
    # Prefix match for versioned variants (e.g. "claude-sonnet-5-20260101")
    for key in sorted(PRICING, key=len, reverse=True):
        if model_id.startswith(key):
            return PRICING[key]
    return _FALLBACK


def resolve_ttl(payload: dict) -> int:
    """Return session cache TTL in seconds from a status-line payload.

    Reads prompt_cache.ttl (explicit override from Claude Code settings).
    Falls back to 300 — the conservative API-key/cloud default.
    TTL inference from transcript ephemeral_*_input_tokens is done in transcript.py.
    """
    pc = payload.get("prompt_cache") or {}
    ttl = pc.get("ttl")
    if isinstance(ttl, (int, float)) and ttl > 0:
        return int(ttl)
    return 300
