import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

WORKTREE = Path(__file__).parent.parent
sys.path.insert(0, str(WORKTREE / "src"))


def _make_transcript(tmp_path, model_id="claude-sonnet-5"):
    """Write a minimal single-turn JSONL transcript."""
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-21T00:00:00Z",
        "message": {
            "model": model_id,
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 100,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "content": [{"type": "text", "text": "Hello."}],
        },
    })
    p = tmp_path / "session.jsonl"
    p.write_text(line + "\n", encoding="utf-8")
    return p


def _fake_anthropic(original_tokens=5000, cleared_tokens=3000):
    """Return a mock anthropic module with count_tokens responses."""
    mock_client = MagicMock()
    baseline_resp = MagicMock()
    baseline_resp.input_tokens = original_tokens

    cleared_resp = MagicMock()
    cleared_resp.input_tokens = cleared_tokens
    cleared_resp.context_management = MagicMock()
    cleared_resp.context_management.original_input_tokens = original_tokens

    def count_tokens(**kwargs):
        if "context_management" in kwargs:
            return cleared_resp
        return baseline_resp

    mock_client.beta.messages.count_tokens.side_effect = count_tokens

    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    return mock_anthropic


def test_savings_computed_correctly(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)
    fake_anthro = _fake_anthropic(original_tokens=5000, cleared_tokens=3000)

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib

        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=False)

    assert rc == 0
    assert "5,000" in out   # original
    assert "3,000" in out   # cleared
    assert "2,000" in out   # savings


def test_json_output_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)
    fake_anthro = _fake_anthropic(original_tokens=5000, cleared_tokens=3000)

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib

        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=True)

    assert rc == 0
    data = json.loads(out)
    assert "original_tokens" in data
    assert "cleared_tokens" in data
    assert "savings_tokens" in data
    assert "savings_pct" in data
    assert "savings_usd_approx" in data
    assert data["savings_tokens"] == 2000


def test_missing_api_key_exits_1(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    transcript = _make_transcript(tmp_path)
    fake_anthro = _fake_anthropic()

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib

        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=False)

    assert rc == 1
    assert "ANTHROPIC_API_KEY" in out


def test_missing_anthropic_package(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)

    # Simulate ImportError by removing anthropic from sys.modules and blocking it
    with patch.dict(sys.modules, {"anthropic": None}):
        import importlib

        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=False)

    assert rc == 1
    assert "pip install anthropic" in out.lower() or "anthropic" in out.lower()


def test_zero_savings_reported(tmp_path, monkeypatch):
    # cleared >= original → savings == 0, never negative
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    transcript = _make_transcript(tmp_path)
    # cleared_tokens > original: simulate no savings (or expansion)
    fake_anthro = _fake_anthropic(original_tokens=3000, cleared_tokens=3500)

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib

        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        rc, out = mod._run_eval(str(transcript), json_out=True)

    assert rc == 0
    data = json.loads(out)
    assert data["savings_tokens"] == 0
    assert data["savings_usd_approx"] == 0


def test_model_id_from_last_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    # Transcript with two turns using different models
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-21T00:00:00Z",
            "message": {
                "model": "claude-haiku-4-5-20251001",
                "usage": {"input_tokens": 100, "output_tokens": 10,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            },
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-21T00:01:00Z",
            "message": {
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 200, "output_tokens": 20,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            },
        }),
    ]
    transcript = tmp_path / "two_turns.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    seen_models: list[str] = []
    fake_anthro = MagicMock()

    def count_tokens(**kwargs):
        seen_models.append(kwargs.get("model"))
        resp = MagicMock()
        resp.input_tokens = 200
        resp.context_management = MagicMock()
        resp.context_management.original_input_tokens = 200
        return resp

    fake_anthro.Anthropic.return_value.beta.messages.count_tokens.side_effect = count_tokens

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib

        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        mod._run_eval(str(transcript), json_out=False)

    # All count_tokens calls use the model from the LAST turn
    assert all(m == "claude-sonnet-5" for m in seen_models if m is not None)


def test_tool_use_content_preserved_in_messages(tmp_path, monkeypatch):
    """tool_use blocks must survive message reconstruction for clear_tool_uses to work."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-21T00:00:00Z",
        "message": {
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": 5000, "output_tokens": 100,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            "content": [
                {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "Done."},
            ],
        },
    })
    transcript = tmp_path / "tool_session.jsonl"
    transcript.write_text(line + "\n", encoding="utf-8")

    captured_calls: list[dict] = []
    fake_anthro = _fake_anthropic(original_tokens=5000, cleared_tokens=3000)
    orig_side_effect = fake_anthro.Anthropic.return_value.beta.messages.count_tokens.side_effect

    def capturing(**kwargs):
        captured_calls.append(kwargs)
        return orig_side_effect(**kwargs)

    fake_anthro.Anthropic.return_value.beta.messages.count_tokens.side_effect = capturing

    with patch.dict(sys.modules, {"anthropic": fake_anthro}):
        import importlib

        import ccgate.scripts.clear_eval as mod
        importlib.reload(mod)
        mod._run_eval(str(transcript), json_out=False)

    assert captured_calls, "count_tokens was not called"
    msgs = captured_calls[0]["messages"]
    all_blocks = [
        block
        for msg in msgs
        for block in (msg["content"] if isinstance(msg.get("content"), list) else [])
    ]
    block_types = {b.get("type") for b in all_blocks if isinstance(b, dict)}
    assert "tool_use" in block_types, f"tool_use blocks not preserved in messages: {msgs}"
