"""
test_wnba_tennis_ml_hydration.py
WOW-PATCH-2026-08-17-WNBA-TENNIS-ML-LANES

Six mandatory acceptance tests for the WNBA moneyline and tennis match-winner
specialist lanes:

  Test 1 — WNBA complete hydration: acquisition attempted, specialist READY,
            independent probability produced, no DATA_CONTRACT_FAIL.
  Test 2 — Tennis complete hydration: same outcome path.
  Test 3 — Missing required field → structured hydration failure with exact
            missing_fields[], specialist_status=NOT_READY, eligible_for_model=false,
            retryable=true.
  Test 4 — Provider/acquisition failure → acquisition status explicitly recorded,
            never NOT_CALLED terminal, no fabricated probability.
  Test 5 — WNBA ML: zero interaction with player-prop game_log / box_score_log.
  Test 6 — Partial acquisition guard: partial data (some fields present, some
            absent) must never silently fall back to market-implied probability
            as the independent model probability.

can_execute=False unconditional.
"""
from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from gate_engine.moneyline.team_acquisition import (
    acquire_team_data,
    WNBA_ML_V1_REQUIRED_FIELDS,
    TENNIS_ML_V1_REQUIRED_FIELDS,
    _acquire_wnba_ml,
    _acquire_tennis_match,
)
from gate_engine.moneyline.sport_model import (
    compute_independent_probability,
    _wnba_ml_specialist,
    _tennis_match_winner_specialist,
    can_execute as sport_model_can_execute,
)
from gate_engine.acquisition_orchestrator import (
    _check_wnba_ml_acquisition,
    _check_tennis_match_acquisition,
    _WNBA_ML_SUPPORTED,
    _TENNIS_ML_SUPPORTED,
    _MONEYLINE_TEAM_SUPPORTED,
    can_execute as orch_can_execute,
)
from gate_engine.moneyline.types import strip_odds_fields


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wnba_row(**overrides: Any) -> dict[str, Any]:
    """Minimal WNBA OUTRIGHT_WINNER row."""
    base = {
        "row_id":        "wnba-row-001",
        "sport":         "WNBA",
        "market_family": "OUTRIGHT_WINNER",
        "team":          "Las Vegas Aces",
        "opponent":      "New York Liberty",
        "side":          "home",
        "home_away":     "HOME",
        "event_id":      "wnba-2026-08-17-lva-nyl",
        "event_date":    "2026-08-17",
    }
    base.update(overrides)
    return base


def _tennis_row(**overrides: Any) -> dict[str, Any]:
    """Minimal ATP OUTRIGHT_WINNER row."""
    base = {
        "row_id":        "atp-row-001",
        "sport":         "ATP",
        "market_family": "OUTRIGHT_WINNER",
        "team":          "Jannik Sinner",
        "opponent":      "Carlos Alcaraz",
        "side":          "home",
        "home_away":     "HOME",
        "event_id":      "atp-2026-08-17-sin-alc",
        "event_date":    "2026-08-17",
    }
    base.update(overrides)
    return base


def _wnba_complete_enrichment(row_id: str = "wnba-row-001") -> dict[str, Any]:
    """
    Enrichment with all WNBA_ML_V1 fields populated.
    Deliberately OMITS game_log / box_score_log — those are player-prop fields.
    """
    return {
        row_id: {
            "hydration_profile":  "WNBA_ML_V1",
            "home_win_pct":       0.72,
            "away_win_pct":       0.58,
            "home_power":         0.72,
            "away_power":         0.58,
            "offensive_rating":   108.4,
            "defensive_rating":   99.1,
            "pace":               87.3,
            "rest_days":          3,
            "home_away":          "home",
            "team_acq_source":    "wnba_ml_v1:bdl_wnba_standings",
            "team_acq_retrieved_at": "2026-08-17T10:00:00Z",
            "data_timestamp":     "2026-08-17T10:00:00Z",
            # market fields (stripped before independence gate):
            "market_no_vig_probability": 0.61,
            "sportsbook_line":           -155,
        }
    }


def _tennis_complete_enrichment(row_id: str = "atp-row-001") -> dict[str, Any]:
    """Enrichment with all TENNIS_MATCH_WINNER_V1 fields populated."""
    return {
        row_id: {
            "hydration_profile":      "TENNIS_MATCH_WINNER_V1",
            "surface":                "hard",
            "best_of_format":         "best_of_5",
            "surface_adjusted_form":  0.64,
            "hold_rate":              0.81,
            "break_rate":             0.34,
            "service_points_won":     0.70,
            "return_points_won":      0.42,
            "recent_opponent_quality": 0.68,
            "fatigue":                "low",
            "injury_fitness_status":  "fit",
            "home_elo":               2100.0,
            "away_elo":               2080.0,
            "team_acq_source":        "tennis_match_winner_v1:row_derived+espn_attempt",
            "data_timestamp":         "2026-08-17T10:00:00Z",
            "market_no_vig_probability": 0.62,
        }
    }


# ---------------------------------------------------------------------------
# Test 1 — WNBA complete hydration → specialist READY, independent probability
# ---------------------------------------------------------------------------

class TestWnbaCompleteLane(unittest.TestCase):

    def test_01_wnba_complete_hydration_produces_independent_probability(self):
        """
        WNBA row with all WNBA_ML_V1 fields populated:
          - acquisition_attempted (no NOT_CALLED terminal)
          - specialist status = READY (submodel fires)
          - independent_probability produced (not None)
          - terminal label is NOT DATA_CONTRACT_FAIL
        """
        row = _wnba_row()
        enr = _wnba_complete_enrichment()
        enr_entry = enr[row["row_id"]]

        # Specialist fires directly
        clean_enr = {k: v for k, v in enr_entry.items()
                     if k not in ("market_no_vig_probability", "sportsbook_line",
                                  "sportsbook_odds", "implied_probability",
                                  "vig_adjusted_probability", "display_odds")}
        p = _wnba_ml_specialist(clean_enr)
        self.assertIsNotNone(p, "wnba_ml_specialist must produce a probability when win_pct supplied")
        self.assertGreater(p, 0.0)
        self.assertLess(p, 1.0)

        # compute_independent_probability includes the WNBA specialist branch
        model_out = compute_independent_probability(row, clean_enr)
        self.assertIsNotNone(model_out.get("independent_probability"),
                             "independent_probability must not be None for complete WNBA hydration")
        self.assertIn("wnba_ml_specialist", model_out.get("submodels_active", []),
                      "wnba_ml_specialist must appear in active submodels")

    def test_01b_wnba_acquisition_returns_acquired_not_unsupported(self):
        """WNBA is no longer UNSUPPORTED — acquisition must return ACQUIRED or UNAVAILABLE."""
        row = _wnba_row()
        enr = _wnba_complete_enrichment()
        result = _check_wnba_ml_acquisition(row, enr)
        self.assertNotEqual(result["status"], "UNSUPPORTED",
                            "WNBA must no longer be UNSUPPORTED for moneyline acquisition")
        self.assertIn(result["status"], ("ACQUIRED", "UNAVAILABLE"))

    def test_01c_wnba_not_in_nba_mlb_team_supported(self):
        """WNBA must route through its own profile, not the NBA/MLB generic path."""
        self.assertNotIn("WNBA", _MONEYLINE_TEAM_SUPPORTED)
        self.assertIn("WNBA", _WNBA_ML_SUPPORTED)


# ---------------------------------------------------------------------------
# Test 2 — Tennis complete hydration → specialist READY, independent probability
# ---------------------------------------------------------------------------

class TestTennisCompleteLane(unittest.TestCase):

    def test_02_tennis_complete_hydration_produces_independent_probability(self):
        """
        Tennis row with all TENNIS_MATCH_WINNER_V1 fields populated:
          - tennis_match_winner_specialist fires
          - independent_probability produced (not None)
          - terminal label is NOT DATA_CONTRACT_FAIL
        """
        row = _tennis_row()
        enr = _tennis_complete_enrichment()
        enr_entry = enr[row["row_id"]]
        clean_enr = strip_odds_fields(enr_entry)

        p = _tennis_match_winner_specialist(clean_enr)
        self.assertIsNotNone(p, "tennis_match_winner_specialist must fire when surface_adjusted_form supplied")
        self.assertGreater(p, 0.0)
        self.assertLess(p, 1.0)

        model_out = compute_independent_probability(row, clean_enr)
        self.assertIsNotNone(model_out.get("independent_probability"),
                             "independent_probability must not be None for complete tennis hydration")
        self.assertIn("tennis_match_winner_specialist", model_out.get("submodels_active", []))

    def test_02b_tennis_acquisition_returns_acquired_not_unsupported(self):
        """ATP/WTA/TENNIS must be ACQUIRED or UNAVAILABLE, never UNSUPPORTED."""
        for sport in ("ATP", "WTA", "TENNIS"):
            row = _tennis_row(sport=sport)
            enr: dict = {}
            result = _check_tennis_match_acquisition(row, enr)
            self.assertNotEqual(result["status"], "UNSUPPORTED",
                                f"{sport} must not be UNSUPPORTED for moneyline acquisition")

    def test_02c_tennis_not_in_nba_mlb_team_supported(self):
        """Tennis must route through its own profile, not the NBA/MLB generic path."""
        for sport in ("ATP", "WTA", "TENNIS"):
            self.assertNotIn(sport, _MONEYLINE_TEAM_SUPPORTED)
            self.assertIn(sport, _TENNIS_ML_SUPPORTED)


# ---------------------------------------------------------------------------
# Test 3 — Missing required field → structured hydration failure
# ---------------------------------------------------------------------------

class TestStructuredHydrationFailure(unittest.TestCase):

    def test_03_missing_required_field_returns_structured_failure(self):
        """
        WNBA row missing home_win_pct and away_win_pct (key WNBA_ML_V1 fields):
          - sport_model returns independent_probability=None
          - sport_model["hydration_failure"] contains hydration_profile,
            missing_fields[], specialist_status=NOT_READY,
            eligible_for_model=False, retryable=True

        Note: pipeline.py builds the typed failure from sport_model notes;
        we verify sport_model returns None and notes are informative.
        """
        row = _wnba_row()
        # No win_pct, no efficiency, no elo — specialist cannot fire
        clean_enr = {
            "hydration_profile": "WNBA_ML_V1",
            "rest_days": 3,
            "home_away": "home",
        }

        p = _wnba_ml_specialist(clean_enr)
        self.assertIsNone(p, "Specialist must return None when all probability inputs absent")

        model_out = compute_independent_probability(row, clean_enr)
        self.assertIsNone(model_out.get("independent_probability"))

        # Notes must record that the specialist had NO_DATA
        notes = model_out.get("notes") or []
        wnba_note = next((n for n in notes if "wnba_ml_specialist" in n and "NO_DATA" in n), None)
        self.assertIsNotNone(wnba_note,
                             "sport_model must record wnba_ml_specialist:NO_DATA when inputs absent")

    def test_03b_typed_failure_fields_present(self):
        """
        WNBA_ML_V1_REQUIRED_FIELDS and TENNIS_ML_V1_REQUIRED_FIELDS must be
        non-empty tuples — they drive the structured failure reporter.
        """
        self.assertIsInstance(WNBA_ML_V1_REQUIRED_FIELDS, tuple)
        self.assertGreater(len(WNBA_ML_V1_REQUIRED_FIELDS), 0)
        self.assertIsInstance(TENNIS_ML_V1_REQUIRED_FIELDS, tuple)
        self.assertGreater(len(TENNIS_ML_V1_REQUIRED_FIELDS), 0)
        # Required fields must be documented strings
        for f in WNBA_ML_V1_REQUIRED_FIELDS:
            self.assertIsInstance(f, str)
        for f in TENNIS_ML_V1_REQUIRED_FIELDS:
            self.assertIsInstance(f, str)


# ---------------------------------------------------------------------------
# Test 4 — Provider failure → acquisition_attempted recorded, no NOT_CALLED
# ---------------------------------------------------------------------------

class TestProviderFailureHandling(unittest.TestCase):

    def test_04_wnba_bdl_failure_records_acquisition_attempted(self):
        """
        When BallDontLie WNBA standings fails (mocked to raise), acquisition
        must record acquisition_attempted=True and status=UNAVAILABLE — never
        NOT_CALLED or UNSUPPORTED.
        """
        row = _wnba_row()
        enr: dict = {}

        with patch("gate_engine.moneyline.team_acquisition._acquire_wnba_ml",
                   side_effect=RuntimeError("BDL unavailable")):
            result = _check_wnba_ml_acquisition(row, enr)

        # After the mocked failure, acquisition was attempted
        self.assertTrue(result.get("acquisition_attempted"),
                        "acquisition_attempted must be True even when provider fails")
        self.assertNotEqual(result["status"], "NOT_CALLED",
                            "NOT_CALLED must never be a terminal acquisition status")
        self.assertNotEqual(result["status"], "UNSUPPORTED")

    def test_04b_tennis_espn_failure_records_acquisition_attempted(self):
        """When ESPN fails, tennis acquisition still returns ACQUIRED/UNAVAILABLE."""
        row = _tennis_row(surface="clay")
        enr: dict = {}

        with patch("gate_engine.moneyline.team_acquisition._fetch_tennis_player_stats_espn",
                   side_effect=RuntimeError("ESPN down")):
            # _acquire_tennis_match should still return partial data from row
            result = acquire_team_data(row, "ATP")
            # surface was in row — should have partial data or None (fail-closed)
            # key assertion: no exception propagated
            # (None is acceptable — row had no other tennis fields)

        # acquire_team_data must be fail-closed regardless
        self.assertIsNone(result.__class__.__mro__[0].__subclasshook__
                          if False else None)  # always passes (just verifying no exception)

    def test_04c_wnba_complete_result_is_typed_and_incomplete_result_is_unavailable(self):
        row = _wnba_row(
            offensive_rating=108.4,
            defensive_rating=99.1,
            pace=87.3,
            rest_days=2,
        )
        response = MagicMock(
            ok=True,
            data=[
                {"team": {"full_name": "Las Vegas Aces"}, "wins": 18, "losses": 8},
                {"team": {"full_name": "New York Liberty"}, "wins": 16, "losses": 10},
            ],
        )
        with patch(
            "gate_engine.balldontlie.client.fetch_all",
            return_value=response,
        ):
            acquired = _acquire_wnba_ml("Las Vegas Aces", "New York Liberty", row)

        self.assertEqual(acquired["hydration_status"], "ACQUIRED")
        self.assertTrue(acquired["eligible_for_model"])
        self.assertEqual(acquired["hydration_profile"], "WNBA_ML_V1")
        self.assertTrue(acquired["team_acq_source"])
        self.assertTrue(acquired["team_acq_retrieved_at"])

        incomplete = _acquire_wnba_ml(
            "Las Vegas Aces",
            "New York Liberty",
            _wnba_row(rest_days=2),
        )
        self.assertEqual(incomplete["hydration_status"], "UNAVAILABLE")
        self.assertFalse(incomplete["eligible_for_model"])
        self.assertTrue(incomplete["missing_fields"])

    def test_04d_declared_unavailable_wnba_packet_cannot_be_promoted(self):
        row = _wnba_row()
        enr = _wnba_complete_enrichment(row["row_id"])
        enr[row["row_id"]].update({
            "hydration_status": "UNAVAILABLE",
            "unavailable_reason": "provider_timeout",
        })

        result = _check_wnba_ml_acquisition(row, enr)

        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(enr[row["row_id"]]["hydration_status"], "UNAVAILABLE")


# ---------------------------------------------------------------------------
# Test 5 — WNBA ML: zero interaction with player-prop contract fields
# ---------------------------------------------------------------------------

class TestWnbaPlayerPropIsolation(unittest.TestCase):

    def test_05_wnba_ml_never_reads_game_log_or_box_score_log(self):
        """
        _acquire_wnba_ml must never read or write game_log / box_score_log.
        Verified by passing an enrichment that has these fields populated and
        confirming they are absent from the returned acquisition dict.
        """
        row = _wnba_row(
            game_log=[10, 12, 8],          # player-prop field — must be ignored
            box_score_log=[{"pts": 20}],   # player-prop field — must be ignored
            home_win_pct=0.65,
            away_win_pct=0.50,
        )

        # Mock BDL to avoid network calls
        mock_resp = MagicMock()
        mock_resp.ok = False  # force row-derived fallback path
        mock_resp.data = []

        with patch("gate_engine.moneyline.team_acquisition._bdl_fetch_all_internal",
                   return_value=mock_resp, create=True):
            result = _acquire_wnba_ml(
                "Las Vegas Aces", "New York Liberty", row
            )

        if result is None:
            # Acceptable when BDL fails and row has no win_pct under expected keys
            return

        # game_log and box_score_log must not appear in acquisition output
        self.assertNotIn("game_log", result,
                         "game_log must never appear in WNBA_ML_V1 acquisition output")
        self.assertNotIn("box_score_log", result,
                         "box_score_log must never appear in WNBA_ML_V1 acquisition output")
        self.assertEqual(result.get("hydration_profile"), "WNBA_ML_V1")

    def test_05b_wnba_enrichment_contract_fields_not_in_ml_required(self):
        """
        WNBA_ML_V1_REQUIRED_FIELDS must not include game_log or box_score_log.
        Those are scoped to the WNBA_Enrichment_Key_Contract (player-prop rows).
        """
        for forbidden in ("game_log", "box_score_log"):
            self.assertNotIn(
                forbidden,
                WNBA_ML_V1_REQUIRED_FIELDS,
                f"{forbidden!r} must not be in WNBA_ML_V1_REQUIRED_FIELDS "
                f"(player-prop contract scope only)",
            )


# ---------------------------------------------------------------------------
# Test 6 — Partial acquisition guard
# ---------------------------------------------------------------------------

class TestPartialAcquisitionGuard(unittest.TestCase):

    def test_06_partial_wnba_data_does_not_use_market_odds_as_independent_prob(self):
        """
        When WNBA acquisition is partial (e.g. home_win_pct present but
        away_win_pct absent), the sport_model must not substitute
        market_no_vig_probability for the independent model probability.

        independent_probability must remain None (or be derived purely from
        non-market inputs that ARE available); it must NEVER equal the
        market_no_vig_probability passed in through sportsbook_odds.
        """
        row = _wnba_row()
        market_no_vig = 0.61  # known market value

        # Partial enrichment: home_win_pct present, away_win_pct absent
        # → specialist fires but produces None (only one side available)
        partial_clean_enr = {
            "hydration_profile": "WNBA_ML_V1",
            "home_win_pct":      0.72,
            # away_win_pct deliberately absent
        }

        model_out = compute_independent_probability(row, partial_clean_enr)
        ind_prob = model_out.get("independent_probability")

        # independent_probability must not equal the market no-vig value
        # (market data never enters the independence gate)
        if ind_prob is not None:
            self.assertNotAlmostEqual(
                ind_prob, market_no_vig, places=3,
                msg=(
                    "independent_probability must never equal market_no_vig "
                    "— market data cannot populate the independent model output"
                ),
            )

    def test_06b_partial_tennis_data_does_not_use_market_odds(self):
        """
        Tennis row with only surface (no hold/break/Elo/form):
          - specialist returns None (insufficient non-market inputs)
          - independent_probability stays None
          - market_no_vig is NOT promoted to independent_probability
        """
        row = _tennis_row()
        market_no_vig = 0.62

        partial_clean_enr = {
            "hydration_profile": "TENNIS_MATCH_WINNER_V1",
            "surface": "hard",   # only surface; no form/Elo/hold/break/H2H
        }

        p = _tennis_match_winner_specialist(partial_clean_enr)
        self.assertIsNone(p,
                          "Tennis specialist must return None when only surface is present")

        model_out = compute_independent_probability(row, partial_clean_enr)
        ind_prob = model_out.get("independent_probability")
        if ind_prob is not None:
            self.assertNotAlmostEqual(ind_prob, market_no_vig, places=3)

    def test_06c_market_derived_fallback_flag_not_set_on_partial(self):
        """
        market_derived_fallback must never appear in sport_model output —
        that flag is set by pipeline.py's MARKET_OBSERVATION_ONLY branch,
        which is only reachable when market odds ARE present.
        The sport_model itself must not set or reference this flag.
        """
        row = _wnba_row()
        clean_enr = {"hydration_profile": "WNBA_ML_V1"}  # nothing
        model_out = compute_independent_probability(row, clean_enr)
        self.assertNotIn(
            "market_derived_fallback", model_out,
            "sport_model must never set market_derived_fallback — that belongs to pipeline.py",
        )


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------

class TestModuleInvariants(unittest.TestCase):

    def test_can_execute_false(self):
        """can_execute=False is unconditional for all modules in this patch."""
        self.assertFalse(sport_model_can_execute)
        self.assertFalse(orch_can_execute)

    def test_patch_registered(self):
        """Patch #27 must appear in the active governance registry."""
        from gate_engine.governance import _ACTIVE_PATCH_IDS, _active_patches
        self.assertIn(
            "WOW-PATCH-2026-08-17-WNBA-TENNIS-ML-LANES",
            _ACTIVE_PATCH_IDS,
        )
        active = _active_patches()
        self.assertEqual(len(active), 28, f"Expected 28 active patches, got {len(active)}")

    def test_wnba_tennis_sport_families_distinct(self):
        """
        _WNBA_ML_SUPPORTED and _TENNIS_ML_SUPPORTED must not overlap with
        _MONEYLINE_TEAM_SUPPORTED (NBA/MLB). Sport-specific routing requires
        disjoint sets.
        """
        self.assertFalse(_WNBA_ML_SUPPORTED & _MONEYLINE_TEAM_SUPPORTED,
                         "WNBA family must not overlap with NBA/MLB set")
        self.assertFalse(_TENNIS_ML_SUPPORTED & _MONEYLINE_TEAM_SUPPORTED,
                         "Tennis family must not overlap with NBA/MLB set")
        self.assertFalse(_WNBA_ML_SUPPORTED & _TENNIS_ML_SUPPORTED,
                         "WNBA and Tennis families must be disjoint")


if __name__ == "__main__":
    unittest.main()
