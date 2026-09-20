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


class TestMissCauseSuffix:
    """Miss-cause suffix appended to low-ratio escalation in Band 3."""

    def _base_payload(self, hit_ratio=0.60, requests=15):
        return {
            "model": {"id": "claude-sonnet-5"},
            "context_window": {"used_percentage": 30.0},
            "cost": {"total_cost_usd": 0.50},
            "prompt_cache": {
                "hit_ratio": hit_ratio,
                "requests": requests,
                "warm": True,
                "caching_observed": True,
                "recache_tokens_if_cold": 0,
            },
        }

    def test_low_ratio_band3_shows_top_cause(self):
        payload = self._base_payload()
        payload["prompt_cache"]["miss_causes"] = {"tools_changed": 3, "ttl_expired": 1}
        from ccgate.config import DEFAULTS
        result = render(payload, DEFAULTS)
        assert "tools_changed" in result
        assert "×3" in result

    def test_low_ratio_band3_shows_at_most_two_causes(self):
        payload = self._base_payload()
        payload["prompt_cache"]["miss_causes"] = {
            "tools_changed": 5, "ttl_expired": 3, "model_switch": 1
        }
        from ccgate.config import DEFAULTS
        result = render(payload, DEFAULTS)
        # top-2 by count: tools_changed, ttl_expired; model_switch must not appear
        assert "tools_changed" in result
        assert "ttl_expired" in result
        assert "model_switch" not in result

    def test_low_ratio_band2_no_suffix(self):
        # Band 2: miss_causes key absent
        payload = self._base_payload()
        # miss_causes not set — Band 2 behaviour
        from ccgate.config import DEFAULTS
        result = render(payload, DEFAULTS)
        assert "×" not in result

    def test_high_ratio_no_suffix(self):
        payload = self._base_payload(hit_ratio=0.95)
        payload["prompt_cache"]["miss_causes"] = {"tools_changed": 1}
        from ccgate.config import DEFAULTS
        result = render(payload, DEFAULTS)
        assert "×" not in result

    def test_low_ratio_empty_miss_causes_no_suffix(self):
        payload = self._base_payload()
        payload["prompt_cache"]["miss_causes"] = {}
        from ccgate.config import DEFAULTS
        result = render(payload, DEFAULTS)
        assert "×" not in result
