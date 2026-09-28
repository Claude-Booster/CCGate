import pytest

from ccgate.report import assert_bounds

VALID = {
    "total_requests": 5, "total_misses": 2, "hit_ratio": 0.6, "cache_read_rate": 0.25,
    "by_origin": {"main": {"requests": 3, "misses": 1},
                  "subagent": {"requests": 2, "misses": 1}},
    "tokens": {"grand_total_input": 404000, "cache_read": 100000,
               "cache_creation": 300000, "input": 4000, "output": 190},
}


def test_valid_summary_passes():
    assert_bounds(VALID)  # must not raise


def test_zero_requests_passes():
    empty = {"total_requests": 0, "total_misses": 0, "hit_ratio": 1.0,
             "cache_read_rate": 0.0,
             "by_origin": {"main": {"requests": 0, "misses": 0},
                           "subagent": {"requests": 0, "misses": 0}},
             "tokens": {"grand_total_input": 0, "cache_read": 0,
                        "cache_creation": 0, "input": 0, "output": 0}}
    assert_bounds(empty)  # must not raise


def test_misses_exceed_requests_fires():
    bad = {**VALID, "total_misses": 6}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_origin_misses_mismatch_fires():
    bad = {**VALID, "by_origin": {"main": {"requests": 3, "misses": 1},
                                  "subagent": {"requests": 2, "misses": 0}}}
    with pytest.raises(ValueError):
        assert_bounds(bad)  # 1 + 0 != total_misses 2


def test_negative_token_fires():
    bad = {**VALID, "tokens": {**VALID["tokens"], "cache_read": -1}}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_cache_exceeds_grand_total_fires():
    bad = {**VALID, "tokens": {**VALID["tokens"], "grand_total_input": 100}}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_rate_out_of_range_fires():
    bad = {**VALID, "cache_read_rate": 1.5}
    with pytest.raises(ValueError):
        assert_bounds(bad)
