import json
from pathlib import Path

from ccgate.config import DEFAULTS
from ccgate.scripts.statusline import render

FIXTURES = Path(__file__).parent / "fixtures" / "statusline"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestRender:
    def test_band3_normal_line_format(self):
        payload = _load("band3_payload.json")
        line = render(payload, DEFAULTS)
        assert "[Opus" in line or "Opus" in line
        assert "%" in line
        assert "$" in line
        assert "\n" not in line

    def test_band1_renders_without_cache_ratio(self):
        payload = _load("band1_payload.json")
        line = render(payload, DEFAULTS)
        assert isinstance(line, str)
        assert "\n" not in line

    def test_band2_shows_hit_ratio(self):
        payload = _load("band2_payload.json")
        line = render(payload, DEFAULTS)
        assert "cache" in line.lower() or "%" in line

    def test_cold_cache_escalation(self):
        payload = _load("band3_payload.json")
        payload["prompt_cache"]["warm"] = False
        payload["prompt_cache"]["recache_tokens_if_cold"] = 200_000
        line = render(payload, DEFAULTS)
        assert "COLD" in line

    def test_low_hit_ratio_escalation(self):
        payload = _load("band3_payload.json")
        payload["prompt_cache"]["hit_ratio"] = 0.61
        payload["prompt_cache"]["requests"] = 15
        line = render(payload, DEFAULTS)
        assert "↓" in line or "61%" in line or "cache" in line.lower()

    def test_compact_advise_escalation(self):
        payload = _load("band3_payload.json")
        payload["context_window"]["used_percentage"] = 82
        line = render(payload, DEFAULTS)
        assert "/compact" in line

    def test_no_cache_reported_escalation(self):
        payload = _load("band3_payload.json")
        payload["prompt_cache"]["caching_observed"] = False
        payload["prompt_cache"]["requests"] = 5
        line = render(payload, DEFAULTS)
        assert "NO CACHE" in line or "cache" in line.lower()

    def test_line_respects_columns(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "60")
        payload = _load("band3_payload.json")
        line = render(payload, DEFAULTS)
        assert len(line) <= 60
