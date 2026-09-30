"""measure.py — pure metrics + decision logic for the Track B enforcement A/B (spec)."""
from __future__ import annotations

import json
import statistics
from pathlib import Path


def extract_run_metrics(record_path) -> dict:
    """Parse a ccgate run record (JSONL) into experiment metrics. Pure — no SDK, no subprocess.
    misses_per_1k is a cache-miss approximation (requests that created cache beyond the first),
    secondary and not gated (spec §2)."""
    tokens_total = 0
    turns = 0
    f1_fires = 0
    f3_truncations = 0
    complete_marker = False
    cache_creations = 0
    for line in Path(record_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            u = msg["usage"]
            gti = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0))
            tokens_total += gti + u.get("output_tokens", 0)
            turns += 1
            if u.get("cache_creation_input_tokens", 0) > 0:
                cache_creations += 1
            continue
        if ev.get("type") == "ccgate_event" and not ev.get("summary"):
            if ev.get("rule") == "F1":
                f1_fires += 1
            elif ev.get("rule") == "F3":
                f3_truncations += 1
            continue
        if ev.get("type") == "ccgate_run_end":
            complete_marker = True
    misses = max(0, cache_creations - 1)   # the first request always creates cache
    misses_per_1k = (misses / turns * 1000) if turns else 0.0
    return {"tokens_total": tokens_total, "turns": turns, "f1_fires": f1_fires,
            "f3_truncations": f3_truncations, "misses_per_1k": misses_per_1k,
            "complete_marker": complete_marker}
