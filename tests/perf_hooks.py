"""perf_hooks.py — G7 gate: post_tool computation p95 < 50 ms over 500 payloads.

Run as a standalone script: python tests/perf_hooks.py
Measures in-process computation time only (excludes Python startup overhead).
"""
import io
import json
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Must set CCGATE_HOME before importing post_tool so state.py resolves correctly
_tmpdir = tempfile.mkdtemp(prefix="ccgate_perf_")
os.environ["CCGATE_HOME"] = _tmpdir

import ccgate.hooks.post_tool as post_tool  # noqa: E402

N = 500
SEED = 42
TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "Agent"]
P95_LIMIT_MS = 50.0


def _run_one(payload: dict) -> float:
    raw = json.dumps(payload)
    out_buf = io.StringIO()
    t0 = time.perf_counter()
    with patch("sys.stdin", io.StringIO(raw)), patch("sys.stdout", out_buf):
        post_tool.main()
    return (time.perf_counter() - t0) * 1000


def main() -> int:
    rng = random.Random(SEED)
    latencies: list[float] = []
    for i in range(N):
        tool = rng.choice(TOOLS)
        chars = rng.randint(100, 100_000)
        payload = {
            "session_id": "perf_session",
            "tool_name": tool,
            "tool_response": "x" * chars,
        }
        latencies.append(_run_one(payload))

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(0.95 * N)]
    p99 = latencies[int(0.99 * N)]
    p_max = latencies[-1]
    print(
        f"G7 latency ({N} payloads) — "
        f"p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms  max={p_max:.1f}ms"
    )
    if p95 < P95_LIMIT_MS:
        print(f"G7 PASS: p95={p95:.1f}ms < {P95_LIMIT_MS}ms")
        return 0
    print(f"G7 FAIL: p95={p95:.1f}ms >= {P95_LIMIT_MS}ms", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
