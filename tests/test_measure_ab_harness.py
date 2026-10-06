import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from measure_ab import assemble_report  # noqa: E402


def test_assemble_report_verdict_and_diagnostics():
    baseline = [{"tokens_total": t, "complete": True, "reason": "ok"} for t in (1000, 1100, 1200)]
    enforced = [{"tokens_total": t, "complete": True, "reason": "ok"} for t in (800, 820, 810)]
    diag = {"f1_fires_total": 0, "f3_truncations_total": 6}
    rep = assemble_report(baseline, enforced, diag)
    assert rep["verdict"] == "BUILD_B1C"
    assert rep["f1_fired"] is False           # F1 never fired -> headline finding (spec §5)
    assert "F1 did not fire" in rep["notes"]


def test_assemble_report_notes_empty_when_f1_fired():
    baseline = [{"tokens_total": t, "complete": True, "reason": "ok"} for t in (1000, 1100, 1200)]
    enforced = [{"tokens_total": t, "complete": True, "reason": "ok"} for t in (800, 820, 810)]
    diag = {"f1_fires_total": 3, "f3_truncations_total": 6}
    rep = assemble_report(baseline, enforced, diag)
    assert rep["f1_fired"] is True and rep["notes"] == ""
