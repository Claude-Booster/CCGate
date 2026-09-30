import json
from pathlib import Path
from ccgate.measure import extract_run_metrics


def _write_record(tmp_path):
    lines = [
        {"type": "assistant", "message": {"model": "m", "usage": {
            "input_tokens": 100, "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 0, "output_tokens": 50}}},
        {"type": "assistant", "message": {"model": "m", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 200, "output_tokens": 20}}},
        {"type": "ccgate_event", "rule": "F1", "surface": "bash", "matched_token": "x.lock"},
        {"type": "ccgate_event", "rule": "F3", "command_prefix": "pytest", "chars_elided": 5000},
        {"type": "ccgate_event", "rule": "F3", "summary": True, "truncations": 1},  # NOT a fire
        {"type": "ccgate_run_end"},
    ]
    p = tmp_path / "run.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return p


def test_extract_run_metrics(tmp_path):
    m = extract_run_metrics(_write_record(tmp_path))
    # tokens_total = (100+900+0 + 50) + (10+0+200 + 20) = 1050 + 230 = 1280
    assert m["tokens_total"] == 1280
    assert m["turns"] == 2                    # two assistant usage lines
    assert m["f1_fires"] == 1
    assert m["f3_truncations"] == 1           # summary line excluded
    assert m["complete_marker"] is True       # ccgate_run_end present
    assert m["misses_per_1k"] >= 0            # secondary, not gated


def test_extract_incomplete_record_has_no_marker(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {"usage": {
        "input_tokens": 1, "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0, "output_tokens": 1}}}) + "\n", encoding="utf-8")
    assert extract_run_metrics(p)["complete_marker"] is False


from ccgate.measure import derive_n, decide


def test_derive_n_boundaries():
    assert derive_n(1000, 1000) == 5          # d=0 -> 5
    assert derive_n(1000, 1090) == 5          # d~8.6% (<=10) -> 5
    assert derive_n(1000, 1200) == 9          # d~18% (10<d<=25) -> 9


def test_derive_n_unmeasurable():
    assert derive_n(1000, 1300) is None       # d~26% (>25) -> None
    assert derive_n(1000, 2000) is None       # d~67% -> None


def test_decide_excludes_incomplete_from_median():
    # Both arms at the SAME 3/5 completion so the completion condition passes and the test
    # isolates the property under test: the enforced median is over COMPLETED runs only (810),
    # not dragged toward 0 by the two incompletes.
    base = ([{"tokens_total": t, "complete": True} for t in (1000, 1100, 1200)]
            + [{"tokens_total": 0, "complete": False}, {"tokens_total": 0, "complete": False}])
    enf = ([{"tokens_total": t, "complete": True} for t in (800, 820, 810)]
           + [{"tokens_total": 0, "complete": False}, {"tokens_total": 0, "complete": False}])
    d = decide(base, enf)   # both 60% complete -> not void; enforced median 810 < baseline min 1000
    assert d["verdict"] == "BUILD_B1C" and d["enforced_median"] == 810


def test_decide_void_when_low_completion():
    base = [{"tokens_total": 100, "complete": True}] + [{"tokens_total": 0, "complete": False}] * 4
    enf = [{"tokens_total": 50, "complete": True}] + [{"tokens_total": 0, "complete": False}] * 4
    assert decide(base, enf)["verdict"] == "VOID"     # 1/5 each -> <=50%


def test_decide_build_when_enforced_below_baseline_min():
    base = [{"tokens_total": t, "complete": True} for t in (1000, 1100, 1200, 1050, 1150)]
    enf = [{"tokens_total": t, "complete": True} for t in (800, 850, 820, 830, 810)]
    assert decide(base, enf)["verdict"] == "BUILD_B1C"     # enforced median 820 < baseline min 1000


def test_decide_no_build_when_within_baseline_range():
    base = [{"tokens_total": t, "complete": True} for t in (1000, 1100, 1200, 1050, 1150)]
    enf = [{"tokens_total": t, "complete": True} for t in (1020, 1080, 1090, 1030, 1060)]
    assert decide(base, enf)["verdict"] == "NO_BUILD"     # enforced median 1060 >= baseline min 1000


from ccgate.measure import classify_completion, tokens_injected_present


def test_classify_completion():
    assert classify_completion(True, 0, False) == (True, "ok")
    assert classify_completion(False, 0, True) == (False, "turn_cap")
    assert classify_completion(False, 1, False) == (False, "error")
    assert classify_completion(False, 0, False) == (False, "gave_up")


def test_tokens_injected_present():
    assert tokens_injected_present('event = {"rule": "F3", "tokens_injected": n}') is True
    assert tokens_injected_present('event = {"rule": "F3", "chars_elided": n}') is False
