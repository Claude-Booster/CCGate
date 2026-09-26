from pathlib import Path

from ccgate import taxonomy
from ccgate.config import DEFAULTS
from ccgate.scripts.miss_audit import attribute_miss, is_subagent_transcript, run_audit
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


def test_is_subagent_transcript_detects_subagents_dir(tmp_path):
    main = tmp_path / "session.jsonl"
    sub = tmp_path / "subagents" / "agent-abc123.jsonl"
    sub.parent.mkdir(parents=True)
    assert is_subagent_transcript(sub) is True
    assert is_subagent_transcript(main) is False


class TestByOrigin:
    def test_by_origin_always_has_both_buckets(self):
        report = run_audit([FIXTURES / "clean_15req.jsonl"], DEFAULTS)
        bo = report["summary"]["by_origin"]
        assert set(bo) == {"main", "subagent"}
        assert set(bo["main"]) == {"requests", "misses"}
        assert set(bo["subagent"]) == {"requests", "misses"}
        # clean_15req is a main-thread fixture: all requests in main bucket
        assert bo["subagent"]["requests"] == 0
        assert bo["main"]["requests"] == report["summary"]["total_requests"]

    def test_main_and_subagent_misses_sum_to_total(self):
        report = run_audit([FIXTURES / "d1_model_switch.jsonl"], DEFAULTS)
        bo = report["summary"]["by_origin"]
        assert bo["main"]["misses"] + bo["subagent"]["misses"] == \
            report["summary"]["total_misses"]

    def test_subagent_misses_land_in_subagent_bucket(self, tmp_path):
        # THE defect this task fixes: subagent cold-start misses were counted as
        # avoidable main-thread waste. Build a real transcript under a subagents/
        # dir with a genuine miss (turn 2 re-processes 5000 tokens) and run it
        # through run_audit — its miss must land in the subagent bucket, not main.
        import json
        sub = tmp_path / "sess-uuid" / "subagents" / "agent-deadbeef.jsonl"
        sub.parent.mkdir(parents=True)
        lines = [
            {"type": "assistant", "timestamp": "2026-09-26T10:00:00.000Z",
             "message": {"model": "claude-sonnet-5", "usage": {
                 "input_tokens": 5000, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 5000,
                 "cache_creation": {"ephemeral_1h_input_tokens": 5000}}}},
            {"type": "assistant", "timestamp": "2026-09-26T10:00:05.000Z",
             "message": {"model": "claude-sonnet-5", "usage": {
                 "input_tokens": 5000, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 5000,
                 "cache_creation": {"ephemeral_1h_input_tokens": 5000}}}},
        ]
        sub.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        report = run_audit([sub], DEFAULTS)
        bo = report["summary"]["by_origin"]
        assert report["summary"]["total_misses"] >= 1
        assert bo["subagent"]["misses"] == report["summary"]["total_misses"]
        assert bo["main"]["misses"] == 0
