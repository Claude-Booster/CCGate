import tempfile
from pathlib import Path

from ccgate.scripts.shape import (
    _check_cache_ttl,
    _check_claudemd_excludes,
    _check_claudemd_lines,
    _check_deny_reads,
    _check_large_read_sinks,
    _check_output_caps,
    _check_skill_listing,
    _check_skill_side_effects,
    _check_worktree_sparse,
    stage_fixes,
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


class TestLargeReadSinks:
    """Warn-only (never auto-fixed) large lockfiles + bundles."""

    CFG = {"denyReadsMinBytes": 1000}

    def test_large_lockfile_flagged_with_suggested_rule(self, tmp_path):
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 2000)
        findings = _check_large_read_sinks(str(tmp_path), {}, self.CFG)
        hits = [f for f in findings if f["check"] == "denyReads"]
        assert hits and hits[0]["severity"] == "warning"
        assert "Read(**/pnpm-lock.yaml)" in hits[0]["message"]

    def test_small_lockfile_not_flagged(self, tmp_path):
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 100)
        findings = _check_large_read_sinks(str(tmp_path), {}, self.CFG)
        assert not findings

    def test_lockfile_already_denied_not_flagged(self, tmp_path):
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 2000)
        settings = {"permissions": {"deny": ["Read(**/pnpm-lock.yaml)"]}}
        findings = _check_large_read_sinks(str(tmp_path), settings, self.CFG)
        assert not findings

    def test_large_minjs_outside_dist_flagged(self, tmp_path):
        (tmp_path / "public").mkdir()
        (tmp_path / "public" / "lib.min.js").write_text("x" * 2000)
        findings = _check_large_read_sinks(str(tmp_path), {}, self.CFG)
        hits = [f for f in findings if f["check"] == "denyReads"]
        assert hits and "min.js" in hits[0]["message"]

    def test_bundle_specific_rule_does_not_suppress_other_bundle(self, tmp_path):
        # a deny rule for one .min.js must NOT mark every .min.js as covered
        (tmp_path / "public").mkdir()
        (tmp_path / "public" / "app.min.js").write_text("x" * 2000)
        settings = {"permissions": {"deny": ["Read(vendor/jquery.min.js)"]}}
        findings = _check_large_read_sinks(str(tmp_path), settings, self.CFG)
        assert any(f["check"] == "denyReads" for f in findings)

    def test_lockfile_specific_path_rule_does_not_suppress_other(self, tmp_path):
        # denying one package's lockfile must NOT suppress a different lockfile
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 2000)
        settings = {"permissions": {"deny": ["Read(packages/a/pnpm-lock.yaml)"]}}
        findings = _check_large_read_sinks(str(tmp_path), settings, self.CFG)
        assert any(f["check"] == "denyReads" for f in findings)

    def test_lockfile_exact_path_rule_suppresses_that_file(self, tmp_path):
        # denying the exact file being scanned DOES cover it
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 2000)
        settings = {"permissions": {"deny": ["Read(pnpm-lock.yaml)"]}}
        findings = _check_large_read_sinks(str(tmp_path), settings, self.CFG)
        assert not findings

    def test_minjs_inside_dist_not_flagged(self, tmp_path):
        # dist/ is already auto-denied by the existing denyReads fix — don't double-report
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "bundle.min.js").write_text("x" * 2000)
        findings = _check_large_read_sinks(str(tmp_path), {}, self.CFG)
        assert not findings

    def test_lockfile_in_node_modules_not_flagged(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "package-lock.json").write_text("x" * 2000)
        findings = _check_large_read_sinks(str(tmp_path), {}, self.CFG)
        assert not findings

    def test_message_states_size_not_evidence_basis(self, tmp_path):
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 2000)
        findings = _check_large_read_sinks(str(tmp_path), {}, self.CFG)
        msg = findings[0]["message"]
        assert "~" in msg                       # token figure is approximate
        assert "based on" in msg.lower() and "size" in msg.lower()

    def test_gitignored_lockfile_skipped(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True,
                       stdin=subprocess.DEVNULL)
        (tmp_path / ".gitignore").write_text("pnpm-lock.yaml\n")
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 2000)
        findings = _check_large_read_sinks(str(tmp_path), {}, self.CFG)
        assert not findings


class TestWarnOnlyNotStaged:
    """The load-bearing guard: large read sinks must NEVER be auto-staged by --fix."""

    def test_stage_fixes_does_not_emit_large_read_sinks(self, tmp_path):
        (tmp_path / "pnpm-lock.yaml").write_text("x" * 60000)
        report = stage_fixes(str(tmp_path), {"denyReadsMinBytes": 1000})
        assert not any("pnpm-lock.yaml" in str(f.get("value", "")) for f in report["fixes"])


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
        assert _check_output_caps(str(tmp_path)) == []


class TestHookInterpreters:
    def _bare(self, cmd):
        from ccgate.scripts.shape import _command_uses_bare_alias_python
        return _command_uses_bare_alias_python(cmd)

    def test_bare_python_detected(self):
        assert self._bare("python -m ccgate.hooks.pre_tool")
        assert self._bare("python3 -m foo")
        assert self._bare("pythonw -m foo")

    def test_full_path_python_not_flagged(self):
        assert not self._bare(
            r"C:\Users\me\AppData\Local\Python\pythoncore-3.14-64\python.exe -m ccgate.hooks.pre_tool"
        )
        assert not self._bare("/c/Users/me/python/python.exe -m foo")

    def test_pythoncore_substring_not_flagged(self):
        # 'python' inside 'pythoncore-3.14-64' must not match
        assert not self._bare("pythoncore-3.14-64 -m foo")

    def test_embedded_bare_python_in_pwsh_flagged(self):
        assert self._bare(
            'pwsh -NonInteractive -Command "$r=(python -m ccgate.dispatch shape 2>&1|Out-String)"'
        )

    def test_no_python_not_flagged(self):
        assert not self._bare("pwsh -NonInteractive -File script.ps1")
        assert not self._bare("node index.js")

    def test_iter_hook_commands(self):
        from ccgate.scripts.shape import _iter_hook_commands
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Edit", "hooks": [
                        {"type": "command", "command": "python -m a"},
                        {"type": "prompt", "prompt": "ignore me"},
                    ]},
                ],
                "SessionEnd": [
                    {"hooks": [{"type": "command", "command": "node b.js"}]},
                ],
            }
        }
        got = sorted(cmd for _, cmd in _iter_hook_commands(settings))
        assert got == ["node b.js", "python -m a"]

    def test_flags_bare_python_when_alias_present(self, monkeypatch):
        import ccgate.scripts.shape as shape
        monkeypatch.setattr(shape, "_resolves_to_windowsapps_alias", lambda i: True)
        settings = {"hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [
                {"type": "command", "command": "python -m ccgate.hooks.pre_tool"},
            ]},
        ]}}
        findings = shape._check_hook_interpreters(settings)
        assert any(f["check"] == "hookInterpreter" and f["severity"] == "warning"
                   for f in findings)

    def test_no_finding_when_no_alias_on_machine(self, monkeypatch):
        import ccgate.scripts.shape as shape
        monkeypatch.setattr(shape, "_resolves_to_windowsapps_alias", lambda i: False)
        settings = {"hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [
                {"type": "command", "command": "python -m ccgate.hooks.pre_tool"},
            ]},
        ]}}
        assert shape._check_hook_interpreters(settings) == []

    def test_no_finding_for_full_path_hooks(self, monkeypatch):
        import ccgate.scripts.shape as shape
        monkeypatch.setattr(shape, "_resolves_to_windowsapps_alias", lambda i: True)
        settings = {"hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [
                {"type": "command",
                 "command": r"C:\Python\python.exe -m ccgate.hooks.pre_tool"},
            ]},
        ]}}
        assert shape._check_hook_interpreters(settings) == []


class TestRestartNotice:
    def test_settings_json_fix_needs_restart(self):
        from ccgate.scripts.shape import _needs_restart_notice
        assert _needs_restart_notice([{"file": "~/.claude/settings.json"}])

    def test_hooks_json_fix_needs_restart(self):
        from ccgate.scripts.shape import _needs_restart_notice
        assert _needs_restart_notice([{"file": "/x/.claude/hooks.json"}])

    def test_claudemd_fix_no_restart(self):
        from ccgate.scripts.shape import _needs_restart_notice
        assert not _needs_restart_notice([{"file": "CLAUDE.md"}])

    def test_empty_no_restart(self):
        from ccgate.scripts.shape import _needs_restart_notice
        assert not _needs_restart_notice([])
