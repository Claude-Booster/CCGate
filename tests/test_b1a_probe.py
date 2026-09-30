import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from b1a_ci_probe import evaluate, _parse_bash_denies, _contextignore_for  # noqa: E402

MARK = "SENTINEL_B1A_READ_OK"


def test_baseline_uses_empty_contextignore_not_prefix_toggle():
    # Review Finding 1: bashReadPrefixes is list-merged, so [] can't empty it — the baseline
    # must disable the deny via an EMPTY .contextignore instead (bashReadPrefixes stays default).
    assert _contextignore_for(False) == ""                       # baseline: no patterns -> no deny
    assert _contextignore_for(True).strip() == "target_marker.txt"  # treatment: real pattern -> deny


def test_green_when_baseline_reads_and_treatment_denies():
    assert evaluate(0, MARK, 0, "I'll skip it.", 1) == 0


def test_fails_if_baseline_did_not_read():
    assert evaluate(0, "no marker here", 0, "skipped", 1) != 0     # red state absent


def test_fails_if_treatment_not_denied():
    assert evaluate(0, MARK, 0, MARK, 0) != 0                      # no deny event


def test_fails_if_treatment_crashed():
    assert evaluate(0, MARK, 1, "traceback", 1) != 0


def test_parse_bash_denies_counts_f1_bash_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = tmp_path / "r.jsonl"
    rec.write_text(json.dumps({"type": "ccgate_event", "rule": "F1", "surface": "bash",
                               "summary": True, "denies": 2}) + "\n", encoding="utf-8")
    assert _parse_bash_denies(f"run record: {rec}\n") == 2
