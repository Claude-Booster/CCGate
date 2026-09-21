import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from ccgate.scripts.shape import stage_fixes


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

    def test_report_schema(self, tmp_path):
        """Staged report validates against schema/ccgate.fix-report.schema.json (G9)."""
        schema_path = Path(__file__).parent.parent / "schema" / "ccgate.fix-report.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        with patch("ccgate.scripts.shape.Path.home", return_value=tmp_path):
            report = stage_fixes(cwd=str(tmp_path))
        jsonschema.validate(instance=report, schema=schema)  # raises on failure
