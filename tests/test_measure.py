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
