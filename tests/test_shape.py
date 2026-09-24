import tempfile
from pathlib import Path

from ccgate.config import DEFAULTS
from ccgate.scripts.shape import (
    _check_claudemd_lines, _check_skill_listing,
    _check_skill_side_effects, _check_claudemd_excludes,
    run_shape,
)
from ccgate.scripts.shape import (
    _check_cache_ttl, _check_deny_reads, _check_worktree_sparse,
    _check_output_caps, _load_settings,
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


class TestCacheTtl:
    def test_no_api_key_indicators_no_finding(self, tmp_path):
        # settings.json with no apiKeyHelper/cloudProviderId
        settings = {"hitRatioFloor": 0.85}
        findings = _check_cache_ttl(settings)
        assert not any(f["check"] == "cacheTtl" for f in findings)

    def test_api_key_helper_without_ttl_flagged(self, tmp_path):
        settings = {"apiKeyHelper": "op://vault/key", "promptCacheTtl": None}
        findings = _check_cache_ttl(settings)
        assert any(f["check"] == "cacheTtl" and f["severity"] == "warning"
                   for f in findings)

    def test_cloud_provider_without_ttl_flagged(self):
        settings = {"cloudProviderId": "vertex"}
        findings = _check_cache_ttl(settings)
        assert any(f["check"] == "cacheTtl" for f in findings)

    def test_api_key_with_ttl_set_passes(self):
        settings = {"apiKeyHelper": "op://vault/key", "promptCacheTtl": "1h"}
        findings = _check_cache_ttl(settings)
        assert not any(f["check"] == "cacheTtl" for f in findings)


class TestDenyReads:
    def test_no_sensitive_dirs_no_finding(self, tmp_path):
        settings: dict = {}
        findings = _check_deny_reads(str(tmp_path), settings)
        assert not any(f["check"] == "denyReads" for f in findings)

    def test_dist_dir_without_deny_rule_flagged(self, tmp_path):
        (tmp_path / "dist").mkdir()
        settings: dict = {}
        findings = _check_deny_reads(str(tmp_path), settings)
        assert any(f["check"] == "denyReads" and f["severity"] == "warning"
                   for f in findings)

    def test_dist_dir_with_deny_rule_passes(self, tmp_path):
        (tmp_path / "dist").mkdir()
        settings = {"permissions": {"deny": ["Read(dist/**/*)", "Bash(rm:-rf)"]}}
        findings = _check_deny_reads(str(tmp_path), settings)
        assert not any(f["check"] == "denyReads" for f in findings)

    def test_generated_file_without_deny_flagged(self, tmp_path):
        (tmp_path / "api.generated.ts").write_text("// generated")
        settings: dict = {}
        findings = _check_deny_reads(str(tmp_path), settings)
        assert any(f["check"] == "denyReads" for f in findings)

    def test_generated_file_with_deny_rule_passes(self, tmp_path):
        (tmp_path / "api.generated.ts").write_text("// generated")
        settings = {"permissions": {"deny": ["Read(**/*.generated.*/***)"]}}
        findings = _check_deny_reads(str(tmp_path), settings)
        assert not any(f["check"] == "denyReads" for f in findings)


class TestWorktreeSparse:
    def test_not_in_worktree_no_finding(self, tmp_path):
        # .git is a directory — not a worktree
        (tmp_path / ".git").mkdir()
        settings: dict = {}
        findings = _check_worktree_sparse(str(tmp_path), settings)
        assert not any(f["check"] == "worktreeSparse" for f in findings)

    def test_worktree_single_package_no_finding(self, tmp_path):
        # .git is a file — inside a worktree; single package
        (tmp_path / ".git").write_text("gitdir: ../../.git/worktrees/my-wt")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        settings: dict = {}
        findings = _check_worktree_sparse(str(tmp_path), settings)
        assert not any(f["check"] == "worktreeSparse" for f in findings)

    def test_worktree_monorepo_without_sparse_flagged(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: ../../.git/worktrees/my-wt")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='root'\n")
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\nname='core'\n")
        settings: dict = {}
        findings = _check_worktree_sparse(str(tmp_path), settings)
        assert any(f["check"] == "worktreeSparse" and f["severity"] == "warning"
                   for f in findings)

    def test_worktree_monorepo_with_sparse_passes(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: ../../.git/worktrees/my-wt")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='root'\n")
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\nname='core'\n")
        settings = {"worktree": {"sparsePaths": ["packages/core"]}}
        findings = _check_worktree_sparse(str(tmp_path), settings)
        assert not any(f["check"] == "worktreeSparse" for f in findings)


class TestOutputCaps:
    def test_always_returns_empty_in_phase0(self, tmp_path):
        # Phase 0 stub — no session history available
        from ccgate.scripts.shape import _check_output_caps
        assert _check_output_caps(str(tmp_path)) == []
