from ccgate.ledger import assert_gate, compute_net, format_headline

_NO_NOTICES = {
    "session_id": "s1",
    "model": None,
    "tool_calls": [
        {"seq": 1, "tool": "Read", "response_chars": 100,
         "tokens_est": 25, "notice_bytes": 0}
    ],
    "ledger": {"tokens_avoided": 0},
}

_WITH_NOTICE = {
    "session_id": "s1",
    "model": None,
    "tool_calls": [
        {"seq": 1, "tool": "Read", "response_chars": 100_000,
         "tokens_est": 25_000, "notice_bytes": 80}
    ],
    "ledger": {"tokens_avoided": 0},
}


class TestComputeNet:
    def test_no_notices_zero_injected(self):
        result = compute_net(_NO_NOTICES, session_input_tokens=1000)
        assert result["tokens_injected"] == 0
        assert result["tokens_avoided"] == 0
        assert result["net"] == 0

    def test_notice_contributes_to_injected(self):
        result = compute_net(_WITH_NOTICE, session_input_tokens=1000)
        assert result["tokens_injected"] == 80 // 4  # == 20
        assert result["net"] == -20

    def test_net_usd_zero_without_pricing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGATE_HOME", str(tmp_path))
        result = compute_net(_NO_NOTICES, session_input_tokens=1000)
        assert result["net_usd"] == 0.0

    def test_multiple_notices_summed(self):
        session = {
            "model": None,
            "tool_calls": [
                {"notice_bytes": 80},
                {"notice_bytes": 0},
                {"notice_bytes": 60},
            ],
            "ledger": {"tokens_avoided": 0},
        }
        result = compute_net(session)
        assert result["tokens_injected"] == (80 // 4) + (60 // 4)  # 20 + 15 == 35

    def test_empty_session_returns_zeros(self):
        result = compute_net({})
        assert result == {"tokens_avoided": 0, "tokens_injected": 0,
                          "net": 0, "net_usd": 0.0}


class TestFormatHeadline:
    def test_positive_net_shows_plus(self):
        session = {"ledger": {"tokens_avoided": 1500, "tokens_injected": 266, "net": 1234}}
        line = format_headline(session)
        assert "net +1,234" in line
        assert "avoided 1,500" in line
        assert "injected 266" in line

    def test_negative_net_shows_warning(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 20, "net": -20}}
        line = format_headline(session)
        assert "net -20" in line
        assert "self-cost" in line

    def test_zero_net_shows_zero(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0}}
        line = format_headline(session)
        assert "net +0" in line or "net 0" in line


class TestAssertGate:
    def test_g6_passes_when_within_2pct_and_net_positive(self):
        session = {"ledger": {"tokens_avoided": 1000, "tokens_injected": 10, "net": 990}}
        assert assert_gate(session, session_input_tokens=1000) is True

    def test_g6_fails_when_net_zero(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 0, "net": 0}}
        assert assert_gate(session, session_input_tokens=1000) is False

    def test_g6_fails_when_net_negative(self):
        session = {"ledger": {"tokens_avoided": 0, "tokens_injected": 20, "net": -20}}
        assert assert_gate(session, session_input_tokens=1000) is False

    def test_g6_fails_when_injected_exceeds_2pct(self):
        # 25 / 1000 = 2.5% > 2%
        session = {"ledger": {"tokens_avoided": 1000, "tokens_injected": 25, "net": 975}}
        assert assert_gate(session, session_input_tokens=1000) is False

    def test_g6_passes_at_exact_2pct_boundary(self):
        # 20 / 1000 = exactly 2%
        session = {"ledger": {"tokens_avoided": 1000, "tokens_injected": 20, "net": 980}}
        assert assert_gate(session, session_input_tokens=1000) is True
