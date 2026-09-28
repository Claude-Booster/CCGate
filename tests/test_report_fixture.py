from pathlib import Path

from ccgate.scripts.miss_audit import run_audit

FIXTURES = Path(__file__).parent / "fixtures" / "track_a_report"


def _paths():
    return [FIXTURES / "main.jsonl", FIXTURES / "subagents" / "agent-1.jsonl"]


def test_fixture_reproduces_known_counts():
    report = run_audit(_paths(), {})
    s = report["summary"]
    assert s["total_requests"] == 5
    assert s["total_misses"] == 2
    assert s["expected_rebuilds"] == 0
    assert s["hit_ratio"] == 0.6
    assert s["cache_read_rate"] == 0.25
    assert s["by_origin"] == {
        "main": {"requests": 3, "misses": 1},
        "subagent": {"requests": 2, "misses": 1},
    }
    assert s["tokens"] == {
        "grand_total_input": 404000,
        "cache_read": 100000,
        "cache_creation": 300000,
        "input": 4000,
        "output": 190,
    }
