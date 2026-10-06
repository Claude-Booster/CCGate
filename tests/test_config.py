from ccgate.config import DEFAULTS, load_config


def test_bashcap_defaults_present():
    assert DEFAULTS["bashEnabled"] is False
    assert DEFAULTS["bashCapHeadChars"] == 4000
    assert DEFAULTS["bashCapTailChars"] == 12000
    assert DEFAULTS["bashCapDebugLoopCalls"] == 3
    assert "pytest" in DEFAULTS["bashCapPrefixes"]
    assert "grep" not in DEFAULTS["bashCapPrefixes"]   # dropped (spec §9)


def test_bashenabled_replaces_bashcapenabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    cfg = load_config()
    assert "bashEnabled" in cfg and cfg["bashEnabled"] is False
    assert "bashCapEnabled" not in cfg


def test_otel_host_default_is_loopback():
    # otel_reader must bind loopback by default; otelHost is the escape hatch for
    # the rare cross-host OTLP-ingestion case.
    assert DEFAULTS["otelHost"] == "127.0.0.1"


def test_bashreadprefixes_default_and_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashReadPrefixes": ["xxd"]}', encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert "cat" in cfg["bashReadPrefixes"] and "xxd" in cfg["bashReadPrefixes"]  # merged, not replaced


def test_bashcap_headchars_out_of_range_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapHeadChars": 0}', encoding="utf-8")
    assert load_config()["bashCapHeadChars"] == 4000   # 0 rejected → default


def test_bashcap_prefixes_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"bashCapPrefixes": ["deno test"]}', encoding="utf-8")
    prefixes = load_config()["bashCapPrefixes"]
    assert "pytest" in prefixes and "deno test" in prefixes   # merged, not replaced
