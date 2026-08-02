"""
test_scan_audit.py
Tests for:
  - kalshi_engine/scan_audit.py  (WOW-PATCH-2026-08-01-LINEMAKERS-PRESENTATION-AND-SELF-AUDIT)
  - kalshi_engine/sports_gate.py Gate 0 (event-state mutex)
"""
import pytest
from kalshi_engine.scan_audit import (
    check_ticker_identity,
    build_candidate_audit_row,
    build_candidate_audit_table,
    build_evidence_manifest,
    run_second_pass_audit,
    build_reconciliation_equation,
    build_candidate_funnel_summary,
)
from kalshi_engine.sports_gate import check as sports_gate_check


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_candidate(**overrides) -> dict:
    """Return a minimal sports candidate dict that passes Gate 0."""
    base = {
        "ticker":                      "KXMLB-ABC-D123",
        "event_ticker":                "KXMLB-ABC",
        "market_title":                "Yankees vs Red Sox",
        "settlement_condition":        "The team with more runs wins.",
        "market_type":                 "full_game_outright_winner",
        "trading_active":              True,
        "kalshi_orderbook_source":     "direct_api",
        "price_age_minutes":           3.0,
        "calibrated_prob_lower_bound": 0.70,
        "lineup_status":               "CONFIRMED",
        "consensus_odds":              {"status": "AVAILABLE", "single_book_fallback": False,
                                       "consensus_fair_probability": 0.68},
        "market_prior_weight":         0.30,
        "net_edge_lower_bound":        0.025,
        "settlement_grade_result":     {"settlement_risk": "LOW", "resolution_clarity_grade": "A"},
        "portfolio_check_passed":      True,
        "portfolio_rejection_reason":  None,
        "event_status":                "UNKNOWN",
        "process_pass_fail":           "PASS",
        "failure_category":            None,
        "category":                    "sports_winner",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# check_ticker_identity tests
# ---------------------------------------------------------------------------

class TestCheckTickerIdentity:

    def test_all_match_no_warning(self):
        cand = {"ticker": "KXMLB-T1", "inventory_ticker": "KXMLB-T1", "orderbook_ticker": "KXMLB-T1"}
        result = check_ticker_identity(cand)
        assert result["identity_verified"] is True
        assert result["warning"] is False
        assert result["warning_label"] is None
        assert result["mismatches"] == []

    def test_no_inventory_or_orderbook_ticker_passes(self):
        cand = {"ticker": "KXMLB-T1"}
        result = check_ticker_identity(cand)
        assert result["identity_verified"] is True
        assert result["warning"] is False

    def test_inventory_mismatch_warns(self):
        cand = {"ticker": "KXMLB-T1", "inventory_ticker": "KXMLB-T2"}
        result = check_ticker_identity(cand)
        assert result["warning"] is True
        assert result["warning_label"] == "CONTRACT_IDENTITY_UNVERIFIED"
        assert len(result["mismatches"]) == 1

    def test_orderbook_mismatch_warns(self):
        cand = {"ticker": "KXMLB-T1", "orderbook_ticker": "KXMLB-DIFFERENT"}
        result = check_ticker_identity(cand)
        assert result["warning"] is True
        assert result["warning_label"] == "CONTRACT_IDENTITY_UNVERIFIED"

    def test_both_mismatch_both_listed(self):
        cand = {"ticker": "KXMLB-T1", "inventory_ticker": "KXMLB-T2", "orderbook_ticker": "KXMLB-T3"}
        result = check_ticker_identity(cand)
        assert len(result["mismatches"]) == 2

    def test_warning_never_blocks(self):
        """check_ticker_identity must return a warning dict, not raise or return a gate failure."""
        cand = {"ticker": "KXMLB-T1", "orderbook_ticker": "KXMLB-DIFFERENT"}
        result = check_ticker_identity(cand)
        assert "passed" not in result     # not a gate result
        assert result["warning"] is True  # only a warning


# ---------------------------------------------------------------------------
# build_candidate_audit_row tests
# ---------------------------------------------------------------------------

class TestBuildCandidateAuditRow:

    def test_contains_20_required_fields(self):
        cand = _make_candidate()
        row  = build_candidate_audit_row(cand)
        required = {
            "contract_ticker", "category_and_lane", "side", "settlement_source",
            "event_state", "yes_executable_ask", "no_executable_ask",
            "yes_midpoint", "no_midpoint", "orderbook_timestamp", "price_age_minutes",
            "fee_adjusted_break_even", "model_probability", "calibrated_lower_bound",
            "point_edge", "lower_bound_edge",
            "primary_win_path", "primary_failure_path",
            "gate_result", "final_label",
        }
        missing = required - set(row.keys())
        assert not missing, f"Audit row missing fields: {missing}"

    def test_contract_identity_warning_field_present(self):
        cand = _make_candidate(ticker="KXMLB-T1", orderbook_ticker="KXMLB-OTHER")
        row  = build_candidate_audit_row(cand)
        assert "contract_identity_warning" in row
        assert row["contract_identity_warning"] == "CONTRACT_IDENTITY_UNVERIFIED"

    def test_no_warning_when_tickers_match(self):
        cand = _make_candidate(ticker="KXMLB-T1", orderbook_ticker="KXMLB-T1")
        row  = build_candidate_audit_row(cand)
        assert row["contract_identity_warning"] is None

    def test_gate_result_pass(self):
        cand = _make_candidate(process_pass_fail="PASS")
        row  = build_candidate_audit_row(cand)
        assert row["gate_result"] == "PASS"

    def test_gate_result_fail_carries_code(self):
        cand = _make_candidate(process_pass_fail="FAIL", failure_category="EDGE_BELOW_FLOOR")
        row  = build_candidate_audit_row(cand)
        assert row["gate_result"] == "EDGE_BELOW_FLOOR"

    def test_point_edge_and_lb_edge_are_separate(self):
        """Point edge must never equal lower-bound edge when model_prob != lower_bound."""
        cand = _make_candidate(
            model_probability        = 0.72,
            calibrated_prob_lower_bound = 0.68,
            fee_adjusted_break_even  = 0.66,
        )
        row = build_candidate_audit_row(cand)
        # point_edge  = 0.72 - 0.66 = +0.06
        # lb_edge     = 0.68 - 0.66 = +0.02
        assert row["point_edge"] is not None
        assert row["lower_bound_edge"] is not None
        assert abs(row["point_edge"] - 0.06) < 0.001
        assert abs(row["lower_bound_edge"] - 0.02) < 0.001
        assert row["point_edge"] != row["lower_bound_edge"]


# ---------------------------------------------------------------------------
# build_evidence_manifest tests
# ---------------------------------------------------------------------------

class TestBuildEvidenceManifest:

    def test_returns_evidence_and_identity_check(self):
        cand   = _make_candidate()
        result = build_evidence_manifest(cand)
        assert "evidence" in result
        assert "ticker_identity_check" in result

    def test_verified_when_tickers_match(self):
        cand   = _make_candidate(ticker="KXMLB-T1", inventory_ticker="KXMLB-T1")
        result = build_evidence_manifest(cand)
        assert result["ticker_identity_check"]["identity_verified"] is True

    def test_unverified_when_tickers_differ(self):
        cand   = _make_candidate(ticker="KXMLB-T1", orderbook_ticker="KXMLB-OTHER")
        result = build_evidence_manifest(cand)
        assert result["ticker_identity_check"]["identity_verified"] is False
        assert result["ticker_identity_check"]["warning_label"] == "CONTRACT_IDENTITY_UNVERIFIED"


# ---------------------------------------------------------------------------
# run_second_pass_audit tests
# ---------------------------------------------------------------------------

class TestRunSecondPassAudit:

    def _counters(self, **overrides) -> dict:
        base = {
            "identity_failures":     0,
            "settlement_failures":   0,
            "event_state_failures":  0,
            "stale_price_failures":  0,
            "model_failures":        0,
            "edge_failures":         0,
            "portfolio_failures":    0,
        }
        base.update(overrides)
        return base

    def test_all_pass_on_clean_candidates(self):
        cands   = [_make_candidate()]
        result  = run_second_pass_audit(cands, self._counters(), final_pool=cands)
        assert result["passed"] is True
        assert result["failures"] == []
        assert result["total_checks"] == 7

    def test_fails_when_qualified_row_missing_disposition(self):
        cand = _make_candidate()
        del cand["process_pass_fail"]
        result = run_second_pass_audit([cand], self._counters(), final_pool=[])
        check1 = next(c for c in result["checks"] if c["id"] == 1)
        assert not check1["passed"]

    def test_fails_when_qualified_row_carries_failure_category(self):
        cand = _make_candidate(process_pass_fail="PASS", failure_category="EDGE_BELOW_FLOOR")
        result = run_second_pass_audit([cand], self._counters(), final_pool=[cand])
        check2 = next(c for c in result["checks"] if c["id"] == 2)
        assert not check2["passed"]

    def test_fails_check3_when_live_event_qualified(self):
        cand = _make_candidate(process_pass_fail="PASS", event_status="in_progress")
        result = run_second_pass_audit([cand], self._counters(), final_pool=[cand])
        check3 = next(c for c in result["checks"] if c["id"] == 3)
        assert not check3["passed"]

    def test_fails_check5_when_stale_price_in_qualified(self):
        cand = _make_candidate(process_pass_fail="PASS", price_age_minutes=15.0)
        result = run_second_pass_audit([cand], self._counters(), final_pool=[cand])
        check5 = next(c for c in result["checks"] if c["id"] == 5)
        assert not check5["passed"]

    def test_fails_check6_when_no_edge_in_qualified_sports(self):
        cand = _make_candidate(process_pass_fail="PASS", net_edge_lower_bound=-0.01, category="sports_winner")
        result = run_second_pass_audit([cand], self._counters(), final_pool=[cand])
        check6 = next(c for c in result["checks"] if c["id"] == 6)
        assert not check6["passed"]

    def test_check7_passes_with_portfolio_failures_key(self):
        result = run_second_pass_audit([], {"portfolio_failures_ct": 0}, final_pool=[])
        check7 = next(c for c in result["checks"] if c["id"] == 7)
        assert check7["passed"]

    def test_check7_warns_without_portfolio_key(self):
        result = run_second_pass_audit([], {}, final_pool=[])
        check7 = next(c for c in result["checks"] if c["id"] == 7)
        assert not check7["passed"]


# ---------------------------------------------------------------------------
# build_reconciliation_equation tests
# ---------------------------------------------------------------------------

class TestBuildReconciliationEquation:

    def _ctrs(self, **kw) -> dict:
        return {
            "identity_failures":     kw.get("identity_failures", 0),
            "settlement_failures":   kw.get("settlement_failures", 0),
            "event_state_failures":  kw.get("event_state_failures", 0),
            "model_failures":        kw.get("model_failures", 0),
            "stale_price_failures":  kw.get("stale_price_failures", 0),
            "edge_failures":         kw.get("edge_failures", 0),
            "portfolio_failures_ct": kw.get("portfolio_failures_ct", 0),
        }

    def test_pass_when_buckets_sum_to_qualified(self):
        """8+5+3+4+10 = 30; qualified=10; rows_scanned defaults to sum."""
        ctrs = self._ctrs(
            identity_failures=8, settlement_failures=5,
            stale_price_failures=3, model_failures=4,
        )
        result = build_reconciliation_equation(ctrs, qualified=10)
        assert result["status"] == "RECONCILIATION_PASS"
        assert result["equation_sum"] == 30
        assert result["delta"] == 0

    def test_mismatch_detected_when_explicit_rows_scanned_differs(self):
        """Caller says rows_scanned=35 but buckets sum to 30 → MISMATCH."""
        ctrs = self._ctrs(identity_failures=8, settlement_failures=5,
                          stale_price_failures=3, model_failures=4)
        ctrs["rows_scanned"] = 35
        result = build_reconciliation_equation(ctrs, qualified=10)
        assert result["status"] == "RECONCILIATION_MISMATCH"
        assert result["delta"] == -5

    def test_all_zeros_qualified_zero_passes(self):
        result = build_reconciliation_equation(self._ctrs(), qualified=0)
        assert result["status"] == "RECONCILIATION_PASS"
        assert result["equation_sum"] == 0

    def test_regression_rt021_example(self):
        """RT-021: 30 = 8 + 5 + 3 + 4 + 10."""
        ctrs = self._ctrs(identity_failures=8, settlement_failures=5,
                          stale_price_failures=3, model_failures=4)
        result = build_reconciliation_equation(ctrs, qualified=10)
        assert result["equation_sum"] == 30
        assert result["status"] == "RECONCILIATION_PASS"

    def test_event_state_failures_counted_in_equation(self):
        ctrs = self._ctrs(event_state_failures=2, model_failures=3)
        result = build_reconciliation_equation(ctrs, qualified=5)
        assert result["equation_buckets"]["event_state_failed"] == 2
        assert result["equation_sum"] == 10


# ---------------------------------------------------------------------------
# sports_gate Gate 0 — event-state mutex  tests
# ---------------------------------------------------------------------------

_INVENTORY_READY = "INVENTORY_READY"

def _minimal_passing_candidate(**overrides) -> dict:
    """Minimal candidate that passes Gates 0–9."""
    base = {
        "event_status":                "UNKNOWN",
        "ticker":                      "KXMLB-T1",
        "event_ticker":                "KXMLB-EV1",
        "market_title":                "Yankees vs Sox",
        "settlement_condition":        "Team with more runs wins.",
        "market_type":                 "full_game_outright_winner",
        "trading_active":              True,
        "kalshi_orderbook_source":     "direct_api",
        "price_age_minutes":           3.0,
        "calibrated_prob_lower_bound": 0.72,
        "lineup_status":               "CONFIRMED",
        "consensus_odds":              {"status": "AVAILABLE", "single_book_fallback": False,
                                       "consensus_fair_probability": 0.70},
        "market_prior_weight":         0.30,
        "net_edge_lower_bound":        0.03,
        "settlement_grade_result":     {"settlement_risk": "LOW", "resolution_clarity_grade": "A"},
        "portfolio_check_passed":      True,
        "portfolio_rejection_reason":  None,
    }
    base.update(overrides)
    return base


class TestSportsGateGate0EventStateMutex:

    @pytest.mark.parametrize("live_status", [
        "in_progress", "IN_PROGRESS", "live", "LIVE",
        "started", "STARTED", "halftime", "HALFTIME",
        "suspended", "SUSPENDED", "active", "ACTIVE",
        "inprogress",
    ])
    def test_live_event_blocked_as_category_disabled(self, live_status):
        cand   = _minimal_passing_candidate(event_status=live_status)
        result = sports_gate_check(cand, _INVENTORY_READY)
        assert result["passed"] is False, f"Expected FAIL for event_status={live_status}"
        assert result["failure_category"] == "CATEGORY_DISABLED_OR_UNSUPPORTED"
        assert result["failure_gate"] == 0
        # Verify reason is in the detail string
        detail = result["gate_verdicts"][-1]["detail"]
        assert "LIVE_MARKET_DISABLED" in detail

    @pytest.mark.parametrize("pregame_status", [
        "UNKNOWN", "unknown", "scheduled", "SCHEDULED",
        "pending", "not_started", "",
    ])
    def test_pregame_status_passes_gate_0(self, pregame_status):
        cand   = _minimal_passing_candidate(event_status=pregame_status)
        result = sports_gate_check(cand, _INVENTORY_READY)
        # Gate 0 should pass; later gates may fail — we only care gate 0 passed
        gate_0 = next((v for v in result["gate_verdicts"] if v["gate"] == 0), None)
        assert gate_0 is not None, "Gate 0 verdict missing"
        assert gate_0["passed"] is True, f"Gate 0 should pass for pregame status={pregame_status}"

    def test_missing_event_status_defaults_to_pass(self):
        """A candidate with no event_status key should not be blocked by Gate 0."""
        cand = _minimal_passing_candidate()
        del cand["event_status"]
        result = sports_gate_check(cand, _INVENTORY_READY)
        gate_0 = next((v for v in result["gate_verdicts"] if v["gate"] == 0), None)
        assert gate_0 is not None
        assert gate_0["passed"] is True

    def test_live_event_never_reaches_gate_1(self):
        """Gate 0 must short-circuit before Gate 1 inventory check."""
        cand   = _minimal_passing_candidate(event_status="live")
        result = sports_gate_check(cand, _INVENTORY_READY)
        assert result["failure_gate"] == 0
        # Only one verdict should exist (gate 0 fail)
        assert len(result["gate_verdicts"]) == 1

    def test_inventory_not_ready_still_blocked_at_gate_1_when_pregame(self):
        """Gate 0 passes for pregame; Gate 1 then fires for missing inventory."""
        cand   = _minimal_passing_candidate(event_status="scheduled")
        result = sports_gate_check(cand, "INVENTORY_EMPTY")
        assert result["failure_gate"] == 1
        assert result["failure_category"] == "INVENTORY_NOT_READY"
