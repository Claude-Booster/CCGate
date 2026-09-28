import json as _json
from pathlib import Path

import pytest

from ccgate.report import assert_bounds, compute_ledger, render_report, _D4_CHECKS
from ccgate.scripts import miss_audit

_FIX = Path(__file__).parent / "fixtures" / "track_a_report"
_PATHS = [str(_FIX / "main.jsonl"), str(_FIX / "subagents" / "agent-1.jsonl")]

VALID = {
    "total_requests": 5, "total_misses": 2, "hit_ratio": 0.6, "cache_read_rate": 0.25,
    "by_origin": {"main": {"requests": 3, "misses": 1},
                  "subagent": {"requests": 2, "misses": 1}},
    "tokens": {"grand_total_input": 404000, "cache_read": 100000,
               "cache_creation": 300000, "input": 4000, "output": 190},
}


def test_valid_summary_passes():
    assert_bounds(VALID)  # must not raise


def test_zero_requests_passes():
    empty = {"total_requests": 0, "total_misses": 0, "hit_ratio": 1.0,
             "cache_read_rate": 0.0,
             "by_origin": {"main": {"requests": 0, "misses": 0},
                           "subagent": {"requests": 0, "misses": 0}},
             "tokens": {"grand_total_input": 0, "cache_read": 0,
                        "cache_creation": 0, "input": 0, "output": 0}}
    assert_bounds(empty)  # must not raise


def test_misses_exceed_requests_fires():
    bad = {**VALID, "total_misses": 6}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_origin_misses_mismatch_fires():
    bad = {**VALID, "by_origin": {"main": {"requests": 3, "misses": 1},
                                  "subagent": {"requests": 2, "misses": 0}}}
    with pytest.raises(ValueError):
        assert_bounds(bad)  # 1 + 0 != total_misses 2


def test_negative_token_fires():
    bad = {**VALID, "tokens": {**VALID["tokens"], "cache_read": -1}}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_cache_exceeds_grand_total_fires():
    bad = {**VALID, "tokens": {**VALID["tokens"], "grand_total_input": 100}}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_rate_out_of_range_fires():
    bad = {**VALID, "cache_read_rate": 1.5}
    with pytest.raises(ValueError):
        assert_bounds(bad)


def test_ledger_zeros_are_honest():
    led = compute_ledger([])
    assert led["tokens_avoided"] == 0
    assert led["tokens_measured"] == 0
    assert led["tokens_injected"] == 0
    assert led["net"] == 0


def test_ledger_prevented_is_per_cause():
    led = compute_ledger([])
    prevented = led["tokens_prevented"]
    assert prevented["a1"]["status"] == "unmeasurable"
    assert prevented["a3"]["value"] == 0
    assert prevented["d4"]["status"] == "available_unapplied"
    assert prevented["d4"]["findings"] == []


def test_ledger_d4_filters_shape_findings():
    findings = [
        {"check": "claudeMdExcludes", "severity": "warning"},
        {"check": "denyReads", "severity": "warning"},
        {"check": "toolDeferralSummary", "severity": "info"},  # not D4 → excluded
    ]
    led = compute_ledger(findings)
    d4 = led["tokens_prevented"]["d4"]["findings"]
    assert {f["check"] for f in d4} == {"claudeMdExcludes", "denyReads"}
    assert all(c in _D4_CHECKS for c in {"claudeMdExcludes", "denyReads", "claudeMdLines", "skillListing"})


def test_ledger_prevented_has_no_token_estimate():
    """D4 findings carry no fabricated number (spec §3)."""
    led = compute_ledger([{"check": "claudeMdExcludes", "severity": "warning"}])
    d4 = led["tokens_prevented"]["d4"]
    assert "value" in d4 and d4["value"] is None
    for f in d4["findings"]:
        assert "estimated_tokens" not in f


def test_render_states_prevention_model_accurately():
    out = render_report(VALID, compute_ledger([]))
    assert "ELIMINATE remains available" in out
    assert "PREVENT and RECOVER" in out
    assert "ccgate run" in out


def test_render_reports_zeros_and_facts():
    out = render_report(VALID, compute_ledger([]))
    assert "avoided" in out and "0" in out
    assert "2" in out          # total misses
    assert "25.0%" in out      # cache-read rate
    assert "ccgate shape" in out   # pointer to unapplied config
    assert "ccgate audit" in out   # pointer to cause table


def test_render_d4_none_found():
    out = render_report(VALID, compute_ledger([]))
    assert "none found" in out.lower()


def test_render_d4_lists_findings():
    led = compute_ledger([{"check": "claudeMdExcludes", "severity": "warning"}])
    out = render_report(VALID, led)
    assert "claudeMdExcludes" in out


def test_report_flag_human_output(capsys, monkeypatch):
    # Hermetic: don't spawn `claude --version` via run_shape under the suite (fd-0 hygiene).
    monkeypatch.setattr("ccgate.scripts.shape.run_shape", lambda **kw: [])
    miss_audit.main(["--report", *_PATHS])
    out = capsys.readouterr().out
    assert "TRACK A — MEASUREMENT REPORT" in out
    assert "PREVENT and RECOVER" in out
    assert "cache-read rate: 25.0%" in out


def test_report_flag_json_has_ledger(capsys, monkeypatch):
    monkeypatch.setattr("ccgate.scripts.shape.run_shape", lambda **kw: [])
    miss_audit.main(["--report", "--json", *_PATHS])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["ledger"]["tokens_avoided"] == 0
    assert payload["ledger"]["tokens_prevented"]["a1"]["status"] == "unmeasurable"
    assert payload["summary"]["total_misses"] == 2


def test_plain_audit_path_runs_bound_guard(capsys, monkeypatch):
    """assert_bounds guards the plain `audit` path too — the defect's original home."""
    calls = []
    monkeypatch.setattr("ccgate.report.assert_bounds", lambda s: calls.append(s))
    miss_audit.main(_PATHS)  # no --report
    assert calls and calls[0]["total_misses"] == 2


def test_report_passes_project_cwd_to_run_shape(monkeypatch, capsys):
    """D4 headroom is project-scoped: run_shape must be called with a real cwd, not None,
    or denyReads/claudeMdExcludes silently return []."""
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("ccgate.scripts.shape.run_shape", _capture)
    miss_audit.main(["--report", *_PATHS])
    assert captured.get("cwd")  # truthy, not None


def test_report_flag_empty_corpus_exits_zero(capsys, monkeypatch):
    monkeypatch.setattr(miss_audit, "find_transcripts", lambda **kw: [], raising=False)
    with pytest.raises(SystemExit) as ei:
        miss_audit.main(["--report", "--since", "1d"])
    assert ei.value.code == 0
