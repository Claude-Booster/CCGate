from ccgate.config import load_config, DEFAULTS


def test_bashcap_defaults_present():
    assert DEFAULTS["bashCapEnabled"] is False
    assert DEFAULTS["bashCapHeadChars"] == 4000
    assert DEFAULTS["bashCapTailChars"] == 12000
    assert DEFAULTS["bashCapDebugLoopCalls"] == 3
    assert "pytest" in DEFAULTS["bashCapPrefixes"]
    assert "grep" not in DEFAULTS["bashCapPrefixes"]   # dropped (spec §9)


def test_bashcap_headchars_out_of_range_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapHeadChars": 0}', encoding="utf-8")
    assert load_config()["bashCapHeadChars"] == 4000   # 0 rejected → default


def test_bashcap_prefixes_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapPrefixes": ["deno test"]}', encoding="utf-8")
    prefixes = load_config()["bashCapPrefixes"]
    assert "pytest" in prefixes and "deno test" in prefixes   # merged, not replaced
