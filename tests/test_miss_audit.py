from pathlib import Path

from ccgate import taxonomy
from ccgate.config import DEFAULTS
from ccgate.scripts.miss_audit import attribute_miss, run_audit
from ccgate.transcript import Request, Usage, classify_requests

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def _req(idx, model, inp, read, create, h1=0, h5=0, ts="2026-09-19T10:00:00.000Z"):
    return Request(idx, ts, model, Usage(
        input_tokens=inp,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=create,
        ephemeral_1h_input_tokens=h1,
        ephemeral_5m_input_tokens=h5,
    ))


class TestClassifyRequests:
    def test_first_turn_is_hit(self):
        reqs = [_req(0, "claude-sonnet-5", 5000, 0, 5000, h1=5000)]
        result = classify_requests(reqs)
        assert result[0][1].value == "HIT"

    def test_large_miss_detected(self):
        reqs = [
            _req(0, "claude-sonnet-5", 5000, 0, 5000, h1=5000),
            _req(1, "claude-sonnet-5", 5000, 0, 5000, h1=5000),
        ]
        result = classify_requests(reqs)
        assert result[1][1].value == "MISS"

    def test_small_miss_below_threshold_is_hit(self):
        reqs = [
            _req(0, "claude-sonnet-5", 5000, 0, 5000, h1=5000),
            _req(1, "claude-sonnet-5", 5200, 4900, 300, h1=300),
        ]
        result = classify_requests(reqs)
        assert result[1][1].value == "HIT"

    def test_expected_rebuild_not_counted_as_miss(self):
        req = _req(0, "claude-sonnet-5", 1200, 0, 1200, h1=1200)
        req.is_expected_rebuild = True
        result = classify_requests([req])
        assert result[0][1].value == "EXPECTED_REBUILD"


class TestAttributeMiss:
    def test_model_switch_detected(self):
        prev = _req(0, "claude-sonnet-5", 5000, 0, 5000)
        curr = _req(1, "claude-opus-5",   5000, 0, 5000)
        assert attribute_miss(curr, prev, ttl=3600) == taxonomy.D1_MODEL_SWITCH

    def test_same_model_returns_unclassified(self):
        prev = _req(0, "claude-sonnet-5", 5000, 0, 5000)
        curr = _req(1, "claude-sonnet-5", 5000, 0, 5000)
        cause = attribute_miss(curr, prev, ttl=3600)
        assert cause == taxonomy.D1_UNCLASSIFIED

    def test_ttl_expired_detected(self):
        prev = _req(0, "claude-sonnet-5", 5000, 0, 5000,
                    ts="2026-09-19T10:00:05.000Z")
        curr = _req(1, "claude-sonnet-5", 5000, 0, 5000,
                    ts="2026-09-19T10:10:35.000Z")
        assert attribute_miss(curr, prev, ttl=300) == taxonomy.D1_TTL_EXPIRED


class TestRunAudit:
    def test_model_switch_fixture(self):
        report = run_audit([FIXTURES / "d1_model_switch.jsonl"], DEFAULTS)
        causes = [m["cause"] for m in report["misses"] if m["cause"] != taxonomy.D2_COMPACTION]
        assert taxonomy.D1_MODEL_SWITCH in causes

    def test_clean_session_has_no_avoidable_misses(self):
        report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
        avoidable = [m for m in report["misses"] if m["cause"] in taxonomy.D1_ALL
                     and m["cause"] != taxonomy.D1_UNCLASSIFIED]
        assert sum(m["count"] for m in avoidable) == 0

    def test_report_has_raw_tokens_and_no_dollars_or_turns(self):
        report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
        s = report["summary"]
        # dollar/turns layer is gone
        assert "total_usd" not in s
        assert "avoidable_usd" not in s
        assert "assumptions" not in report
        # raw tokens present and non-negative (by-construction bound)
        assert set(s["tokens"]) == {
            "grand_total_input", "cache_read", "cache_creation", "input", "output"
        }
        assert all(v >= 0 for v in s["tokens"].values())
        assert 0.0 <= s["cache_read_rate"] <= 1.0
        assert 0.0 <= s["hit_ratio"] <= 1.0
        for m in report["misses"]:
            assert "cost_usd" not in m

    def test_empty_paths_all_zero_no_crash(self):
        # Also the zero-cache guard: with no requests, cache_read + cache_creation == 0,
        # so cache_read_rate must be 0.0, not a ZeroDivisionError.
        report = run_audit([], DEFAULTS)
        s = report["summary"]
        assert s["total_requests"] == 0
        assert s["hit_ratio"] == 1.0
        assert s["tokens"]["grand_total_input"] == 0
        assert s["cache_read_rate"] == 0.0

    def test_d2_compaction_present_but_not_in_avoidable_d1(self):
        # Replaces the old dollar-based test: D2.compaction is detected and listed,
        # but is not one of the avoidable D1 causes.
        report = run_audit([FIXTURES / "d2_compaction.jsonl"], DEFAULTS)
        causes = {m["cause"] for m in report["misses"]}
        assert taxonomy.D2_COMPACTION in causes
        assert taxonomy.D2_COMPACTION not in taxonomy.D1_ALL
