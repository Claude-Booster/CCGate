from ccgate.model import ModelSpec, get_model_spec, resolve_ttl


def test_known_model_returns_spec():
    spec = get_model_spec("claude-sonnet-5")
    assert isinstance(spec, ModelSpec)
    assert spec.rate_in > 0
    assert spec.write_multiplier > 1.0
    assert spec.read_multiplier < 1.0
    assert spec.window_tokens > 0


def test_unknown_model_returns_fallback():
    spec = get_model_spec("claude-future-99")
    assert isinstance(spec, ModelSpec)
    assert spec.rate_in > 0


def test_partial_prefix_match():
    # "claude-sonnet-5-20260101" should match "claude-sonnet-5"
    spec = get_model_spec("claude-sonnet-5-20260101")
    assert spec == get_model_spec("claude-sonnet-5")


def test_resolve_ttl_from_explicit_field():
    payload = {"prompt_cache": {"ttl": 3600}}
    assert resolve_ttl(payload) == 3600


def test_resolve_ttl_missing_returns_conservative():
    assert resolve_ttl({}) == 300
    assert resolve_ttl({"prompt_cache": {}}) == 300


def test_resolve_ttl_null_prompt_cache():
    assert resolve_ttl({"prompt_cache": None}) == 300
