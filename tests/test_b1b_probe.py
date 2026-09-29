import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from b1b_ci_probe import evaluate  # noqa: E402

MARK = "ccgate F3"


def test_green_when_baseline_full_and_treatment_truncated():
    assert evaluate(0, "L"*50000, 0, "LLL" + MARK + "LLL") == 0


def test_fails_if_treatment_not_truncated():
    assert evaluate(0, "L"*50000, 0, "L"*50000) != 0        # marker absent → no truncation


def test_treatment_crash_is_not_success():
    assert evaluate(0, "L"*50000, 1, "traceback") != 0
