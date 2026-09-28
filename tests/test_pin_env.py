"""test_pin_env.py — G18: session pinning shape checks and fix staging."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ccgate.scripts.shape import (
    _check_session_pinning,
    _get_cc_version,
    _is_subscription_auth,
    stage_fixes,
    apply_fixes,
)


class TestGetCcVersion:
    def test_returns_tuple_on_success(self):
        """Parsed version string becomes a 3-tuple of ints."""
        with patch("ccgate.scripts.shape.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Claude Code 2.1.257 (claude-code)\n"
            mock_run.return_value.returncode = 0
            result = _get_cc_version()
        assert result == (2, 1, 257)

    def test_returns_none_on_failure(self):
        """Subprocess failure returns None (fail-open)."""
        with patch("ccgate.scripts.shape.subprocess.run", side_effect=OSError("not found")):
            result = _get_cc_version()
        assert result is None

    def test_spawn_uses_devnull_stdin(self):
        """fd-0 hygiene: the version spawn must not inherit a contaminated stdin."""
        import subprocess as _sp
        with patch("ccgate.scripts.shape.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Claude Code 2.1.257 (claude-code)\n"
            mock_run.return_value.returncode = 0
            _get_cc_version()
        assert mock_run.call_args.kwargs.get("stdin") is _sp.DEVNULL


class TestIsSubscriptionAuth:
    def test_api_key_helper_is_not_subscription(self):
        assert _is_subscription_auth({"apiKeyHelper": "my-helper"}) is False

    def test_cloud_provider_is_not_subscription(self):
        assert _is_subscription_auth({"cloudProviderId": "aws"}) is False

    def test_empty_settings_no_snapshots_is_not_subscription(self, tmp_path):
        with patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            result = _is_subscription_auth({})
        assert result is False

    def test_snapshot_with_1h_ttl_is_subscription(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        snap = sessions / "abc123-statusline.json"
        snap.write_text(
            json.dumps({"prompt_cache": {"ttl": "1h"}}), encoding="utf-8"
        )
        with patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            result = _is_subscription_auth({})
        assert result is True

    def test_snapshot_with_5m_ttl_is_not_subscription(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        snap = sessions / "abc123-statusline.json"
        snap.write_text(
            json.dumps({"prompt_cache": {"ttl": "5m"}}), encoding="utf-8"
        )
        with patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            result = _is_subscription_auth({})
        assert result is False


class TestCheckSessionPinning:
    def test_missing_both_keys_fires_two_findings(self, tmp_path):
        """No env keys → two sessionPinning findings."""
        with patch("ccgate.scripts.shape._get_cc_version", return_value=None), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            findings = _check_session_pinning({})
        checks = [f["check"] for f in findings]
        assert checks.count("sessionPinning") == 2

    def test_both_keys_present_no_finding(self):
        """Both env keys set → no findings."""
        settings = {"env": {
            "ENABLE_PROMPT_CACHING_1H": "1",
            "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
        }}
        with patch("ccgate.scripts.shape._get_cc_version", return_value=None), \
             patch("ccgate.scripts.shape._is_subscription_auth", return_value=False):
            findings = _check_session_pinning(settings)
        assert not any(f["check"] == "sessionPinning" for f in findings)

    def test_subscription_auth_skips_check(self, tmp_path):
        """Subscription auth → no sessionPinning findings regardless of env."""
        with patch("ccgate.scripts.shape._is_subscription_auth", return_value=True):
            findings = _check_session_pinning({})
        assert not any(f["check"] == "sessionPinning" for f in findings)

    # G18: version floor boundaries
    def test_below_floor_108_emits_version_gap_not_fix(self, tmp_path):
        """CC < 2.1.108 → version_gap finding, ENABLE_PROMPT_CACHING_1H absent from findings."""
        with patch("ccgate.scripts.shape._get_cc_version", return_value=(2, 1, 100)), \
             patch("ccgate.scripts.shape._is_subscription_auth", return_value=False), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            findings = _check_session_pinning({})
        assert any(f["check"] == "version_gap" for f in findings)
        # sessionPinning finding for the missing key should be absent (key is below floor)
        pinning = [f for f in findings if f["check"] == "sessionPinning"
                   and "ENABLE_PROMPT_CACHING_1H" in f.get("message", "")]
        assert len(pinning) == 0

    def test_at_floor_108_below_257_stages_first_key_only(self, tmp_path):
        """CC = 2.1.108 → ENABLE_PROMPT_CACHING_1H staged; CLAUDE_CODE_SUBAGENT omits (version_gap)."""
        with patch("ccgate.scripts.shape._get_cc_version", return_value=(2, 1, 108)), \
             patch("ccgate.scripts.shape._is_subscription_auth", return_value=False), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            findings = _check_session_pinning({})
        checks = [f["check"] for f in findings]
        # First key: sessionPinning (floor met); second key: version_gap (below 2.1.257)
        assert "sessionPinning" in checks
        assert "version_gap" in checks

    def test_at_floor_257_stages_both_keys(self, tmp_path):
        """CC = 2.1.257 → both keys staged as sessionPinning (no version_gap)."""
        with patch("ccgate.scripts.shape._get_cc_version", return_value=(2, 1, 257)), \
             patch("ccgate.scripts.shape._is_subscription_auth", return_value=False), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            findings = _check_session_pinning({})
        checks = [f["check"] for f in findings]
        assert checks.count("sessionPinning") == 2
        assert "version_gap" not in checks

    def test_no_version_detected_stages_both_keys(self, tmp_path):
        """_get_cc_version() returns None → assume compatible, stage both keys."""
        with patch("ccgate.scripts.shape._get_cc_version", return_value=None), \
             patch("ccgate.scripts.shape._is_subscription_auth", return_value=False), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            findings = _check_session_pinning({})
        checks = [f["check"] for f in findings]
        assert checks.count("sessionPinning") == 2


class TestStagePinEnv:
    def test_stage_fixes_includes_both_env_keys(self, tmp_path):
        """stage_fixes() emits json_set for both env keys when both absent."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({}), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path), \
             patch("ccgate.scripts.shape._get_cc_version", return_value=(2, 2, 0)), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        pin_fixes = [f for f in report["fixes"] if f["check"] == "sessionPinning"]
        keys = {f["key"] for f in pin_fixes}
        assert "env.ENABLE_PROMPT_CACHING_1H" in keys
        assert "env.CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL" in keys
        assert all(f["action"] == "json_set" for f in pin_fixes)
        assert all(f["file"] == "~/.claude/settings.json" for f in pin_fixes)
        assert all("\\" not in f["file"] for f in pin_fixes)

    def test_apply_writes_env_block(self, tmp_path):
        """apply_fixes() writes both keys into settings.json env block."""
        (tmp_path / ".claude").mkdir()
        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(json.dumps({}), encoding="utf-8")
        report = {
            "generated": "2026-09-21T00:00:00Z",
            "cwd": str(tmp_path).replace("\\", "/"),
            "ccgate_version": "0.1.0",
            "fixes": [
                {
                    "check": "sessionPinning",
                    "file": "~/.claude/settings.json",
                    "action": "json_set",
                    "key": "env.ENABLE_PROMPT_CACHING_1H",
                    "value": "1",
                    "description": "Pin 1-hour prompt cache TTL",
                },
                {
                    "check": "sessionPinning",
                    "file": "~/.claude/settings.json",
                    "action": "json_set",
                    "key": "env.CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
                    "value": "1h",
                    "description": "Pin subagent prompt cache TTL to 1 hour",
                },
            ],
            "skipped": [],
            "applied": False,
            "applied_at": None,
            "startup_chars_before": None,
            "startup_chars_after": None,
            "startup_tokens_before_approx": None,
            "startup_tokens_after_approx": None,
            "startup_delta_approx": None,
        }
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            apply_fixes(report)
        result = json.loads(settings_file.read_text(encoding="utf-8"))
        assert result["env"]["ENABLE_PROMPT_CACHING_1H"] == "1"
        assert result["env"]["CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"] == "1h"

    def test_stage_omits_already_set_key(self, tmp_path):
        """No fix emitted for a key that is already set."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps({"env": {
                "ENABLE_PROMPT_CACHING_1H": "1",
                "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
            }}),
            encoding="utf-8",
        )
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path), \
             patch("ccgate.scripts.shape._get_cc_version", return_value=(2, 2, 0)), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        pin_fixes = [f for f in report["fixes"] if f["check"] == "sessionPinning"]
        assert len(pin_fixes) == 0

    def test_below_floor_key_absent_from_fixes(self, tmp_path):
        """CC < 2.1.108 → ENABLE_PROMPT_CACHING_1H absent from fixes list (G18)."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({}), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path), \
             patch("ccgate.scripts.shape._get_cc_version", return_value=(2, 1, 100)), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        pin_keys = [f["key"] for f in report["fixes"] if f["check"] == "sessionPinning"]
        assert "env.ENABLE_PROMPT_CACHING_1H" not in pin_keys
        assert "env.CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL" not in pin_keys

    def test_apply_falls_back_on_permission_error(self, tmp_path):
        """Windows WinError 5: os.replace() failure falls back to direct write."""
        import os
        from ccgate.scripts.shape import _safe_json_patch

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"existing": True}), encoding="utf-8")

        with patch("ccgate.scripts.shape.os.replace", side_effect=PermissionError("WinError 5")):
            _safe_json_patch(settings_file, "set", "env.ENABLE_PROMPT_CACHING_1H", "1")

        result = json.loads(settings_file.read_text(encoding="utf-8"))
        assert result["env"]["ENABLE_PROMPT_CACHING_1H"] == "1"
        assert result["existing"] is True
        assert not (tmp_path / "settings.json.tmp").exists()
