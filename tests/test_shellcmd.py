import pytest
from ccgate.run.shellcmd import (
    compile_prefixes, command_matches, first_token, strip_runner_prefixes,
)


def test_strip_bare_runners():
    assert strip_runner_prefixes("sudo cat x") == "cat x"
    assert strip_runner_prefixes("time cat x") == "cat x"
    assert strip_runner_prefixes("command cat x") == "cat x"
    assert strip_runner_prefixes("nice cat x") == "cat x"


def test_strip_env_assignments_and_bare_env():
    assert strip_runner_prefixes("env FOO=1 cat x") == "cat x"
    assert strip_runner_prefixes("env FOO=1 BAR=2 cat x") == "cat x"
    assert strip_runner_prefixes("env cat x") == "cat x"
    assert strip_runner_prefixes("env -i cat x") == "cat x"


def test_strip_sudo_value_flags():
    assert strip_runner_prefixes("sudo -u foo cat x") == "cat x"
    assert strip_runner_prefixes("sudo -n cat x") == "cat x"


def test_strip_recursive_with_cap():
    assert strip_runner_prefixes("sudo env FOO=1 cat x") == "cat x"


def test_strip_leaves_non_runner_untouched():
    assert strip_runner_prefixes("cat x") == "cat x"
    assert strip_runner_prefixes("pytest -q") == "pytest -q"
    assert strip_runner_prefixes("") == ""


def test_strip_b1b_inputs_for_future_retrofit():
    # B1b's F3 truncation is defeated by these today; the retrofit will call this helper.
    assert strip_runner_prefixes("sudo pytest") == "pytest"
    assert strip_runner_prefixes("env CI=1 cargo test") == "cargo test"
    assert strip_runner_prefixes("time npm test") == "npm test"


def test_first_token():
    assert first_token("cat foo.lock") == "cat"
    assert first_token("  head  -100 x") == "head"
    assert first_token("") == ""


def test_compile_prefixes_rejects_metachars():
    with pytest.raises(ValueError):
        compile_prefixes(["cat", "gr.p"])
    assert compile_prefixes(["cat", "head"]) == ["cat", "head"]


def test_command_matches_startswith():
    assert command_matches("cargo test --all", ["cargo test"]) is True
    assert command_matches("ls -la", ["cat"]) is False
