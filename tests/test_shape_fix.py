import json
import re
import warnings
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from ccgate.scripts.shape import apply_fixes, stage_fixes


class TestStageFixes:
    def test_stage_cachettl(self, tmp_path):
        """stage_fixes() emits json_set entry for cacheTtl when promptCacheTtl absent."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps({"apiKeyHelper": "my-helper"}), encoding="utf-8"
        )
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        fixes = [f for f in report["fixes"] if f["check"] == "cacheTtl"]
        assert len(fixes) == 1
        assert fixes[0]["action"] == "json_set"
        assert fixes[0]["key"] == "promptCacheTtl"
        assert fixes[0]["value"] == "1h"
        assert fixes[0]["file"] == "~/.claude/settings.json"

    def test_stage_deny_reads(self, tmp_path):
        """stage_fixes() emits json_append entries for each uncovered sensitive dir."""
        (tmp_path / "dist").mkdir()
        (tmp_path / ".claude").mkdir()
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        fixes = [f for f in report["fixes"] if f["check"] == "denyReads"]
        assert len(fixes) >= 1
        assert all(f["action"] == "json_append" for f in fixes)
        assert any(f["value"] == "Read(dist/**/*)" for f in fixes)

    def test_stage_claudemd_stub(self, tmp_path):
        """stage_fixes() emits a create entry for each unscoped sub-package."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='root'\n", encoding="utf-8")
        pkg = tmp_path / "packages" / "api"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\nname='api'\n", encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        fixes = [f for f in report["fixes"] if f["check"] == "claudeMdExcludes"]
        assert len(fixes) >= 1
        assert fixes[0]["action"] == "create"
        assert fixes[0]["content"] == "# api\n"

    def test_skipped_claudemd_lines(self, tmp_path):
        """Over-limit CLAUDE.md appears in skipped, never in fixes."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text("line\n" * 201, encoding="utf-8")
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        assert any(s["check"] == "claudeMdLines" for s in report["skipped"])
        assert not any(f["check"] == "claudeMdLines" for f in report["fixes"])

    def test_fix_skills_gated(self, tmp_path):
        """Skill fix absent without --fix-skills; present with it."""
        skill_dir = tmp_path / ".claude" / "skills" / "my-deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-deploy\ndescription: deploys stuff\n---\nDo it.\n",
            encoding="utf-8",
        )
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            no_flag = stage_fixes(cwd=str(tmp_path), fix_skills=False)
            with_flag = stage_fixes(cwd=str(tmp_path), fix_skills=True)
        assert not any(f["check"] == "skillSideEffects" for f in no_flag["fixes"])
        assert len([f for f in with_flag["fixes"] if f["check"] == "skillSideEffects"]) == 1
        skill_fix = next(f for f in with_flag["fixes"] if f["check"] == "skillSideEffects")
        assert skill_fix["action"] == "frontmatter_set"
        assert skill_fix["key"] == "disable-model-invocation"
        assert skill_fix["value"] is True
        # file field must be the full skill path — not just drive letter on Windows
        assert "SKILL.md" in skill_fix["file"]
        assert "\\" not in skill_fix["file"]  # G10: forward slashes

    def test_report_schema(self, tmp_path):
        """Staged report validates against schema/ccgate.fix-report.schema.json (G9)."""
        schema_path = Path(__file__).parent.parent / "schema" / "ccgate.fix-report.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        jsonschema.validate(instance=report, schema=schema)  # raises on failure

    def test_stage_fixes_generated_no_deprecation_and_valid_format(self, tmp_path):
        """stage_fixes() 'generated' field is produced without DeprecationWarning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
                report = stage_fixes(cwd=str(tmp_path))
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", report["generated"])


class TestApplyFixes:
    def _base_report(self, tmp_path, fixes=None):
        """Minimal report dict for apply tests."""
        return {
            "generated": "2026-09-21T00:00:00Z",
            "cwd": str(tmp_path).replace("\\", "/"),
            "ccgate_version": "0.1.0",
            "fixes": fixes or [],
            "skipped": [],
            "applied": False,
            "applied_at": None,
            "startup_chars_before": None,
            "startup_chars_after": None,
            "startup_tokens_before_approx": None,
            "startup_tokens_after_approx": None,
            "startup_delta_approx": None,
        }

    def test_apply_writes_global_settings(self, tmp_path):
        """apply_fixes() patches ~/.claude/settings.json with json_set."""
        (tmp_path / ".claude").mkdir()
        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(json.dumps({"apiKeyHelper": "my-helper"}), encoding="utf-8")
        report = self._base_report(tmp_path, fixes=[{
            "check": "cacheTtl",
            "file": "~/.claude/settings.json",
            "action": "json_set",
            "key": "promptCacheTtl",
            "value": "1h",
            "description": "Set prompt cache TTL to 1 hour",
        }])
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            updated = apply_fixes(report)
        result = json.loads(settings_file.read_text(encoding="utf-8"))
        assert result["promptCacheTtl"] == "1h"
        assert updated["applied"] is True
        assert updated["applied_at"] is not None

    def test_apply_writes_project_settings(self, tmp_path):
        """apply_fixes() patches .claude/settings.json with json_append."""
        (tmp_path / ".claude").mkdir()
        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(json.dumps({"permissions": {"deny": []}}), encoding="utf-8")
        report = self._base_report(tmp_path, fixes=[{
            "check": "denyReads",
            "file": ".claude/settings.json",
            "action": "json_append",
            "key": "permissions.deny",
            "value": "Read(dist/**/*)",
            "description": "Deny reads of dist/ directory",
        }])
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            apply_fixes(report)
        result = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "Read(dist/**/*)" in result["permissions"]["deny"]

    def test_apply_creates_stub_claudemd(self, tmp_path):
        """apply_fixes() creates a two-line stub CLAUDE.md file."""
        report = self._base_report(tmp_path, fixes=[{
            "check": "claudeMdExcludes",
            "file": "packages/api/CLAUDE.md",
            "action": "create",
            "content": "# api\n",
            "description": "Create stub CLAUDE.md for package api",
        }])
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            apply_fixes(report)
        stub = tmp_path / "packages" / "api" / "CLAUDE.md"
        assert stub.exists()
        assert stub.read_text(encoding="utf-8") == "# api\n"

    def test_apply_atomic_write(self, tmp_path):
        """Original settings file is unchanged if os.replace raises."""
        (tmp_path / ".claude").mkdir()
        settings_file = tmp_path / ".claude" / "settings.json"
        original = json.dumps({"apiKeyHelper": "my-helper"})
        settings_file.write_text(original, encoding="utf-8")
        report = self._base_report(tmp_path, fixes=[{
            "check": "cacheTtl",
            "file": "~/.claude/settings.json",
            "action": "json_set",
            "key": "promptCacheTtl",
            "value": "1h",
            "description": "Set prompt cache TTL to 1 hour",
        }])
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path), \
             patch("ccgate.scripts.shape.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                apply_fixes(report)
        assert settings_file.read_text(encoding="utf-8") == original

    def test_apply_idempotent(self, tmp_path):
        """Running apply_fixes() twice does not duplicate permissions.deny entries."""
        (tmp_path / ".claude").mkdir()
        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(json.dumps({"permissions": {"deny": []}}), encoding="utf-8")
        report = self._base_report(tmp_path, fixes=[{
            "check": "denyReads",
            "file": ".claude/settings.json",
            "action": "json_append",
            "key": "permissions.deny",
            "value": "Read(dist/**/*)",
            "description": "Deny reads of dist/ directory",
        }])
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            apply_fixes(report)
            apply_fixes(dict(report))  # second apply, same fixes
        result = json.loads(settings_file.read_text(encoding="utf-8"))
        assert result["permissions"]["deny"].count("Read(dist/**/*)" ) == 1

    def test_report_startup_delta(self, tmp_path):
        """Startup measurement fields are populated and delta >= 0 after apply."""
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            staged = stage_fixes(cwd=str(tmp_path))
        # All startup fields start null
        assert staged["startup_tokens_before_approx"] is None
        assert staged["startup_tokens_after_approx"] is None
        assert staged["startup_delta_approx"] is None
        # After apply, all fields are populated
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            applied = apply_fixes(staged)
        assert applied["startup_tokens_before_approx"] is not None
        assert applied["startup_tokens_after_approx"] is not None
        assert applied["startup_delta_approx"] is not None
        assert applied["startup_delta_approx"] >= 0  # I3: never negative

    def test_apply_frontmatter_set(self, tmp_path):
        """apply_fixes() injects frontmatter key into a SKILL.md (frontmatter_set action)."""
        skill_dir = tmp_path / ".claude" / "skills" / "my-deploy"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\nname: my-deploy\ndescription: deploys stuff\n---\nDo it.\n",
            encoding="utf-8",
        )
        rel = str(skill_file).replace("\\", "/")
        report = self._base_report(tmp_path, fixes=[{
            "check": "skillSideEffects",
            "file": rel,
            "action": "frontmatter_set",
            "key": "disable-model-invocation",
            "value": True,
            "description": "Add disable-model-invocation to SKILL.md",
        }])
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            apply_fixes(report)
        content = skill_file.read_text(encoding="utf-8")
        assert "disable-model-invocation: true" in content
        assert "name: my-deploy" in content
        assert "Do it." in content

    def test_report_paths_windows(self, tmp_path):
        """Report cwd and fix file fields use forward slashes (G10)."""
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        assert "\\" not in report["cwd"]
        for fix in report["fixes"]:
            assert "\\" not in fix["file"]

    def test_applied_at_has_no_deprecation_and_valid_format(self, tmp_path):
        """applied_at has valid format and is produced without DeprecationWarning."""
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            staged = stage_fixes(cwd=str(tmp_path))
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
                applied = apply_fixes(staged)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", applied["applied_at"])
