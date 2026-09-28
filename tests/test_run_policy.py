from pathlib import Path
from ccgate.run.policy import load_contextignore, path_is_ignored


def test_absent_contextignore_returns_empty(tmp_path):
    assert load_contextignore(tmp_path) == []


def test_blank_and_comment_lines_ignored(tmp_path):
    (tmp_path / ".contextignore").write_text("# comment\n\nsecrets/*.txt\n", encoding="utf-8")
    assert load_contextignore(tmp_path) == ["secrets/*.txt"]


def test_glob_matches_full_path_and_basename(tmp_path):
    pats = ["secrets/*.txt", "*.key"]
    assert path_is_ignored("secrets/a.txt", pats) is True
    assert path_is_ignored("/repo/private.key", pats) is True     # basename match
    assert path_is_ignored("src/main.py", pats) is False


def test_empty_patterns_never_match():
    assert path_is_ignored("anything.txt", []) is False
