"""
test_llp_mlb_winner_preflight.py
Regression suite for the LLP MLB Winner Preflight Gate.

Six mandatory tests from the reviewer spec plus additional edge-case
and architecture invariant tests.

Run:
  cd artifacts/flask-scoring-api
  python -m pytest gate_engine/tests/test_llp_mlb_winner_preflight.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from gate_engine import llp_mlb_winner_preflight as pf
from gate_engine.labels import PropLabel


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _mlb_winner_row(**overrides) -> dict:
    """
    Build a minimal MLB winner row that passes all three gates by default.
    Override individual fields to exercise specific failure paths.
    """
    base = {
        "sport":   "MLB",
        "market":  "game winner",
        # Gate 1 — confirmed starter + lineup
        "starter_status": "CONFIRMED",
        "lineup_status":  "CONFIRMED",
        # Gate 2 — healthy event
        "event_status":   "SCHEDULED",
        "weather_status": "CLEAR",
        # Gate 3 — comfortable edge at 1.73x
        # breakeven = 1/1.73 ≈ 0.5780, buffer = 0.015
        "kalshi_multiplier":                 1.73,
        "sportsbook_no_vig_probability":     0.620,
        "calibrated_probability_lower_bound": 0.600,
    }
    base.update(overrides)
    return base


def _non_mlb_winner_row(**overrides) -> dict:
    base = {
        "sport":  "NBA",
        "market": "player_points",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Reviewer-mandated regression tests (Tests 1–6)
# ---------------------------------------------------------------------------

class TestReviewerMandatedCases:

    def test_1_starter_unconfirmed_caps_at_watch(self):
        """
        TEST 1: Candidate has 68% model probability but starter unconfirmed.
        Expected: cap at WINNER_WATCH (MARKET_VERIFIED_HOLD) /
                  NO_STARTER_CONFIRMATION.
        """
        row = _mlb_winner_row(
            starter_status="UNCONFIRMED",
            model_probability=0.68,
        )
        pf.run(row)

        assert row["preflight_checked"] is True
        assert row["preflight_status"] == "WATCH"
        assert row["upgrade_allowed"] is False
        assert row["terminal_label"] == PropLabel.MARKET_VERIFIED_HOLD.value
        assert "NO_STARTER_CONFIRMATION" in row["preflight_blockers"]

    def test_2_postponement_risk_kills_row(self):
        """
        TEST 2: Candidate has confirmed starter and lineup but game has
        postponement risk.
        Expected: EVENT_RESET_REQUIRED_POSTPONEMENT.
        """
        row = _mlb_winner_row(event_status="POSTPONED")
        pf.run(row)

        assert row["preflight_checked"] is True
        assert row["preflight_status"] == "FAIL_POSTPONEMENT"
        assert row["upgrade_allowed"] is False
        assert row["terminal_label"] == PropLabel.SLATE_PURGE.value
        assert "EVENT_RESET_REQUIRED_POSTPONEMENT" in row["preflight_blockers"]

    def test_3_model_lb_below_breakeven_plus_buffer(self):
        """
        TEST 3: Candidate is 1.55x, breakeven 64.5%, model lower bound 63.8%.
        Expected: LLP_PRICE_FIREWALL_FAIL (model_lb < breakeven + buffer).

        breakeven = 1/1.55 ≈ 0.6452
        buffer    = 0.020  (multiplier < 1.60)
        threshold = 0.6652
        model_lb  = 0.638 < 0.6652 → FAIL_MODEL
        no_vig    = 0.660 > 0.6452 → market ok
        """
        row = _mlb_winner_row(
            kalshi_multiplier=1.55,
            sportsbook_no_vig_probability=0.660,
            calibrated_probability_lower_bound=0.638,
        )
        pf.run(row)

        assert row["preflight_checked"] is True
        assert row["preflight_status"] == "FAIL"
        assert row["upgrade_allowed"] is False
        assert row["terminal_label"] == PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value
        assert "LLP_PRICE_FIREWALL_FAIL" in row["preflight_blockers"]
        assert "MODEL_LOWER_BOUND_BELOW_BREAKEVEN" in row["preflight_blockers"]
        # no_vig passes → should NOT have NO_VIG_BELOW_BREAKEVEN
        assert "NO_VIG_BELOW_BREAKEVEN" not in row["preflight_blockers"]

    def test_4_no_vig_below_breakeven(self):
        """
        TEST 4: Candidate is 1.73x, breakeven 57.8%, model lower bound 59.4%,
        but no-vig is 56.9%.
        Expected: NO_VIG_BELOW_BREAKEVEN.

        breakeven = 1/1.73 ≈ 0.5780
        buffer    = 0.015
        no_vig    = 0.569 < 0.5780 → FAIL_MARKET
        model_lb  = 0.594 > 0.5780 + 0.015 = 0.5930 → model ok
        """
        row = _mlb_winner_row(
            kalshi_multiplier=1.73,
            sportsbook_no_vig_probability=0.569,
            calibrated_probability_lower_bound=0.594,
        )
        pf.run(row)

        assert row["preflight_checked"] is True
        assert row["preflight_status"] == "FAIL"
        assert row["upgrade_allowed"] is False
        assert row["terminal_label"] == PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value
        assert "NO_VIG_BELOW_BREAKEVEN" in row["preflight_blockers"]
        # model passes → should NOT have MODEL_LOWER_BOUND_BELOW_BREAKEVEN
        assert "MODEL_LOWER_BOUND_BELOW_BREAKEVEN" not in row["preflight_blockers"]

    def test_5_all_gates_pass_does_not_auto_approve(self):
        """
        TEST 5: Candidate passes starter, lineup, weather, no-vig, and model
        lower bound.
        Expected: eligible for exact price/no-vig/fee auditor — NOT
        automatically playable; terminal_label is NOT forced to FINAL_APPROVED.
        """
        row = _mlb_winner_row()   # fully compliant defaults
        pf.run(row)

        assert row["preflight_checked"] is True
        assert row["preflight_status"] == "PASS"
        assert row["upgrade_allowed"] is True
        assert row["preflight_blockers"] == []
        # terminal_label was not present before preflight; PASS leaves it
        # entirely unset — the classifier sets it, not this gate.
        assert row.get("terminal_label") is None
        assert row.get("terminal_label") != PropLabel.FINAL_APPROVED.value
        assert row.get("terminal_label") != PropLabel.MONEY_QUALIFIED.value

    def test_6_postponed_to_doubleheader_kills_original_row(self):
        """
        TEST 6: Game postponed and moved to doubleheader.
        Expected: original row killed (SLATE_PURGE); fresh full rerun required.
        """
        for event_status in ("POSTPONED", "CANCELLED", "SUSPENDED"):
            row = _mlb_winner_row(event_status=event_status)
            pf.run(row)

            assert row["terminal_label"] == PropLabel.SLATE_PURGE.value, (
                f"event_status={event_status} should yield SLATE_PURGE"
            )
            assert row["upgrade_allowed"] is False
            assert "EVENT_RESET_REQUIRED_POSTPONEMENT" in row["preflight_blockers"]


# ---------------------------------------------------------------------------
# Scope / no-op tests
# ---------------------------------------------------------------------------

class TestScopeGuards:

    def test_non_mlb_sport_is_noop(self):
        """Non-MLB rows must not be touched."""
        row = _non_mlb_winner_row()
        pf.run(row)
        assert row.get("preflight_checked") is False
        assert "preflight_status" not in row
        assert "terminal_label" not in row

    def test_mlb_non_winner_market_is_noop(self):
        """MLB prop rows (e.g. pitcher_strikeouts) are not winner markets."""
        row = {"sport": "MLB", "market": "pitcher_strikeouts"}
        pf.run(row)
        assert row.get("preflight_checked") is False

    def test_already_terminal_reject_is_skipped(self):
        """Rows with an upstream terminal reject must not be overwritten."""
        row = _mlb_winner_row()
        row["terminal_label"] = PropLabel.REJECT_NO_EDGE.value
        pf.run(row)
        # Gate did not run
        assert row.get("preflight_checked") is False
        assert row["terminal_label"] == PropLabel.REJECT_NO_EDGE.value

    def test_mlb_moneyline_market_keyword_triggers_gate(self):
        """'moneyline' keyword activates the gate."""
        row = {**_mlb_winner_row(), "market": "moneyline"}
        pf.run(row)
        assert row["preflight_checked"] is True

    def test_mlb_ml_keyword_triggers_gate(self):
        """'ml' keyword activates the gate."""
        row = {**_mlb_winner_row(), "market": "ml"}
        pf.run(row)
        assert row["preflight_checked"] is True


# ---------------------------------------------------------------------------
# Gate 1 — Starter / Lineup edge cases
# ---------------------------------------------------------------------------

class TestGate1StarterLineup:

    def test_probable_strong_starter_passes(self):
        row = _mlb_winner_row(starter_status="PROBABLE_STRONG")
        pf.run(row)
        # Only starter gate — should not add NO_STARTER_CONFIRMATION
        assert "NO_STARTER_CONFIRMATION" not in row["preflight_blockers"]

    def test_missing_starter_status_yields_watch(self):
        row = _mlb_winner_row(starter_status=None)
        pf.run(row)
        assert row["preflight_status"] == "WATCH"
        assert any("STARTER_STATUS_MISSING" in b for b in row["preflight_blockers"])

    def test_probable_only_lineup_yields_watch(self):
        row = _mlb_winner_row(lineup_status="PROBABLE_ONLY")
        pf.run(row)
        assert row["preflight_status"] == "WATCH"
        assert "PROBABLE_ONLY" in row["preflight_blockers"]
        assert row["terminal_label"] == PropLabel.MARKET_VERIFIED_HOLD.value

    def test_projected_acceptable_lineup_passes(self):
        row = _mlb_winner_row(lineup_status="PROJECTED_ACCEPTABLE")
        pf.run(row)
        assert "NO_LINEUP_CONFIRMATION" not in row["preflight_blockers"]

    def test_unknown_lineup_status_yields_watch(self):
        row = _mlb_winner_row(lineup_status="EXPECTED")
        pf.run(row)
        assert "NO_LINEUP_CONFIRMATION" in row["preflight_blockers"]


# ---------------------------------------------------------------------------
# Gate 2 — Weather / Event status edge cases
# ---------------------------------------------------------------------------

class TestGate2WeatherEvent:

    def test_cancelled_kills_row(self):
        row = _mlb_winner_row(event_status="CANCELLED")
        pf.run(row)
        assert row["terminal_label"] == PropLabel.SLATE_PURGE.value

    def test_active_pregame_valid_passes(self):
        row = _mlb_winner_row(event_status="ACTIVE_PREGAME_VALID")
        pf.run(row)
        assert "EVENT_STATUS_FAILURE" not in row["preflight_blockers"]
        assert "NO_EVENT_VERIFICATION" not in row["preflight_blockers"]

    def test_missing_event_status_yields_watch(self):
        row = _mlb_winner_row(event_status=None)
        pf.run(row)
        assert row["preflight_status"] == "WATCH"
        assert "NO_EVENT_VERIFICATION" in row["preflight_blockers"]

    def test_weather_rain_yields_watch(self):
        row = _mlb_winner_row(weather_status="RAINOUT_RISK")
        pf.run(row)
        assert "WEATHER_RISK_CUT" in row["preflight_blockers"]
        assert row["preflight_status"] == "WATCH"

    def test_weather_delay_yields_watch(self):
        row = _mlb_winner_row(weather_status="DELAY_RISK")
        pf.run(row)
        assert "WEATHER_RISK_CUT" in row["preflight_blockers"]


# ---------------------------------------------------------------------------
# Gate 3 — Breakeven / No-vig / Model edge cases
# ---------------------------------------------------------------------------

class TestGate3Breakeven:

    def test_breakeven_computed_correctly_1_55x(self):
        """1/1.55 ≈ 0.6452"""
        row = _mlb_winner_row(
            kalshi_multiplier=1.55,
            sportsbook_no_vig_probability=0.70,
            calibrated_probability_lower_bound=0.70,
        )
        pf.run(row)
        be = row.get("kalshi_breakeven_probability")
        assert be is not None
        assert abs(be - (1 / 1.55)) < 1e-4

    def test_short_favorite_buffer_is_2pct(self):
        """Multiplier < 1.60 → buffer 0.020."""
        # model_lb exactly at breakeven + 0.019 → should FAIL (< buffer 0.020)
        be = 1 / 1.58
        row = _mlb_winner_row(
            kalshi_multiplier=1.58,
            sportsbook_no_vig_probability=be + 0.05,
            calibrated_probability_lower_bound=be + 0.019,
        )
        pf.run(row)
        assert "MODEL_LOWER_BOUND_BELOW_BREAKEVEN" in row["preflight_blockers"]

    def test_standard_favorite_buffer_is_1pt5pct(self):
        """Multiplier >= 1.60 → buffer 0.015."""
        be = 1 / 1.65
        row = _mlb_winner_row(
            kalshi_multiplier=1.65,
            sportsbook_no_vig_probability=be + 0.05,
            calibrated_probability_lower_bound=be + 0.016,   # > 0.015 → PASS
        )
        pf.run(row)
        assert "MODEL_LOWER_BOUND_BELOW_BREAKEVEN" not in row["preflight_blockers"]

    def test_both_gate3_failures_adds_kalshi_reject_no_edge(self):
        """When both no_vig AND model_lb fail, KALSHI_REJECT_NO_EDGE is added."""
        be = 1 / 1.70
        row = _mlb_winner_row(
            kalshi_multiplier=1.70,
            sportsbook_no_vig_probability=be - 0.01,   # below breakeven
            calibrated_probability_lower_bound=be - 0.01,  # also below
        )
        pf.run(row)
        assert "KALSHI_REJECT_NO_EDGE" in row["preflight_blockers"]
        assert "NO_VIG_BELOW_BREAKEVEN" in row["preflight_blockers"]
        assert "MODEL_LOWER_BOUND_BELOW_BREAKEVEN" in row["preflight_blockers"]

    def test_missing_multiplier_is_hard_block(self):
        row = _mlb_winner_row(kalshi_multiplier=None)
        pf.run(row)
        assert row["preflight_status"] == "FAIL"
        assert row["terminal_label"] == PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value
        assert any("MISSING_MULTIPLIER" in b for b in row["preflight_blockers"])

    def test_missing_no_vig_is_hard_block(self):
        row = _mlb_winner_row(sportsbook_no_vig_probability=None)
        pf.run(row)
        assert row["preflight_status"] == "FAIL"
        assert any("MISSING_NO_VIG" in b for b in row["preflight_blockers"])

    def test_missing_model_lb_is_hard_block(self):
        row = _mlb_winner_row(calibrated_probability_lower_bound=None)
        pf.run(row)
        assert row["preflight_status"] == "FAIL"
        assert any("MISSING_MODEL_LB" in b for b in row["preflight_blockers"])

    def test_breakeven_gap_field_is_stamped(self):
        """breakeven_gap = no_vig - breakeven."""
        no_vig = 0.620
        m      = 1.73
        row = _mlb_winner_row(
            kalshi_multiplier=m,
            sportsbook_no_vig_probability=no_vig,
        )
        pf.run(row)
        expected_gap = round(no_vig - (1 / m), 6)
        assert abs(row["breakeven_gap"] - expected_gap) < 1e-5


# ---------------------------------------------------------------------------
# Architecture invariants
# ---------------------------------------------------------------------------

class TestArchitectureInvariants:

    def test_can_execute_is_false(self):
        assert pf.can_execute is False

    def test_execution_rule_is_dry_run(self):
        assert pf.EXECUTION_RULE == "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

    def test_gates_record_is_stamped_on_activation(self):
        row = _mlb_winner_row()
        pf.run(row)
        assert "mlb_winner_preflight" in row.get("gates", {})
        gate = row["gates"]["mlb_winner_preflight"]
        assert gate["can_execute"] is False
        assert gate["preflight_status"] == "PASS"

    def test_gates_record_not_stamped_when_noop(self):
        row = _non_mlb_winner_row()
        pf.run(row)
        assert "mlb_winner_preflight" not in row.get("gates", {})

    def test_pass_does_not_overwrite_existing_terminal_label(self):
        """A PASS must not clobber a label already set by an earlier gate."""
        row = _mlb_winner_row()
        row["terminal_label"] = PropLabel.MARKET_VERIFIED_HOLD.value
        pf.run(row)
        assert row["preflight_status"] == "PASS"
        # PASS should leave terminal_label as MARKET_VERIFIED_HOLD, not None
        assert row["terminal_label"] == PropLabel.MARKET_VERIFIED_HOLD.value

    def test_required_output_fields_always_stamped(self):
        """All required output fields from the spec must exist on the row."""
        row = _mlb_winner_row()
        pf.run(row)
        for field in (
            "starter_status", "starter_source",
            "lineup_status", "lineup_source",
            "event_status", "weather_status", "weather_source",
            "kalshi_multiplier",
            "kalshi_breakeven_probability",
            "sportsbook_no_vig_probability",
            "model_probability",
            "calibrated_probability_lower_bound",
            "breakeven_gap",
            "preflight_status",
            "upgrade_allowed",
            "preflight_blockers",
        ):
            assert field in row, f"Required field '{field}' not stamped on row"

    def test_hard_block_label_is_in_reject_labels(self):
        """MLB_WINNER_PREFLIGHT_BLOCK must be in REJECT_LABELS."""
        from gate_engine.labels import REJECT_LABELS
        assert PropLabel.MLB_WINNER_PREFLIGHT_BLOCK in REJECT_LABELS

    def test_gate3_fail_takes_priority_over_gate1_watch(self):
        """Hard block (Gate 3) must override watch cap (Gate 1)."""
        be = 1 / 1.73
        row = _mlb_winner_row(
            starter_status="UNCONFIRMED",         # Gate 1 watch
            sportsbook_no_vig_probability=be - 0.01,  # Gate 3 hard fail
        )
        pf.run(row)
        # hard_blockers take priority — must be FAIL not WATCH
        assert row["preflight_status"] == "FAIL"
        assert row["terminal_label"] == PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value

    def test_postponement_takes_priority_over_gate3_fail(self):
        """SLATE_PURGE (kill) must override MLB_WINNER_PREFLIGHT_BLOCK."""
        row = _mlb_winner_row(
            event_status="POSTPONED",
            sportsbook_no_vig_probability=0.10,  # Gate 3 would also fail
        )
        pf.run(row)
        assert row["terminal_label"] == PropLabel.SLATE_PURGE.value
        assert row["preflight_status"] == "FAIL_POSTPONEMENT"
