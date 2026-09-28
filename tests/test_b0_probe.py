import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from b0_ci_probe import evaluate  # noqa: E402

SENT = "SENTINEL_B0_READ_OK"


def test_clean_deny_passes():
    # baseline read the file (rc 0, SENTINEL present); treatment ran cleanly and did NOT read it
    assert evaluate(0, f"...{SENT}...", 0, "run record: x.jsonl") == 0


def test_treatment_crash_is_not_a_deny():
    # treatment crashed (rc=1); SENTINEL absent must NOT be mistaken for a successful deny (Critical #1)
    assert evaluate(0, f"...{SENT}...", 1, "Traceback: boom") != 0


def test_baseline_crash_fails():
    assert evaluate(1, "", 0, "run record: x.jsonl") != 0


def test_baseline_without_sentinel_fails():
    # baseline must actually read the file; no SENTINEL means the red state was never established
    assert evaluate(0, "nothing here", 0, "denied") != 0
