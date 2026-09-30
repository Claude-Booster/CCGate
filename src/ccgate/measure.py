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


def derive_n(t1: float, t2: float) -> int | None:
    """N per arm from the two enforced variance runs. d = |t1-t2| / mean on tokens/task.
    d<=10% -> 5; 10<d<=25% -> 9; d>25% -> None (unmeasurable at feasible cost) (spec §3)."""
    mean = (t1 + t2) / 2
    if mean == 0:
        return 5
    d = abs(t1 - t2) / mean
    if d <= 0.10:
        return 5
    if d <= 0.25:
        return 9
    return None


def _completed(arm: list[dict]) -> list[dict]:
    return [r for r in arm if r.get("complete")]


def decide(baseline: list[dict], enforced: list[dict]) -> dict:
    """Pre-declared decision rule (spec §6). Computed from data, not adjustable after seeing it."""
    b_done, e_done = _completed(baseline), _completed(enforced)
    b_rate = len(b_done) / len(baseline) if baseline else 0.0
    e_rate = len(e_done) / len(enforced) if enforced else 0.0
    if b_rate <= 0.5 or e_rate <= 0.5:
        return {"verdict": "VOID",
                "reason": f"completion too low (baseline {b_rate:.0%}, enforced {e_rate:.0%})",
                "baseline_rate": b_rate, "enforced_rate": e_rate}
    baseline_min = min(r["tokens_total"] for r in b_done)
    enforced_median = statistics.median(r["tokens_total"] for r in e_done)
    if enforced_median < baseline_min and e_rate >= b_rate:
        verdict = "BUILD_B1C"
        reason = f"enforced median {enforced_median} < baseline min {baseline_min} and completion held"
    else:
        verdict = "NO_BUILD"
        reason = (f"enforced median {enforced_median} not below baseline min {baseline_min}"
                  if enforced_median >= baseline_min else
                  f"enforced completion {e_rate:.0%} < baseline {b_rate:.0%}")
    return {"verdict": verdict, "reason": reason, "baseline_min": baseline_min,
            "enforced_median": enforced_median, "baseline_rate": b_rate, "enforced_rate": e_rate}


def classify_completion(done_ok: bool, returncode: int, max_turns_hit: bool) -> tuple[bool, str]:
    """Map a run outcome to (complete, reason). A done run is 'ok' regardless; otherwise a
    turn-cap hit and a crash are distinguished from a plain give-up (spec §2)."""
    if done_ok:
        return True, "ok"
    if max_turns_hit:
        return False, "turn_cap"
    if returncode != 0:
        return False, "error"
    return False, "gave_up"


def tokens_injected_present(bashcap_source: str) -> bool:
    """True if the F3 event in bashcap source records the tokens_injected field (done-condition
    part 2). A cheap textual check — the harness's pytest run is the real done gate (spec §4)."""
    return "tokens_injected" in bashcap_source
