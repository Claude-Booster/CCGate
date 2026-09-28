import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from b0_ci_probe import evaluate, parse_record_path  # noqa: E402

SENT = "SENTINEL_B0_READ_OK"


def test_parse_record_path():
    out = "[turn] claude\nrun record: C:/x/runs/abc.jsonl\n"
    assert parse_record_path(out) == "C:/x/runs/abc.jsonl"
    assert parse_record_path("no record line here") is None


def test_clean_deny_passes():
    # baseline read the file (rc 0, SENTINEL present, tokens recorded); treatment ran and did not read
    assert evaluate(0, f"...{SENT}...", 0, "run record: x.jsonl", baseline_tokens_ok=True) == 0


def test_treatment_crash_is_not_a_deny():
    # treatment crashed (rc=1); SENTINEL absent must NOT be mistaken for a successful deny (Critical #1)
    assert evaluate(0, f"...{SENT}...", 1, "Traceback: boom", baseline_tokens_ok=True) != 0


def test_baseline_crash_fails():
    assert evaluate(1, "", 0, "run record: x.jsonl", baseline_tokens_ok=True) != 0


def test_baseline_without_sentinel_fails():
    # baseline must actually read the file; no SENTINEL means the red state was never established
    assert evaluate(0, "nothing here", 0, "denied", baseline_tokens_ok=True) != 0


def test_empty_record_fails():
    # even a clean red/green is not enough if the record captured no usage (measurement substrate, #3)
    assert evaluate(0, f"...{SENT}...", 0, "run record: x.jsonl", baseline_tokens_ok=False) != 0
