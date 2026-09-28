from pathlib import Path
from ccgate.run.record import make_run_id, assistant_entry, runs_dir
from ccgate.transcript import encode_cwd, _parse_usage


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
