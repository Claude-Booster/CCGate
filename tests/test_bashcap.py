import asyncio

import pytest

from ccgate.run.bashcap import (
    BashCapHook,
    command_matches,
    compile_prefixes,
    extract_stdout,
    repack,
    truncate,
)
from ccgate.run.record import RunRecorder


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


def test_truncate_never_grows_output():
    # 100 chars over a tiny 20-char budget: the ~135-char marker would make it bigger,
    # so truncate leaves it unchanged (spec §6 — F3 must never grow output).
    out, elided = truncate("X" * 100, head=10, tail=10)
    assert (out, elided) == ("X" * 100, 0)


def test_extract_stdout_shapes():
    assert extract_stdout("plain string") == "plain string"
    assert extract_stdout({"stdout": "s", "stderr": "e", "interrupted": False}) == "s"
    assert extract_stdout({"no_stdout_here": 1}) is None      # unrecognized dict → None
    assert extract_stdout(12345) is None


def test_repack_preserves_dict_siblings():
    out = repack({"stdout": "old", "stderr": "e", "interrupted": False}, "new")
    assert out == {"stdout": "new", "stderr": "e", "interrupted": False}
    assert repack("old", "new") == "new"


def _hook(tmp_path, monkeypatch, prefixes=("pytest",), head=10, tail=10, debug=3):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-hook")
    return BashCapHook(list(prefixes), head, tail, debug, rec), rec


def _call(hook, command, stdout):
    inp = {"tool_name": "Bash", "tool_input": {"command": command},
           "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False}}
    return asyncio.run(hook(inp, "tuid", None))


def test_truncates_matched_over_budget(tmp_path, monkeypatch):
    hook, rec = _hook(tmp_path, monkeypatch)
    out = _call(hook, "pytest -q", "X" * 1000)   # well over budget so the marker overhead saves
    new = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert "elided" in new and len(new) < 1000
    assert hook.summary()["truncations"] == 1
    assert hook.summary()["total_chars_elided"] == 980


def test_non_matching_command_passthrough(tmp_path, monkeypatch):
    hook, _ = _hook(tmp_path, monkeypatch)
    assert _call(hook, "ls -la", "X" * 100) == {}


def test_dict_without_stdout_passthrough_no_event(tmp_path, monkeypatch):
    hook, rec = _hook(tmp_path, monkeypatch)
    inp = {"tool_name": "Bash", "tool_input": {"command": "pytest"},
           "tool_response": {"weird": "shape"}}
    assert asyncio.run(hook(inp, "t", None)) == {}
    assert hook.summary()["truncations"] == 0


def test_non_bash_passthrough(tmp_path, monkeypatch):
    hook, _ = _hook(tmp_path, monkeypatch)
    inp = {"tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": {"stdout": "Y" * 100}}
    assert asyncio.run(hook(inp, "t", None)) == {}


def test_debug_loop_repeat_not_truncated(tmp_path, monkeypatch):
    hook, _ = _hook(tmp_path, monkeypatch, debug=3)
    _call(hook, "pytest -q", "X" * 1000)          # 1st: truncated
    out2 = _call(hook, "pytest -q", "X" * 1000)   # 2nd identical within window: skipped
    assert out2 == {}
    assert hook.summary()["debug_loop_skips"] == 1
    assert hook.summary()["truncations"] == 1
