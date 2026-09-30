import asyncio
import json
from pathlib import Path
from ccgate.run.policy import (
    load_contextignore, path_is_ignored, make_read_deny_hook, make_bash_read_deny_hook,
    first_matching_pattern,
)
from ccgate.run.record import RunRecorder


def _call(hook, tool_name, tool_input):
    return asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input}, "tuid", None))


def _events(rec):
    return [json.loads(l) for l in Path(rec.path).read_text(encoding="utf-8").splitlines()]


def _bash_hook(tmp_path, monkeypatch, patterns=("*.lock",),
               readers=("cat", "head", "tail", "less", "more", "sed", "awk")):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-b1a")
    return make_bash_read_deny_hook(list(patterns), list(readers), rec), rec


def _bash_call(hook, command):
    return asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": command}}, "tuid", None))


def _is_deny(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_denies_ignored_read():
    hook = make_read_deny_hook(["secrets/*.txt"])
    out = _call(hook, "Read", {"file_path": "secrets/a.txt"})
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "secrets/a.txt" in hso["permissionDecisionReason"]


def test_allows_non_ignored_read():
    hook = make_read_deny_hook(["secrets/*.txt"])
    assert _call(hook, "Read", {"file_path": "src/main.py"}) == {}


def test_ignores_non_read_tools():
    hook = make_read_deny_hook(["secrets/*.txt"])
    assert _call(hook, "Bash", {"command": "cat secrets/a.txt"}) == {}


def test_absent_contextignore_returns_empty(tmp_path):
    assert load_contextignore(tmp_path) == []


def test_blank_and_comment_lines_ignored(tmp_path):
    (tmp_path / ".contextignore").write_text("# comment\n\nsecrets/*.txt\n", encoding="utf-8")
    assert load_contextignore(tmp_path) == ["secrets/*.txt"]


def test_glob_matches_full_path_and_basename(tmp_path):
    pats = ["secrets/*.txt", "*.key"]
    assert path_is_ignored("secrets/a.txt", pats) is True
    assert path_is_ignored("/repo/private.key", pats) is True     # basename match
    assert path_is_ignored("src/main.py", pats) is False


def test_empty_patterns_never_match():
    assert path_is_ignored("anything.txt", []) is False


def test_backslash_paths_normalized():
    assert path_is_ignored("C:\\repo\\foo.lock", ["*.lock"]) is True
    assert path_is_ignored("C:\\repo\\src\\app.py", ["*.lock"]) is False


def test_trailing_slash_directory_segment_match():
    pats = ["node_modules/"]
    assert path_is_ignored("node_modules/pkg/index.js", pats) is True
    assert path_is_ignored("src/node_modules/a.js", pats) is True
    assert path_is_ignored("node_modules", pats) is True
    assert path_is_ignored("mynode_modules/x.js", pats) is False   # substring, not a segment
    assert path_is_ignored("src/app.py", pats) is False


def test_existing_glob_and_basename_still_work():
    assert path_is_ignored("a/b/package-lock.json", ["package-lock.json"]) is True
    assert path_is_ignored("x/y/foo.min.js", ["*.min.js"]) is True


def test_bash_deny_common_readers(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _is_deny(_bash_call(hook, "cat foo.lock"))
    assert _is_deny(_bash_call(hook, "head -100 foo.lock"))
    assert _is_deny(_bash_call(hook, "sed -n 1,5p foo.lock"))


def test_bash_allow_non_reader_and_allowed_file(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _bash_call(hook, "rm foo.lock") == {}          # not a reader — allow
    assert _bash_call(hook, "git add foo.lock") == {}     # not a reader — allow
    assert _bash_call(hook, "cat app.py") == {}           # reader, allowed file — allow


def test_bash_deny_whole_command_mixed_files(tmp_path, monkeypatch):
    hook, rec = _bash_hook(tmp_path, monkeypatch)
    out = _bash_call(hook, "cat a.lock b.txt")
    assert _is_deny(out)
    ev = [e for e in _events(rec) if e.get("rule") == "F1" and e.get("surface") == "bash"]
    assert ev and ev[-1]["matched_token"] == "a.lock"


def test_bash_deny_redirection_and_runner_and_quotes(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _is_deny(_bash_call(hook, "cat foo.lock > out.txt"))
    assert _is_deny(_bash_call(hook, "sudo cat foo.lock"))
    assert _is_deny(_bash_call(hook, 'cat "x.lock"'))            # surrounding quotes stripped
    assert _is_deny(_bash_call(hook, "cat C:\\repo\\foo.lock"))  # backslash normalized


def test_reader_gate_is_first_token_equality_not_startswith(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert _bash_call(hook, "lessc styles.lock") == {}    # 'lessc' != 'less' — allow
    assert _bash_call(hook, "catalog foo.lock") == {}     # 'catalog' != 'cat' — allow


def test_bash_matches_read_path_on_substring(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert path_is_ignored("foo.lock.bak", ["*.lock"]) is False   # Read predicate: no match
    assert _bash_call(hook, "cat foo.lock.bak") == {}             # Bash path agrees — allow


def test_bash_non_bash_passthrough(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    assert asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "foo.lock"}}, "t", None)) == {}


def test_bash_fail_open_increments_matcher_errors(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    # tool_input is a non-dict truthy value -> `(123 or {}).get(...)` raises -> caught, counted.
    out = asyncio.run(hook({"tool_name": "Bash", "tool_input": 123}, "t", None))
    assert out == {}
    assert hook.matcher_errors == 1


def test_bash_summary_shape(tmp_path, monkeypatch):
    hook, _ = _bash_hook(tmp_path, monkeypatch)
    _bash_call(hook, "cat foo.lock")
    s = hook.summary()
    assert s["rule"] == "F1" and s["surface"] == "bash"
    assert s["denies"] == 1 and s["matcher_errors"] == 0


def test_first_matching_pattern():
    assert first_matching_pattern("a/foo.lock", ["*.txt", "*.lock"]) == "*.lock"
    assert first_matching_pattern("a/app.py", ["*.lock"]) is None


def test_read_deny_records_event_when_recorder_given(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    r = RunRecorder("run-read")
    read_hook = make_read_deny_hook(["*.lock"], recorder=r)
    out = asyncio.run(read_hook({"tool_name": "Read", "tool_input": {"file_path": "a/foo.lock"}}, "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    ev = [e for e in _events(r) if e.get("rule") == "F1" and e.get("surface") == "read"]
    assert ev and ev[-1]["matched_pattern"] == "*.lock" and ev[-1]["matched_token"] == "a/foo.lock"


def test_read_deny_recorder_none_still_denies(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    read_hook = make_read_deny_hook(["*.lock"])  # no recorder — B0 call site
    out = asyncio.run(read_hook({"tool_name": "Read", "tool_input": {"file_path": "a/foo.lock"}}, "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bash_deny_records_matched_pattern(tmp_path, monkeypatch):
    hook, rec = _bash_hook(tmp_path, monkeypatch)
    _bash_call(hook, "cat foo.lock")
    ev = [e for e in _events(rec) if e.get("rule") == "F1" and e.get("surface") == "bash"]
    assert ev[-1]["matched_pattern"] == "*.lock"


def test_baseline_empty_patterns_allow_default_reader(tmp_path, monkeypatch):
    # Pins the b1a probe's baseline mechanism: default bashReadPrefixes DOES include 'cat'
    # (list-merged, non-emptyable), so the deny must be disabled via EMPTY patterns
    # (empty .contextignore), not by emptying the prefix list. Empty patterns -> allow.
    from ccgate.config import load_config
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    cfg = load_config()
    assert "cat" in cfg["bashReadPrefixes"]                       # default readers include cat
    hook = make_bash_read_deny_hook([], cfg["bashReadPrefixes"], RunRecorder("r-base"))
    assert _bash_call(hook, "cat target_marker.txt") == {}        # no patterns -> allow (RED baseline)


def test_treatment_real_patterns_deny_default_reader(tmp_path, monkeypatch):
    from ccgate.config import load_config
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    cfg = load_config()
    hook = make_bash_read_deny_hook(["target_marker.txt"], cfg["bashReadPrefixes"], RunRecorder("r-treat"))
    assert _is_deny(_bash_call(hook, "cat target_marker.txt"))    # real pattern -> deny (GREEN treatment)
