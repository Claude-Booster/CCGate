import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ccgate.run.cli import run_task


@dataclass
class _FakeAssistant:
    model: str
    usage: dict
    content: list = None


class _FakeClient:
    """Stands in for ClaudeSDKClient: records the options it was built with, yields one turn."""
    last_options = None

    def __init__(self, options=None):
        _FakeClient.last_options = options

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def query(self, prompt): self._prompt = prompt

    async def receive_response(self):
        yield _FakeAssistant("claude-opus-4-8",
                             {"input_tokens": 2, "cache_read_input_tokens": 8,
                              "cache_creation_input_tokens": 0, "output_tokens": 1})


def test_enforced_run_wires_hook_and_writes_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / ".contextignore").write_text("secrets/*.txt\n", encoding="utf-8")
    path = asyncio.run(run_task("do the task", enforce=True, cwd=tmp_path,
                                client_factory=_FakeClient))
    opts = _FakeClient.last_options
    assert opts["setting_sources"] == []
    assert "Read" in opts["allowed_tools"]
    assert opts["hooks"]["PreToolUse"]           # hook present when enforcing
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    assert lines[0]["message"]["model"] == "claude-opus-4-8"
    assert lines[-1]["type"] == "ccgate_run_end"


def test_no_enforce_omits_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    asyncio.run(run_task("do the task", enforce=False, cwd=tmp_path, client_factory=_FakeClient))
    assert not _FakeClient.last_options["hooks"]["PreToolUse"]   # empty → no enforcement


def test_missing_task_file_exits_nonzero(tmp_path, capsys):
    from ccgate.run.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["--task", str(tmp_path / "nope.txt")])
    assert ei.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_b0_restricts_tools_to_read():
    """B0 grants only Read: `tools` (availability) AND allowed_tools (auto-approve) both ["Read"].
    allowed_tools alone leaves Bash/Grep available-but-unapproved, so the model reaches for them
    and is blocked before using Read; and it would leave a non-Read bypass of the deny (review #3)."""
    from ccgate.run.cli import _build_options
    from ccgate.config import load_config
    opts = _build_options([], enforce=True, config=load_config(), bashcap_hook=None)
    assert opts["tools"] == ["Read"]
    assert opts["allowed_tools"] == ["Read"]


def test_bashcap_disabled_by_default_no_bash_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))       # no config → bashCapEnabled False
    asyncio.run(run_task("t", enforce=True, cwd=tmp_path, client_factory=_FakeClient))
    opts = _FakeClient.last_options
    assert "Bash" not in opts["allowed_tools"]
    assert opts["hooks"].get("PostToolUse", []) == []


def test_bashcap_enabled_adds_bash_and_posttool_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapEnabled": true}', encoding="utf-8")
    asyncio.run(run_task("t", enforce=True, cwd=tmp_path, client_factory=_FakeClient))
    opts = _FakeClient.last_options
    assert "Bash" in opts["allowed_tools"] and "Bash" in opts["tools"]
    assert opts["hooks"]["PostToolUse"]       # hook present


def test_missing_sdk_gives_clean_error(tmp_path, monkeypatch, capsys):
    """Without the 'run' extra, `ccgate run` exits cleanly, not with a traceback (review #2)."""
    import importlib.util as iu
    from ccgate.run import cli
    task = tmp_path / "t.txt"
    task.write_text("do it", encoding="utf-8")
    monkeypatch.setattr(cli.importlib.util, "find_spec",
                        lambda n: None if n == "claude_agent_sdk" else iu.find_spec(n))
    monkeypatch.setattr(cli.asyncio, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not reach run")))
    with pytest.raises(SystemExit) as ei:
        cli.main(["--task", str(task)])
    assert ei.value.code == 1
    assert "run" in capsys.readouterr().err.lower()


def test_unreadable_task_dir_exits_cleanly(tmp_path, capsys):
    """--task pointing at a directory (unreadable) → clean exit, not a traceback (review #4)."""
    from ccgate.run.cli import main
    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(SystemExit) as ei:
        main(["--task", str(d)])
    assert ei.value.code == 1
    assert "task" in capsys.readouterr().err.lower()


def _sdk_installed():
    import importlib.util
    return importlib.util.find_spec("claude_agent_sdk") is not None


@pytest.mark.skipif(not _sdk_installed(), reason="claude-agent-sdk not installed")
def test_factory_builds_real_sdk_options():
    """The translation seam (raw dict -> ClaudeAgentOptions/HookMatcher) must not drift from the
    SDK signatures. Constructing options needs no auth/network — catch drift here, not in CI."""
    from ccgate.run.cli import _build_options, _factory
    from ccgate.config import load_config
    cfg = load_config()
    # Both enforce states must build a real client without raising:
    assert _factory(_build_options(["secrets/*.txt"], enforce=True, config=cfg, bashcap_hook=None)) is not None
    assert _factory(_build_options([], enforce=False, config=cfg, bashcap_hook=None)) is not None
