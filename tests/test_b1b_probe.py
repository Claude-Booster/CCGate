import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from b1b_ci_probe import evaluate, _parse_truncations  # noqa: E402


def test_green_when_baseline_zero_and_treatment_truncated():
    assert evaluate(0, 0, 0, 1) == 0


def test_fails_if_treatment_not_truncated():
    assert evaluate(0, 0, 0, 0) != 0


def test_treatment_crash_is_not_success():
    assert evaluate(0, 0, 1, 0) != 0


def test_parse_truncations_reads_f3_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    record = tmp_path / "run.jsonl"
    record.write_text(
        json.dumps({"type": "ccgate_event", "rule": "F3", "summary": True, "truncations": 3}) + "\n",
        encoding="utf-8",
    )
    fake_out = f"some output\nrun record: {record}\n"
    assert _parse_truncations(fake_out) == 3


def test_parse_truncations_returns_zero_when_no_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    record = tmp_path / "run.jsonl"
    record.write_text(
        json.dumps({"type": "ccgate_run_end"}) + "\n",
        encoding="utf-8",
    )
    fake_out = f"run record: {record}\n"
    assert _parse_truncations(fake_out) == 0


def test_parse_truncations_returns_zero_when_no_record_line():
    assert _parse_truncations("no record path in here") == 0
