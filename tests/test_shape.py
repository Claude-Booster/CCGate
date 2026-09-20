import tempfile
from pathlib import Path

from ccgate.config import DEFAULTS
from ccgate.scripts.shape import _check_claudemd_lines, _check_skill_listing, run_shape


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
