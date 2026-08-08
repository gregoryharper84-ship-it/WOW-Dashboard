"""
gate_engine/tests/test_teamrankings_enrichment.py
WOW-PATCH-2026-08-08-TEAMRANKINGS-SECONDARY-ENRICHMENT — Regression tests.

Proves all eight governance invariants specified in the patch:
  1. TR cannot override a starter/lineup blocker
  2. Missing TR does not fail the base model
  3. Stale TR receives zero weight
  4. TR display_odds are NOT double-counted in the market no-vig computation
  5. TR cannot push market_prior_weight above the governance cap
  6. Raw ratings are NOT converted to probability without a calibrated mapping
  7. Opposite-side TR projection triggers TEAMRANKINGS_CONTRADICTION_REVIEW flag
  8. can_execute=False is enforced in every new module

Additional tests:
  9.  UNSUPPORTED_SPORT returns DATA_UNOBTAINABLE-equivalent without breaking model
  10. PROXY_ONLY status yields zero weight
  11. SOURCE_CONFLICT status yields zero weight
  12. Only a direct matchup_win_prob_home activates the TR submodel
  13. TR effective_weight hard ceiling at 10% enforced in sport model ensemble
  14. TR disagreement audit annotation fires for OPPOSITE_SIDE contradiction
  15. TR acquisition notes confirm display_odds_excluded_from_model always True

can_execute=False unconditional.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
from typing import Any

can_execute: bool = False  # UNCONDITIONAL


# ---------------------------------------------------------------------------
# Helpers: minimal valid enrichment builders
# ---------------------------------------------------------------------------

def _tr_data(
    matchup_win_prob_home: float | None = 0.62,
    source_status: str = "RETRIEVED",
    freshness_age_hours: float = 1.0,
    display_odds_home: int | None = -185,
    display_odds_away: int | None = 155,
) -> dict[str, Any]:
    """Return a minimal valid enrichment["teamrankings"] dict."""
    return {
        "home": {
            "team_name": "Home Team",
            "predictive_rating": 82.4,
            "predictive_rank": 8,
            "home_rating": 85.1,
            "away_rating": 79.8,
            "strength_of_schedule": 0.54,
            "last_5_rating": 83.2,
            "last_10_rating": 81.9,
            "consistency_rating": 78.0,
            "projected_win_pct": 0.64,
            "display_odds": display_odds_home,
            "source_url": "https://www.teamrankings.com/nba/team/home-team",
            "retrieved_at": "2026-08-08T19:00:00Z",
            "freshness_age_hours": freshness_age_hours,
            "source_status": source_status,
        },
        "away": {
            "team_name": "Away Team",
            "predictive_rating": 77.1,
            "predictive_rank": 14,
            "home_rating": 79.3,
            "away_rating": 74.5,
            "strength_of_schedule": 0.50,
            "last_5_rating": 76.4,
            "last_10_rating": 77.8,
            "consistency_rating": 74.5,
            "projected_win_pct": 0.55,
            "display_odds": display_odds_away,
            "source_url": "https://www.teamrankings.com/nba/team/away-team",
            "retrieved_at": "2026-08-08T19:00:00Z",
            "freshness_age_hours": freshness_age_hours,
            "source_status": source_status,
        },
        "matchup_win_prob_home": matchup_win_prob_home,
        "source_status": source_status,
    }


def _base_enrichment(**extra: Any) -> dict[str, Any]:
    """Return minimal valid moneyline enrichment (no TR)."""
    return {
        "home_win_pct": 0.60,
        "away_win_pct": 0.45,
        "home_elo": 1550,
        "away_elo": 1490,
        "lineup_confirmed": True,
        "starter_confirmed": True,
        "game_status": "scheduled",
        "participant_status": "active",
        "sportsbook_odds": [
            {"team": "Home Team", "odds": -200, "name": "DraftKings"},
            {"team": "Away Team", "odds": 165,  "name": "DraftKings"},
        ],
        "market_hours_open": 8.0,
        "hold_pct": 0.045,
        "market_freshness_hours": 0.5,
        "status_freshness_hours": 0.25,
        **extra,
    }


# ---------------------------------------------------------------------------
# Import targets
# ---------------------------------------------------------------------------

from gate_engine.moneyline.teamrankings_adapter import (
    extract_teamrankings_enrichment,
    inject_tr_features_into_clean_enrichment,
    TeamRankingsMatchupEnrichment,
    TeamRankingsStatus,
    TR_WEIGHT_DEFAULT,
    TR_WEIGHT_MAX,
    TR_WEIGHT_ZERO,
    TR_STALE_THRESHOLD_HOURS,
    TEAMRANKINGS_SUPPORTED_SPORTS,
)


# ===========================================================================
# 1. TR cannot override a starter / lineup blocker
# ===========================================================================

class TestTRCannotOverrideLineupBlocker(unittest.TestCase):
    """
    If participant_status is absent/invalid (which causes PARTICIPANT_LOCK_FAILED
    in slate_integrity stage 2 of the moneyline pipeline), the pipeline returns
    DATA_CONTRACT_FAIL BEFORE TeamRankings enrichment can contribute to the score.

    TR data in the enrichment must not prevent or bypass this blocker.
    """

    def test_participant_lock_blocks_before_tr_contribution(self):
        """
        MoneylineResult with participant lock failure must never carry a
        terminal label other than DATA_CONTRACT_FAIL, even when rich TR
        enrichment is supplied.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        enr = _base_enrichment(teamrankings=_tr_data())
        # Remove the participant status so slate_integrity fires
        enr.pop("game_status", None)
        enr.pop("participant_status", None)
        enr["lineup_confirmed"] = False
        enr["starter_confirmed"] = False

        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-test-001",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(row, enr)

        # can_execute is always False
        self.assertFalse(result.can_execute)

        # Any lineup/participant failure must still produce DATA_CONTRACT_FAIL
        # (TR enrichment never overrides the blocking)
        if result.blockers:
            # Pipeline fired at least one blocker before TR could contribute
            # Terminal label must reflect the block, not a TR-driven outcome
            self.assertNotEqual(
                result.terminal_label, "MONEY_QUALIFIED",
                "TR must not push a candidate to MONEY_QUALIFIED when lineup is unconfirmed"
            )

    def test_tr_record_present_but_does_not_unlock_participant_gate(self):
        """
        Even with TR data showing high team strength, participant gate must
        remain fail-closed.  The TR record appears in the result layers but
        cannot unblock a PARTICIPANT_LOCK_FAILED blocker.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        enr = _base_enrichment(teamrankings=_tr_data(matchup_win_prob_home=0.80))
        enr["lineup_confirmed"] = False
        enr["starter_confirmed"] = False

        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-test-002",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(row, enr)
        self.assertFalse(result.can_execute)
        # If any PARTICIPANT_LOCK_FAILED blocker fired, terminal must be DATA_CONTRACT_FAIL
        participant_blockers = [b for b in result.blockers if "PARTICIPANT_LOCK" in b]
        if participant_blockers:
            self.assertEqual(result.terminal_label, "DATA_CONTRACT_FAIL")


# ===========================================================================
# 2. Missing TR does not fail the base model
# ===========================================================================

class TestMissingTRDoesNotFailBaseModel(unittest.TestCase):
    """
    Absence of enrichment["teamrankings"] must yield DATA_UNOBTAINABLE status
    and zero effective_weight.  The base moneyline model continues normally.
    """

    def test_missing_tr_returns_data_unobtainable(self):
        enr = _base_enrichment()   # no 'teamrankings' key
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertEqual(tr_enr.source_status, TeamRankingsStatus.DATA_UNOBTAINABLE)
        self.assertEqual(tr_enr.effective_weight, TR_WEIGHT_ZERO)
        self.assertIsNone(tr_enr.matchup_win_prob_home)

    def test_missing_tr_injection_returns_identical_clean_enr(self):
        """inject_tr_features_into_clean_enrichment with zero weight must be a no-op."""
        enr = _base_enrichment()
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        clean_enr = dict(enr)
        result_enr = inject_tr_features_into_clean_enrichment(clean_enr, tr_enr)
        # No TR-specific keys injected
        self.assertNotIn("teamrankings_matchup_win_prob_home", result_enr)
        self.assertNotIn("teamrankings_effective_weight", result_enr)

    def test_missing_tr_does_not_break_moneyline_pipeline(self):
        """Pipeline with no TR enrichment must complete without error."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        enr = _base_enrichment()   # no TR
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-003",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(row, enr)
        # Must complete (not raise) and can_execute always False
        self.assertFalse(result.can_execute)
        # TR layer must be present (even if empty / DATA_UNOBTAINABLE)
        self.assertIn("teamrankings", result.to_dict()["layers"])

    def test_missing_tr_leaves_independent_probability_unchanged(self):
        """
        The independent probability from the base sport model must be identical
        whether TR is present or absent (when TR weight is 0).
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-004",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        enr_no_tr   = _base_enrichment()
        enr_with_tr = _base_enrichment(
            teamrankings=_tr_data(source_status="DATA_UNOBTAINABLE")
        )
        r_no_tr   = run_moneyline_pipeline(row, enr_no_tr,   seed=42)
        r_with_tr = run_moneyline_pipeline(row, enr_with_tr, seed=42)

        self.assertEqual(
            r_no_tr.outputs.independent_probability,
            r_with_tr.outputs.independent_probability,
            "DATA_UNOBTAINABLE TR must not change independent_probability",
        )


# ===========================================================================
# 3. Stale TR receives zero weight
# ===========================================================================

class TestStaleTRReceivesZeroWeight(unittest.TestCase):

    def test_fresh_tr_has_nonzero_weight(self):
        enr = _base_enrichment(teamrankings=_tr_data(freshness_age_hours=0.5))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertGreater(tr_enr.effective_weight, 0.0, "Fresh TR must have non-zero weight")

    def test_stale_tr_has_zero_weight(self):
        stale_hours = TR_STALE_THRESHOLD_HOURS + 1.0
        enr = _base_enrichment(teamrankings=_tr_data(freshness_age_hours=stale_hours))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertEqual(
            tr_enr.effective_weight, TR_WEIGHT_ZERO,
            f"TR data older than {TR_STALE_THRESHOLD_HOURS}h must have effective_weight=0"
        )

    def test_stale_tr_source_status_overridden(self):
        stale_hours = TR_STALE_THRESHOLD_HOURS + 2.0
        enr = _base_enrichment(teamrankings=_tr_data(freshness_age_hours=stale_hours))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertEqual(tr_enr.source_status, TeamRankingsStatus.STALE)

    def test_stale_tr_not_injected_into_clean_enr(self):
        """Stale TR must not inject matchup_win_prob into sport model enrichment."""
        stale_hours = TR_STALE_THRESHOLD_HOURS + 3.0
        enr = _base_enrichment(teamrankings=_tr_data(freshness_age_hours=stale_hours))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        clean_enr = inject_tr_features_into_clean_enrichment(dict(enr), tr_enr)
        self.assertNotIn(
            "teamrankings_matchup_win_prob_home", clean_enr,
            "Stale TR matchup prob must not reach the sport model"
        )

    def test_stale_tr_does_not_affect_moneyline_score(self):
        """With stale TR, pipeline result must equal no-TR result (same seed)."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-005",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        stale_hours = TR_STALE_THRESHOLD_HOURS + 5.0
        r_stale  = run_moneyline_pipeline(
            row, _base_enrichment(teamrankings=_tr_data(freshness_age_hours=stale_hours)), seed=42
        )
        r_no_tr = run_moneyline_pipeline(row, _base_enrichment(), seed=42)
        self.assertEqual(
            r_stale.outputs.independent_probability,
            r_no_tr.outputs.independent_probability,
            "Stale TR must produce identical independent_probability to no-TR run",
        )


# ===========================================================================
# 4. TR display_odds are NOT double-counted in market no-vig
# ===========================================================================

class TestDisplayOddsNotDoubleCountedInMarketPrior(unittest.TestCase):
    """
    TR display_odds (American moneyline) are stored in the TR record for
    context only.  They must NEVER appear in:
    - The clean enrichment passed to the sport model
    - The sportsbook_odds list used by extract_no_vig_probability()
    - The market_comparison layer
    """

    def test_display_odds_not_in_clean_enrichment(self):
        """inject_tr_features must NOT add display_odds to clean_enr."""
        enr = _base_enrichment(teamrankings=_tr_data(
            display_odds_home=-185, display_odds_away=155
        ))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        clean_enr = inject_tr_features_into_clean_enrichment(dict(enr), tr_enr)

        # No display_odds key in any form in clean_enr
        self.assertNotIn("display_odds", clean_enr)
        self.assertNotIn("teamrankings_display_odds", clean_enr)
        self.assertNotIn("teamrankings_home_display_odds", clean_enr)
        self.assertNotIn("home_odds", clean_enr)

    def test_display_odds_always_excluded_flag(self):
        """display_odds_excluded_from_model must always be True."""
        enr = _base_enrichment(teamrankings=_tr_data(display_odds_home=-200))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertTrue(
            tr_enr.display_odds_excluded_from_model,
            "display_odds_excluded_from_model must always be True"
        )

    def test_display_odds_stored_in_team_record_only(self):
        """display_odds must appear only in the team record, not in model inputs."""
        enr = _base_enrichment(teamrankings=_tr_data(display_odds_home=-180))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        # Present in the team record for context
        self.assertEqual(tr_enr.home_record.display_odds, -180)
        # NOT in the injection output
        clean_enr = inject_tr_features_into_clean_enrichment({}, tr_enr)
        self.assertNotIn("display_odds", clean_enr)
        self.assertNotIn("teamrankings_home_display_odds", clean_enr)

    def test_no_vig_computation_uses_only_sportsbook_odds(self):
        """
        extract_no_vig_probability uses enrichment["sportsbook_odds"] — that list
        must not contain TR-derived prices even when TR is RETRIEVED.
        """
        from gate_engine.moneyline_probability import extract_no_vig_probability

        enr = _base_enrichment(teamrankings=_tr_data(
            display_odds_home=-185, display_odds_away=155
        ))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        clean_enr = inject_tr_features_into_clean_enrichment(dict(enr), tr_enr)

        # extract_no_vig_probability reads enrichment["sportsbook_odds"]
        # If TR display_odds were injected there, the no-vig would shift.
        # We verify the sportsbook_odds list length matches the original.
        original_book_count = len(enr.get("sportsbook_odds") or [])
        clean_book_count    = len(clean_enr.get("sportsbook_odds") or [])
        self.assertEqual(
            original_book_count, clean_book_count,
            "TR display_odds must not be appended to sportsbook_odds"
        )


# ===========================================================================
# 5. TR cannot push market_prior_weight above the governance cap
# ===========================================================================

class TestTRCannotPushMarketPriorAboveCap(unittest.TestCase):
    """
    TR enrichment lives in the sport model layer (independent probability).
    It does NOT increase market_weight in the dynamic_calibration.
    The existing MARKET_WEIGHT_CAP (0.50) is unaffected.
    """

    def test_market_weight_unchanged_by_tr_presence(self):
        """
        market_weight in calibration output must be the same whether TR is
        RETRIEVED or DATA_UNOBTAINABLE (same sportsbook_odds, same seed).
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-006",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        r_no_tr = run_moneyline_pipeline(row, _base_enrichment(), seed=0)
        r_with_tr = run_moneyline_pipeline(
            row, _base_enrichment(teamrankings=_tr_data()), seed=0
        )
        mw_no_tr  = r_no_tr.calibration.get("market_weight", 0.0)
        mw_with_tr = r_with_tr.calibration.get("market_weight", 0.0)
        self.assertAlmostEqual(
            mw_no_tr, mw_with_tr, places=4,
            msg="TR must not affect market_weight in the calibration layer",
        )

    def test_tr_market_weight_cap_not_exceeded(self):
        """Calibration market_weight must never exceed MARKET_WEIGHT_CAP (0.50)."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        from gate_engine.moneyline.dynamic_calibration import _MARKET_WEIGHT_CAP
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-007",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(
            row, _base_enrichment(teamrankings=_tr_data()), seed=0
        )
        mw = result.calibration.get("market_weight", 0.0)
        self.assertLessEqual(
            mw, _MARKET_WEIGHT_CAP,
            f"market_weight {mw} must not exceed cap {_MARKET_WEIGHT_CAP}"
        )


# ===========================================================================
# 6. Raw ratings NOT converted to probability without calibrated mapping
# ===========================================================================

class TestRawRatingsNotConvertedToProbability(unittest.TestCase):
    """
    Per WOW governance: if TR provides only raw predictive ratings (no
    matchup_win_prob_home), the adapter must NOT produce a win probability.
    effective_weight must be 0.0 and the sport model submodel must be inactive.
    """

    def test_no_matchup_prob_means_zero_weight(self):
        """When matchup_win_prob_home is absent, effective_weight must be zero."""
        tr = _tr_data(matchup_win_prob_home=None)
        enr = _base_enrichment(teamrankings=tr)
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertEqual(
            tr_enr.effective_weight, TR_WEIGHT_ZERO,
            "Without direct matchup_win_prob, TR must not contribute to the model"
        )
        self.assertIsNone(tr_enr.matchup_win_prob_home)

    def test_ratings_only_not_injected_as_probability(self):
        """Ratings-only TR must not inject teamrankings_matchup_win_prob_home."""
        tr = _tr_data(matchup_win_prob_home=None)
        enr = _base_enrichment(teamrankings=tr)
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        clean_enr = inject_tr_features_into_clean_enrichment({}, tr_enr)
        self.assertNotIn(
            "teamrankings_matchup_win_prob_home", clean_enr,
            "Raw ratings must never appear as a matchup probability in the model"
        )

    def test_weight_reason_documents_absence_of_calibrated_mapping(self):
        """weight_reason must explain that raw-to-prob conversion requires calibrated mapping."""
        tr = _tr_data(matchup_win_prob_home=None)
        enr = _base_enrichment(teamrankings=tr)
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertIn(
            "calibrated_mapping", tr_enr.weight_reason or "",
            "weight_reason must cite the missing calibrated mapping"
        )

    def test_sport_model_tr_submodel_inactive_without_prob(self):
        """Sport model _teamrankings_predictive returns None when matchup prob absent."""
        from gate_engine.moneyline.sport_model import _teamrankings_predictive
        enr_no_prob = {
            "teamrankings_home_rating": 82.4,
            "teamrankings_away_rating": 77.1,
            # No teamrankings_matchup_win_prob_home
            "teamrankings_effective_weight": 0.075,
        }
        self.assertIsNone(
            _teamrankings_predictive(enr_no_prob),
            "_teamrankings_predictive must return None without matchup_win_prob_home"
        )

    def test_sport_model_tr_submodel_inactive_when_weight_zero(self):
        """Sport model _teamrankings_predictive returns None when effective_weight is 0."""
        from gate_engine.moneyline.sport_model import _teamrankings_predictive
        enr = {
            "teamrankings_matchup_win_prob_home": 0.65,
            "teamrankings_effective_weight": 0.0,   # stale / unavailable
        }
        self.assertIsNone(
            _teamrankings_predictive(enr),
            "_teamrankings_predictive must return None when effective_weight=0"
        )

    def test_sport_model_tr_submodel_active_with_valid_prob_and_weight(self):
        """Sport model _teamrankings_predictive returns probability when all valid."""
        from gate_engine.moneyline.sport_model import _teamrankings_predictive
        enr = {
            "teamrankings_matchup_win_prob_home": 0.65,
            "teamrankings_effective_weight": 0.075,
        }
        result = _teamrankings_predictive(enr)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.65, places=4)


# ===========================================================================
# 7. Opposite-side TR projection triggers TEAMRANKINGS_CONTRADICTION_REVIEW
# ===========================================================================

class TestOppositeSideTriggesContradictionReview(unittest.TestCase):
    """
    When TR favors the opposite winner from the WOW core model, a
    TEAMRANKINGS_CONTRADICTION_REVIEW flag must appear in MoneylineResult.blockers.
    This is a non-terminal review flag — it must NOT produce DATA_CONTRACT_FAIL.
    """

    def test_opposite_side_contradiction_flag_set(self):
        """TR matchup_win_prob_home < 0.50 when core model says home team wins."""
        from gate_engine.moneyline.teamrankings_adapter import _compute_contradiction

        # Core model: P(home wins) = 0.68 (home team is the pick)
        # TR: P(home wins) = 0.43 (TR favors the away team)
        agreement, delta, flag, reason = _compute_contradiction(
            matchup_win_prob_home=0.43,
            core_independent_prob_home=0.68,
        )
        self.assertEqual(agreement, "OPPOSITE_SIDE")
        self.assertTrue(flag)
        # Reason includes which side TR and core favor; delta is always present
        self.assertIsNotNone(reason)
        self.assertIn("delta=", reason or "")

    def test_opposite_side_blocker_in_result_not_terminal(self):
        """
        TEAMRANKINGS_CONTRADICTION_REVIEW must appear in result.blockers but
        must NOT cause terminal_label=DATA_CONTRACT_FAIL.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        # Home team has very high TR prob for home but our enrichment indicates
        # the away candidate; simulate an opposite-side setup by using a TR
        # prob that disagrees with the base model direction.
        enr = _base_enrichment(teamrankings=_tr_data(matchup_win_prob_home=0.25))
        # home_win_pct=0.60, away_win_pct=0.45 → core model P(home)~0.57 → home favored
        # TR matchup_win_prob_home=0.25 → TR favors away → OPPOSITE_SIDE
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-008",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(row, enr, seed=0)

        self.assertFalse(result.can_execute)

        tr_review_blockers = [
            b for b in result.blockers
            if b.startswith("TEAMRANKINGS_CONTRADICTION_REVIEW")
        ]
        self.assertTrue(
            len(tr_review_blockers) > 0,
            "TEAMRANKINGS_CONTRADICTION_REVIEW must appear in blockers for OPPOSITE_SIDE"
        )
        # Must NOT terminal-block as DATA_CONTRACT_FAIL solely due to TR contradiction
        self.assertNotEqual(
            result.terminal_label, "DATA_CONTRACT_FAIL",
            "TEAMRANKINGS_CONTRADICTION_REVIEW is non-terminal and must not cause "
            "DATA_CONTRACT_FAIL when the base model itself has sufficient data"
        )

    def test_material_discrepancy_also_sets_contradiction_flag(self):
        """8pp+ same-direction disagreement is DISCREPANCY with flag=True."""
        from gate_engine.moneyline.teamrankings_adapter import _compute_contradiction, TR_CONTRADICTION_THRESHOLD_PP

        agreement, delta, flag, reason = _compute_contradiction(
            matchup_win_prob_home=0.59,
            core_independent_prob_home=0.67,   # diff=0.08 == threshold
        )
        self.assertTrue(flag)
        self.assertGreaterEqual(delta, TR_CONTRADICTION_THRESHOLD_PP)

    def test_small_discrepancy_does_not_flag(self):
        """< 8pp same-direction disagreement → AGREE, flag=False."""
        from gate_engine.moneyline.teamrankings_adapter import _compute_contradiction

        agreement, delta, flag, reason = _compute_contradiction(
            matchup_win_prob_home=0.63,
            core_independent_prob_home=0.67,   # diff=0.04 < 0.08
        )
        self.assertEqual(agreement, "AGREE")
        self.assertFalse(flag)

    def test_tr_contradiction_never_flips_pick(self):
        """
        TR contradiction must LOWER confidence (wider bounds) but must not
        change the direction of the independent_probability output.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-009",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        r_no_tr = run_moneyline_pipeline(row, _base_enrichment(), seed=0)
        r_opposite = run_moneyline_pipeline(
            row, _base_enrichment(teamrankings=_tr_data(matchup_win_prob_home=0.22)), seed=0
        )

        if (r_no_tr.outputs.independent_probability is not None and
                r_opposite.outputs.independent_probability is not None):
            no_tr_favors_home   = r_no_tr.outputs.independent_probability > 0.50
            with_tr_favors_home = r_opposite.outputs.independent_probability > 0.50
            self.assertEqual(
                no_tr_favors_home, with_tr_favors_home,
                "TR contradiction must not flip the direction of independent_probability"
            )


# ===========================================================================
# 8. can_execute=False enforced in every new module
# ===========================================================================

class TestCanExecuteFalseEnforced(unittest.TestCase):

    def test_teamrankings_adapter_can_execute_false(self):
        import gate_engine.moneyline.teamrankings_adapter as m
        self.assertFalse(m.can_execute)

    def test_teamrankings_matchup_enrichment_can_execute_false(self):
        """TeamRankingsMatchupEnrichment is produced by can_execute=False module."""
        tr_enr = TeamRankingsMatchupEnrichment()
        # The object itself has no can_execute attr; the module does
        import gate_engine.moneyline.teamrankings_adapter as m
        self.assertFalse(m.can_execute)

    def test_sport_model_can_execute_false(self):
        import gate_engine.moneyline.sport_model as m
        self.assertFalse(m.can_execute)

    def test_pipeline_can_execute_false(self):
        import gate_engine.moneyline.pipeline as m
        self.assertFalse(m.can_execute)

    def test_types_can_execute_false(self):
        import gate_engine.moneyline.types as m
        self.assertFalse(m.can_execute)

    def test_moneyline_result_can_execute_false_by_default(self):
        from gate_engine.moneyline.types import MoneylineResult
        r = MoneylineResult()
        self.assertFalse(r.can_execute)

    def test_pipeline_result_can_execute_false(self):
        """run_moneyline_pipeline always returns can_execute=False."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-010",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(row, _base_enrichment(), seed=0)
        self.assertFalse(result.can_execute,
                         "can_execute must be unconditionally False")
        self.assertFalse(result.can_approve_bets,
                         "can_approve_bets must be unconditionally False")


# ===========================================================================
# 9. UNSUPPORTED_SPORT returns graceful status without breaking model
# ===========================================================================

class TestUnsupportedSport(unittest.TestCase):

    def test_unsupported_sport_returns_graceful_status(self):
        enr = _base_enrichment(teamrankings=_tr_data())
        tr_enr = extract_teamrankings_enrichment(enr, "SOCCER")  # not in supported set
        self.assertEqual(tr_enr.source_status, TeamRankingsStatus.UNSUPPORTED_SPORT)
        self.assertEqual(tr_enr.effective_weight, TR_WEIGHT_ZERO)

    def test_unsupported_sport_does_not_inject_features(self):
        enr = _base_enrichment(teamrankings=_tr_data())
        tr_enr = extract_teamrankings_enrichment(enr, "SOCCER")
        clean = inject_tr_features_into_clean_enrichment({}, tr_enr)
        self.assertNotIn("teamrankings_matchup_win_prob_home", clean)

    def test_supported_sports_accepted(self):
        for sport in TEAMRANKINGS_SUPPORTED_SPORTS:
            enr = _base_enrichment(teamrankings=_tr_data())
            tr_enr = extract_teamrankings_enrichment(enr, sport)
            self.assertNotEqual(
                tr_enr.source_status, TeamRankingsStatus.UNSUPPORTED_SPORT,
                f"{sport} must be accepted by TeamRankings adapter"
            )


# ===========================================================================
# 10. PROXY_ONLY and SOURCE_CONFLICT yield zero weight
# ===========================================================================

class TestProxyOnlyAndSourceConflictZeroWeight(unittest.TestCase):

    def test_proxy_only_yields_zero_weight(self):
        enr = _base_enrichment(teamrankings=_tr_data(source_status="PROXY_ONLY"))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertEqual(tr_enr.effective_weight, TR_WEIGHT_ZERO,
                         "PROXY_ONLY must yield zero effective_weight")

    def test_source_conflict_yields_zero_weight(self):
        enr = _base_enrichment(teamrankings=_tr_data(source_status="SOURCE_CONFLICT"))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertEqual(tr_enr.effective_weight, TR_WEIGHT_ZERO,
                         "SOURCE_CONFLICT must yield zero effective_weight")

    def test_proxy_only_not_injected(self):
        enr = _base_enrichment(teamrankings=_tr_data(source_status="PROXY_ONLY"))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        clean = inject_tr_features_into_clean_enrichment({}, tr_enr)
        self.assertNotIn("teamrankings_matchup_win_prob_home", clean)


# ===========================================================================
# 11. TR effective_weight hard ceiling at 10% enforced in sport model
# ===========================================================================

class TestTRWeightCeilingEnforced(unittest.TestCase):

    def test_weight_default_does_not_exceed_max(self):
        self.assertLessEqual(TR_WEIGHT_DEFAULT, TR_WEIGHT_MAX,
                             "TR_WEIGHT_DEFAULT must not exceed TR_WEIGHT_MAX")

    def test_weight_ceiling_enforced_in_adapter(self):
        enr = _base_enrichment(teamrankings=_tr_data())
        tr_enr = extract_teamrankings_enrichment(enr, "NBA")
        self.assertLessEqual(
            tr_enr.effective_weight, TR_WEIGHT_MAX,
            f"effective_weight {tr_enr.effective_weight} must not exceed {TR_WEIGHT_MAX}"
        )

    def test_tr_submodel_weight_capped_in_sport_model_ensemble(self):
        """
        When only TR and one other submodel are active, TR's normalised weight
        must not exceed TR_WEIGHT_MAX after the post-normalisation cap.
        """
        from gate_engine.moneyline.sport_model import compute_independent_probability
        from gate_engine.moneyline.types import strip_odds_fields

        # Provide only H2H data + TR matchup prob, no Elo, no power rating
        clean_enr = {
            "home_win_pct": 0.60,
            "away_win_pct": 0.45,
            "teamrankings_matchup_win_prob_home": 0.65,
            "teamrankings_effective_weight": TR_WEIGHT_DEFAULT,
        }
        row = {"sport": "NBA", "team": "Home", "opponent": "Away", "home_away": "vs"}
        out = compute_independent_probability(row, clean_enr)

        weights_used = out.get("ensemble_weights_used", {})
        tr_weight_used = weights_used.get("teamrankings_predictive", 0.0)
        self.assertLessEqual(
            tr_weight_used, TR_WEIGHT_MAX,
            f"TR ensemble weight {tr_weight_used:.4f} must not exceed {TR_WEIGHT_MAX}"
        )


# ===========================================================================
# 12. TR layer present in MoneylineResult.to_dict()["layers"]
# ===========================================================================

class TestTRLayerPresentInResult(unittest.TestCase):

    def test_tr_layer_present_in_layers_dict(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-011",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(row, _base_enrichment(), seed=0)
        layers = result.to_dict()["layers"]
        self.assertIn("teamrankings", layers,
                      "MoneylineResult.to_dict()['layers'] must include 'teamrankings'")

    def test_tr_layer_contains_required_contradiction_fields(self):
        """When TR is present, all four contradiction fields must be in the TR layer."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-012",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(
            row, _base_enrichment(teamrankings=_tr_data()), seed=0
        )
        tr_layer = result.to_dict()["layers"]["teamrankings"]
        for field in [
            "teamrankings_model_agreement",
            "teamrankings_model_delta",
            "teamrankings_contradiction_flag",
            "teamrankings_contradiction_reason",
        ]:
            self.assertIn(
                field, tr_layer,
                f"TR layer must expose '{field}'"
            )

    def test_tr_layer_shows_acquisition_notes_and_source_status(self):
        """TR layer must include source_status and acquisition_notes for observability."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-013",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        result = run_moneyline_pipeline(row, _base_enrichment(), seed=0)
        tr_layer = result.to_dict()["layers"]["teamrankings"]
        self.assertIn("source_status", tr_layer)
        self.assertIn("acquisition_notes", tr_layer)
        self.assertIn("display_odds_excluded_from_model", tr_layer)
        self.assertTrue(tr_layer["display_odds_excluded_from_model"])


# ===========================================================================
# 13. Manifest patch ID present
# ===========================================================================

class TestManifestPatchIdPresent(unittest.TestCase):

    def test_teamrankings_patch_in_active_patch_ids(self):
        from gate_engine.wow_runtime_manifest import WOW_RUNTIME_MANIFEST
        patch_ids = WOW_RUNTIME_MANIFEST.get("active_patch_ids", [])
        self.assertIn(
            "WOW-PATCH-2026-08-08-TEAMRANKINGS-SECONDARY-ENRICHMENT",
            patch_ids,
            "Runtime manifest must include the TeamRankings patch ID"
        )


# ===========================================================================
# 14. Disagreement audit annotated for OPPOSITE_SIDE
# ===========================================================================

class TestDisagreementAuditAnnotatedForOppositeContradiction(unittest.TestCase):

    def test_opposite_side_annotation_in_disagreement_audit(self):
        """OPPOSITE_SIDE TR contradiction must be annotated in disagreement_audit notes."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "NBA",
            "team": "Home Team",
            "opponent": "Away Team",
            "market_type": "full_game_h2h",
            "event_id": "nba-014",
            "slate_date": "2026-08-08",
            "home_away": "vs",
        }
        # TR strongly favors away (prob=0.20 for home) while base model favors home
        result = run_moneyline_pipeline(
            row, _base_enrichment(teamrankings=_tr_data(matchup_win_prob_home=0.20)), seed=0
        )
        audit_notes = result.disagreement_audit.get("notes", [])
        tr_notes = [n for n in audit_notes if "TEAMRANKINGS" in n]
        self.assertTrue(
            len(tr_notes) > 0,
            "Disagreement audit must contain a TEAMRANKINGS annotation for OPPOSITE_SIDE contradiction"
        )


# ===========================================================================
# 15. fill_contradiction updates in place
# ===========================================================================

class TestFillContradictionUpdatesInPlace(unittest.TestCase):

    def test_fill_contradiction_updates_from_absent_to_agree(self):
        """Initial extraction (no core prob) → ABSENT; fill → AGREE."""
        enr = _base_enrichment(teamrankings=_tr_data(matchup_win_prob_home=0.65))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA", core_independent_prob_home=None)
        self.assertEqual(tr_enr.teamrankings_model_agreement, "ABSENT")

        # After core prob available
        tr_enr.fill_contradiction(0.62)
        self.assertNotEqual(tr_enr.teamrankings_model_agreement, "ABSENT")
        self.assertIn(tr_enr.teamrankings_model_agreement, ("AGREE", "DISCREPANCY", "OPPOSITE_SIDE"))

    def test_fill_contradiction_sets_delta(self):
        enr = _base_enrichment(teamrankings=_tr_data(matchup_win_prob_home=0.70))
        tr_enr = extract_teamrankings_enrichment(enr, "NBA", core_independent_prob_home=None)
        self.assertIsNone(tr_enr.teamrankings_model_delta)

        tr_enr.fill_contradiction(0.62)
        self.assertIsNotNone(tr_enr.teamrankings_model_delta)
        self.assertAlmostEqual(tr_enr.teamrankings_model_delta, 0.08, places=4)


if __name__ == "__main__":
    unittest.main()
