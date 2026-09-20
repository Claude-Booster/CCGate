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

    def test_report_schema_fields_present(self):
        report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
        assert "sessions" in report
        assert "summary" in report
        assert "misses" in report
        assert "assumptions" in report
        assert "turns_remaining_est" in report["assumptions"]
        assert "turns_remaining_derivation" in report["assumptions"]

    def test_d2_compaction_not_in_avoidable_total(self):
        report = run_audit([FIXTURES / "d2_compaction.jsonl"], DEFAULTS)
        # D2.compaction must appear in the misses list (we detected it)
        d2_entries = [m for m in report["misses"] if m["cause"] == taxonomy.D2_COMPACTION]
        assert d2_entries, "D2.compaction should appear in misses list"
        # Its cost must NOT be included in avoidable_usd
        d1_cost = sum(
            m["cost_usd"] or 0 for m in report["misses"]
            if m["cause"] in taxonomy.D1_ALL
        )
        assert abs(report["summary"]["avoidable_usd"] - d1_cost) < 1e-9
        # Sanity: D2 cost itself should be zero (expected rebuild, not charged as avoidable)
        assert report["summary"]["avoidable_usd"] == d1_cost
