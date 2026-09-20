import tempfile
from pathlib import Path

from ccgate.config import DEFAULTS
from ccgate.scripts.shape import (
    _check_claudemd_lines, _check_skill_listing,
    _check_skill_side_effects, _check_claudemd_excludes,
    run_shape,
)


class TestClaudemdLines:
    def test_file_within_limit_passes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                         delete=False) as f:
            f.write("line\n" * 100)
            path = Path(f.name)
        findings = _check_claudemd_lines([path])
        assert not any(f["check"] == "claudeMdLines" and f["severity"] == "error"
                       for f in findings)

    def test_file_over_limit_produces_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                         delete=False) as f:
            f.write("line\n" * 201)
            path = Path(f.name)
        findings = _check_claudemd_lines([path])
        assert any(f["check"] == "claudeMdLines" and f["severity"] == "error"
                   for f in findings)


class TestSkillListing:
    def test_small_descriptions_pass(self):
        findings = _check_skill_listing(
            total_desc_chars=100,
            window_tokens=200_000,
            budget_fraction=0.01,
        )
        assert not any(f["check"] == "skillListing" and f["severity"] == "error"
                       for f in findings)

    def test_over_budget_produces_warning(self):
        findings = _check_skill_listing(
            total_desc_chars=10_000,
            window_tokens=200_000,
            budget_fraction=0.01,
        )
        assert any(f["check"] == "skillListing" for f in findings)


class TestSkillSideEffects:
    def test_deploy_skill_without_disable_flagged(self, tmp_path):
        skill_dir = tmp_path / ".claude" / "skills" / "my-deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-deploy\ndescription: deploys stuff\n---\nDo it.\n",
            encoding="utf-8",
        )
        findings = _check_skill_side_effects(str(tmp_path))
        assert any(f["check"] == "skillSideEffects" and f["severity"] == "error"
                   for f in findings)

    def test_deploy_skill_with_disable_not_flagged(self, tmp_path):
        skill_dir = tmp_path / ".claude" / "skills" / "my-deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-deploy\ndescription: deploys\ndisable-model-invocation: true\n---\nDo it.\n",
            encoding="utf-8",
        )
        findings = _check_skill_side_effects(str(tmp_path))
        assert not any(
            f["check"] == "skillSideEffects" and str(tmp_path) in f["message"]
            for f in findings
        )

    def test_analysis_skill_not_flagged(self, tmp_path):
        skill_dir = tmp_path / ".claude" / "skills" / "analysis"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: analysis\ndescription: analyses things\n---\nDo it.\n",
            encoding="utf-8",
        )
        findings = _check_skill_side_effects(str(tmp_path))
        assert not any(
            f["check"] == "skillSideEffects" and str(tmp_path) in f["message"]
            for f in findings
        )

    def test_commit_publish_send_all_flagged(self, tmp_path):
        for kw in ["commit", "publish", "send"]:
            skill_dir = tmp_path / ".claude" / "skills" / kw
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {kw}-tool\ndescription: does {kw}\n---\nDo it.\n",
                encoding="utf-8",
            )
        findings = _check_skill_side_effects(str(tmp_path))
        flagged_checks = [
            f for f in findings
            if f["check"] == "skillSideEffects" and str(tmp_path) in f["message"]
        ]
        assert len(flagged_checks) == 3


class TestClaudemdExcludes:
    def test_single_package_no_finding(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'\n")
        findings = _check_claudemd_excludes(str(tmp_path))
        assert not any(f["check"] == "claudeMdExcludes" for f in findings)

    def test_multi_package_no_scoped_claudemd_flagged(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'root'\n")
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\nname = 'core'\n")
        findings = _check_claudemd_excludes(str(tmp_path))
        assert any(f["check"] == "claudeMdExcludes" and f["severity"] == "warning"
                   for f in findings)

    def test_multi_package_with_scoped_claudemd_passes(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'root'\n")
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\nname = 'core'\n")
        # Add scoped CLAUDE.md in the package directory
        (pkg / "CLAUDE.md").write_text("# scoped\n")
        findings = _check_claudemd_excludes(str(tmp_path))
        assert not any(f["check"] == "claudeMdExcludes" for f in findings)
