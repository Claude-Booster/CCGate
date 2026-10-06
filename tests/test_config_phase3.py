from ccgate.config import DEFAULTS, load_config


def test_defaults_include_new_keys():
    cfg = load_config()
    assert cfg["startupTokenCap"] == 12000
    assert cfg["otelPort"] == 4318
    assert cfg["digestMaxPaths"] == 500


def test_env_override_startup_token_cap(monkeypatch):
    monkeypatch.setenv("CCGATE_STARTUP_TOKEN_CAP", "5000")
    cfg = load_config()
    assert cfg["startupTokenCap"] == 5000


def test_range_guard_otel_port_rejects_out_of_range(monkeypatch):
    # 100 < minimum 1024 → falls back to default
    monkeypatch.setenv("CCGATE_OTEL_PORT", "100")
    cfg = load_config()
    assert cfg["otelPort"] == 4318


def test_env_override_digest_max_paths(monkeypatch):
    monkeypatch.setenv("CCGATE_DIGEST_MAX_PATHS", "200")
    cfg = load_config()
    assert cfg["digestMaxPaths"] == 200


def test_new_keys_in_defaults_dict():
    assert "startupTokenCap" in DEFAULTS
    assert "otelPort" in DEFAULTS
    assert "digestMaxPaths" in DEFAULTS
