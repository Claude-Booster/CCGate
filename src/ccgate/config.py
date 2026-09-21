import json
import os
import re
import sys
from pathlib import Path

# Schema-derived defaults — single source of truth.
DEFAULTS: dict = {
    "hitRatioFloor": 0.85,
    "minRequestsForRatio": 10,
    "coldRecacheWarnTokens": 100_000,
    "compactAdviseAt": 0.80,
    "unboundedOutputTokens": 10_000,
    "maxNoticesPerSession": 4,
    "bigFileLines": 500,
    "staleTimeMs": 600_000,
    "staleFiles": 8,
    "staleTokenRatio": 0.10,
    "readCacheEnabled": False,
    "contextignoreEnabled": False,
    "bashRewriteEnabled": False,
    "pinCacheTtl": True,
    "enforceToolDeferral": True,
    "toolCountJumpThreshold": 30,
    "pinnedModel": None,
    "pinnedEffort": None,
    "skillListingBudgetFraction": 0.01,
    "bashRewriteRules": [],
    "startupTokenCap": 12000,
    "otelPort": 4318,
    "digestMaxPaths": 500,
}

_RANGE: dict[str, tuple] = {
    "hitRatioFloor":              (0.0, 1.0),
    "minRequestsForRatio":        (1, 10_000),
    "coldRecacheWarnTokens":      (0, 10_000_000),
    "compactAdviseAt":            (0.0, 1.0),
    "unboundedOutputTokens":      (0, 10_000_000),
    "maxNoticesPerSession":       (0, 1_000),
    "bigFileLines":               (0, 1_000_000),
    "staleTimeMs":                (0, 86_400_000),
    "staleFiles":                 (0, 10_000),
    "staleTokenRatio":            (0.0, 1.0),
    "skillListingBudgetFraction": (0.0, 1.0),
    "startupTokenCap": (1, 200_000),
    "otelPort":        (1024, 65535),
    "digestMaxPaths":  (1, 10_000),
    "toolCountJumpThreshold": (0, 10_000),
}

def load_config(cwd: str | None = None) -> dict:
    """Load merged config: global ~/.ccgate/config.json + optional project .ccgate/config.json.

    Unknown keys are ignored and a warning is printed to stderr.
    Out-of-range scalar values fall back to their default.
    Arrays (bashRewriteRules) are merged (project appends to global).
    """
    cfg = dict(DEFAULTS)
    _merge_file(cfg, Path.home() / ".ccgate" / "config.json")
    if cwd:
        _merge_file(cfg, Path(cwd) / ".ccgate" / "config.json")
    # Environment overrides: CCGATE_HIT_RATIO_FLOOR → hitRatioFloor
    for key in list(DEFAULTS.keys()):
        if key == "bashRewriteRules":
            continue  # list type not parseable from a single env var
        env_key = "CCGATE_" + _to_upper_snake(key)
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        try:
            val = type(DEFAULTS[key])(raw)
        except (ValueError, TypeError):
            print(f"ccgate: env {env_key}={raw!r} could not be converted — using default",
                  file=sys.stderr)
            continue
        lo, hi = _RANGE.get(key, (None, None))
        if lo is not None and not (lo <= val <= hi):
            print(f"ccgate: env {env_key}={raw!r} out of range [{lo}, {hi}] — using default",
                  file=sys.stderr)
            continue
        cfg[key] = val
    return cfg

def _merge_file(cfg: dict, path: Path) -> None:
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    for key, val in raw.items():
        if key not in DEFAULTS:
            print(f"ccgate: unknown config key '{key}' in {path} — ignored", file=sys.stderr)
            continue
        if key == "bashRewriteRules" and isinstance(val, list):
            cfg[key] = cfg[key] + val
            continue
        lo, hi = _RANGE.get(key, (None, None))
        if lo is not None and not (lo <= val <= hi):
            print(f"ccgate: '{key}' value {val!r} out of range [{lo}, {hi}] — using default", file=sys.stderr)
            continue
        cfg[key] = val

def _to_upper_snake(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).upper()
