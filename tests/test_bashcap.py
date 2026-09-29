import pytest
from ccgate.run.bashcap import build_marker, compile_prefixes, command_matches, truncate


def test_compile_prefixes_rejects_regex_metachars():
    with pytest.raises(ValueError):
        compile_prefixes(["pytest", "grep.*"])   # '.' and '*' are metachars
    assert compile_prefixes(["pytest", "cargo test"]) == ["pytest", "cargo test"]


def test_command_matches_prefix():
    pats = ["pytest", "cargo test"]
    assert command_matches("pytest -q tests/", pats) is True
    assert command_matches("cargo test --all", pats) is True
    assert command_matches("ls -la", pats) is False


def test_truncate_over_budget_keeps_head_tail_and_marker():
    stdout = "H" * 100 + "M" * 500 + "T" * 100  # 700 chars
    out, elided = truncate(stdout, head=100, tail=100)
    assert elided == 500
    assert out.startswith("H" * 100)
    assert out.endswith("T" * 100)
    assert "elided" in out and "500" in out


def test_truncate_at_exact_budget_is_no_change():
    stdout = "X" * 200
    out, elided = truncate(stdout, head=100, tail=100)   # len == head+tail
    assert elided == 0
    assert out == stdout


def test_truncate_head_zero_is_valid():
    stdout = "A" * 300
    out, elided = truncate(stdout, head=0, tail=100)
    assert elided == 200
    assert out.endswith("A" * 100)
    assert not out.startswith("A" * 101)   # no accidental full slice from head=0


def test_truncate_under_budget_unchanged():
    assert truncate("small", head=100, tail=100) == ("small", 0)
