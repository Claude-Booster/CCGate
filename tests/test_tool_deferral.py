"""test_tool_deferral.py — G21: tool deferral enforcement shape checks and fix staging."""
from __future__ import annotations

import json
from unittest.mock import patch

from ccgate.scripts.shape import (
    _check_tool_deferral,
    _is_first_party_base_url,
    _load_mcp_configs,
    apply_fixes,
    stage_fixes,
)


class TestIsFirstPartyBaseUrl:
    def test_anthropic_api_is_first_party(self):
        assert _is_first_party_base_url("https://api.anthropic.com") is True

    def test_anthropic_subdomain_is_first_party(self):
        assert _is_first_party_base_url("https://staging.anthropic.com/v1") is True

    def test_proxy_is_not_first_party(self):
        assert _is_first_party_base_url("https://proxy.company.com") is False

    def test_aws_bedrock_is_not_first_party(self):
        assert _is_first_party_base_url("https://bedrock.us-east-1.amazonaws.com") is False


class TestLoadMcpConfigs:
    def test_returns_empty_when_no_files(self, tmp_path):
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            result = _load_mcp_configs(str(tmp_path))
        assert result == []

    def test_reads_project_mcp_json(self, tmp_path):
        mcp_data = {"mcpServers": {"my-server": {"command": "node"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            result = _load_mcp_configs(str(tmp_path))
        assert len(result) == 1
        _, data = result[0]
        assert "mcpServers" in data

    def test_reads_claude_mcp_json(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        mcp_data = {"mcpServers": {"another": {"command": "python"}}}
        (tmp_path / ".claude" / "mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            result = _load_mcp_configs(str(tmp_path))
        assert len(result) == 1

    def test_skips_malformed_json(self, tmp_path):
        (tmp_path / ".mcp.json").write_text("{bad json", encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            result = _load_mcp_configs(str(tmp_path))
        assert result == []


class TestCheckToolDeferral:
    def test_gateway_url_without_tool_search_is_error(self):
        """G21: non-first-party base URL without ENABLE_TOOL_SEARCH → error severity."""
        settings = {"env": {"ANTHROPIC_BASE_URL": "https://proxy.company.com"}}
        findings = _check_tool_deferral(settings, cwd=None)
        assert any(
            f["check"] == "toolDeferral" and f["severity"] == "error"
            for f in findings
        )

    def test_first_party_url_no_finding(self):
        """api.anthropic.com is first-party → no finding."""
        settings = {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}}
        findings = _check_tool_deferral(settings, cwd=None)
        assert not any(f["check"] == "toolDeferral" for f in findings)

    def test_gateway_with_enable_tool_search_no_finding(self):
        """Gateway + ENABLE_TOOL_SEARCH=true → no finding."""
        settings = {"env": {
            "ANTHROPIC_BASE_URL": "https://proxy.company.com",
            "ENABLE_TOOL_SEARCH": "true",
        }}
        findings = _check_tool_deferral(settings, cwd=None)
        assert not any(f["check"] == "toolDeferral" for f in findings)

    def test_no_base_url_no_finding(self):
        """No ANTHROPIC_BASE_URL → no finding."""
        findings = _check_tool_deferral({}, cwd=None)
        assert not any(f["check"] == "toolDeferral" for f in findings)

    def test_always_load_true_is_warning(self, tmp_path):
        """alwaysLoad: true on an MCP server entry → warning severity."""
        mcp_data = {"mcpServers": {"my-server": {"command": "node", "alwaysLoad": True}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            findings = _check_tool_deferral({}, cwd=str(tmp_path))
        assert any(
            f["check"] == "toolDeferral" and f["severity"] == "warning"
            for f in findings
        )

    def test_always_load_false_no_finding(self, tmp_path):
        """alwaysLoad: false is fine."""
        mcp_data = {"mcpServers": {"my-server": {"command": "node", "alwaysLoad": False}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            findings = _check_tool_deferral({}, cwd=str(tmp_path))
        assert not any(f["check"] == "toolDeferral" for f in findings)


class TestStageToolDeferral:
    def test_gateway_fix_in_fixes_list(self, tmp_path):
        """Gateway URL → ENABLE_TOOL_SEARCH fix appears in fixes, not skipped."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://proxy.company.com"}}),
            encoding="utf-8",
        )
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path), \
             patch("ccgate.scripts.shape._get_cc_version", return_value=None), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        td_fixes = [f for f in report["fixes"] if f["check"] == "toolDeferral"]
        assert len(td_fixes) == 1
        assert td_fixes[0]["key"] == "env.ENABLE_TOOL_SEARCH"
        assert td_fixes[0]["value"] == "true"
        assert td_fixes[0]["action"] == "json_set"
        assert td_fixes[0]["file"] == "~/.claude/settings.json"
        assert "\\" not in td_fixes[0]["file"]

    def test_always_load_in_skipped_not_fixes(self, tmp_path):
        """alwaysLoad entry → skipped (not auto-fixed), absent from fixes."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({}), encoding="utf-8")
        mcp_data = {"mcpServers": {"my-server": {"command": "node", "alwaysLoad": True}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path), \
             patch("ccgate.scripts.shape._get_cc_version", return_value=None), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        assert any(s["check"] == "toolDeferral" for s in report["skipped"])
        assert not any(f["check"] == "toolDeferral" for f in report["fixes"])

    def test_apply_writes_enable_tool_search(self, tmp_path):
        """apply_fixes() writes ENABLE_TOOL_SEARCH into settings.json env block."""
        (tmp_path / ".claude").mkdir()
        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://proxy.company.com"}}),
            encoding="utf-8",
        )
        report = {
            "generated": "2026-09-21T00:00:00Z",
            "cwd": str(tmp_path).replace("\\", "/"),
            "ccgate_version": "0.1.0",
            "fixes": [{
                "check": "toolDeferral",
                "file": "~/.claude/settings.json",
                "action": "json_set",
                "key": "env.ENABLE_TOOL_SEARCH",
                "value": "true",
                "description": "Enable tool search deferral for non-first-party base URL (A3)",
            }],
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
        assert result["env"]["ENABLE_TOOL_SEARCH"] == "true"
        assert result["env"]["ANTHROPIC_BASE_URL"] == "https://proxy.company.com"

    def test_enforced_tool_deferral_disabled_by_config(self, tmp_path):
        """enforceToolDeferral=false in config → no toolDeferral fix staged."""
        from ccgate.config import DEFAULTS
        config_off = {**DEFAULTS, "enforceToolDeferral": False}
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://proxy.company.com"}}),
            encoding="utf-8",
        )
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path), \
             patch("ccgate.scripts.shape._get_cc_version", return_value=None), \
             patch("ccgate.scripts.shape.ccgate_home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path), config=config_off)
        assert not any(f["check"] == "toolDeferral" for f in report["fixes"])


class TestSummarizeToolDeferral:
    def test_summary_with_mixed_servers(self, tmp_path):
        mcp_data = {"mcpServers": {
            "deferred-server": {"command": "node"},
            "always-server": {"command": "python", "alwaysLoad": True},
        }}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            from ccgate.scripts.shape import _summarize_tool_deferral
            findings = _summarize_tool_deferral(str(tmp_path))
        assert len(findings) == 1
        assert findings[0]["check"] == "toolDeferralSummary"
        assert "2 total" in findings[0]["message"]
        assert "1 deferred" in findings[0]["message"]
        assert "1 always-loaded" in findings[0]["message"]

    def test_no_mcp_configs_no_summary(self, tmp_path):
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            from ccgate.scripts.shape import _summarize_tool_deferral
            findings = _summarize_tool_deferral(str(tmp_path))
        assert findings == []
