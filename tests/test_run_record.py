import json

from ccgate.run.record import RunRecorder, assistant_entry, is_complete, make_run_id, runs_dir
from ccgate.scripts.miss_audit import run_audit
from ccgate.transcript import _parse_usage, encode_cwd


def test_append_then_finish_writes_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-abc")
    rec.append_assistant("claude-opus-4-8", {"input_tokens": 1, "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0, "output_tokens": 1})
    assert is_complete(rec.path) is False      # no marker yet → crashed/incomplete
    rec.finish()
    assert is_complete(rec.path) is True
    lines = rec.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["type"] == "ccgate_run_end"


def test_append_event_writes_verbatim_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-evt")
    rec.append_event({"type": "ccgate_event", "rule": "F3", "chars_elided": 500})
    line = json.loads(rec.path.read_text(encoding="utf-8").splitlines()[-1])
    assert line["rule"] == "F3" and line["chars_elided"] == 500


def test_event_does_not_perturb_token_counts(tmp_path, monkeypatch):
    """read_transcript/run_audit ignore non-assistant lines (spec §6)."""
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-evt2")
    rec.append_assistant("claude-opus-4-8", {"input_tokens": 5, "cache_read_input_tokens": 50,
                                             "cache_creation_input_tokens": 0, "output_tokens": 3})
    before = run_audit([rec.path], {})["summary"]["tokens"]
    rec.append_event({"type": "ccgate_event", "rule": "F3", "chars_elided": 999})
    after = run_audit([rec.path], {})["summary"]["tokens"]
    assert before == after


def test_marker_is_harmless_to_measurement(tmp_path, monkeypatch):
    """run_audit token counts identical with and without the terminal marker (spec §5)."""
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    rec = RunRecorder("run-xyz")
    usage = {"input_tokens": 5, "cache_read_input_tokens": 50,
             "cache_creation_input_tokens": 0, "output_tokens": 3}
    rec.append_assistant("claude-opus-4-8", usage)
    without_marker = run_audit([rec.path], {})["summary"]["tokens"]
    rec.finish()
    with_marker = run_audit([rec.path], {})["summary"]["tokens"]
    assert with_marker == without_marker


def test_run_id_encodes_repo():
    cwd = "C:/proj/CCGate"
    rid = make_run_id(cwd)
    assert rid.startswith(encode_cwd(cwd))     # repo-bucketable prefix
    assert rid != make_run_id(cwd)             # unique per call (timestamp+uuid)


def test_assistant_entry_is_transcript_shaped():
    usage = {"input_tokens": 10, "cache_read_input_tokens": 90,
             "cache_creation_input_tokens": 0, "output_tokens": 5}
    e = assistant_entry("claude-opus-4-8", usage, timestamp="2026-09-28T00:00:00.000Z")
    assert e["type"] == "assistant"
    assert e["timestamp"] == "2026-09-28T00:00:00.000Z"
    assert e["message"]["model"] == "claude-opus-4-8"
    # read_transcript's own parser must accept the usage verbatim:
    parsed = _parse_usage(e["message"])
    assert parsed.input_tokens == 10 and parsed.cache_read_input_tokens == 90


def test_runs_dir_under_ccgate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    assert runs_dir() == tmp_path / "runs"
