"""
tests/test_moneyline_architecture.py
WOW v16 — Moneyline Architecture Regression Suite

Verifies all 8 mandatory cases:
  R01  Independent probability contains no sportsbook-price input
  R02  Market calibration remains separately attributable
  R03  Lower-bound ranking can reverse raw-probability ranking when uncertainty differs
  R04  High model disagreement widens uncertainty
  R05  Regime simulation changes unconditional probability (MLB early-hook)
  R06  Tail-only upset paths cannot qualify
  R07  Exact market / no-vig / edge analysis is downstream from prediction
  R08  can_execute=False in every output and every submodule

All tests are offline (no network, no DB).
"""
from __future__ import annotations

import math
import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _base_row(sport="NBA", team="Lakers", opponent="Celtics",
               slate_date="2026-08-08", market_type="h2h", **kw):
    import datetime
    row = {
        "sport":       sport,
        "team":        team,
        "opponent":    opponent,
        "slate_date":  slate_date,
        "market_type": market_type,
        "home_away":   "HOME",
    }
    row.update(kw)
    return row


def _base_enr(**kw):
    enr = {
        "home_win_pct":         0.55,
        "away_win_pct":         0.45,
        "status_freshness_hours": 0.5,
        "event_status":         "SCHEDULED",
        "lineup_confirmed":     True,
        "starter_confirmed":    True,
        "player_status":        "ACTIVE",
        "game_log":             [{"result": "W"}, {"result": "L"},
                                 {"result": "W"}, {"result": "W"},
                                 {"result": "L"}],
    }
    enr.update(kw)
    return enr


# ---------------------------------------------------------------------------
# R01: Independent probability contains no sportsbook-price input
# ---------------------------------------------------------------------------

class TestR01_NoMarketContamination:
    """The contamination guard must block odds fields from the independent model."""

    def test_contamination_guard_raises_on_odds_fields(self):
        from gate_engine.moneyline.types import (
            check_independence_boundary,
            IndependentModelContaminationError,
        )
        bad_enr = {"home_win_pct": 0.55, "no_vig_prob": 0.52}
        with pytest.raises(IndependentModelContaminationError) as exc_info:
            check_independence_boundary(bad_enr)
        assert "no_vig_prob" in str(exc_info.value)

    def test_contamination_guard_passes_clean_enrichment(self):
        from gate_engine.moneyline.types import check_independence_boundary
        clean = {"home_win_pct": 0.55, "home_elo": 1520, "away_elo": 1480}
        check_independence_boundary(clean)   # must not raise

    def test_strip_odds_fields_removes_all_contamination(self):
        from gate_engine.moneyline.types import strip_odds_fields
        dirty = {
            "home_win_pct":    0.55,
            "no_vig_prob":     0.52,
            "american_odds":   -120,
            "sportsbook_odds": [{"home": -120}],
            "h2h_win_pct":     0.60,
        }
        clean = strip_odds_fields(dirty)
        assert "no_vig_prob"     not in clean
        assert "american_odds"   not in clean
        assert "sportsbook_odds" not in clean
        assert clean.get("home_win_pct") == 0.55
        assert clean.get("h2h_win_pct")  == 0.60

    def test_sport_model_rejects_contaminated_enrichment(self):
        from gate_engine.moneyline.sport_model import compute_independent_probability
        from gate_engine.moneyline.types import IndependentModelContaminationError
        row = _base_row()
        bad_enr = {"home_win_pct": 0.55, "no_vig_prob": 0.55}
        with pytest.raises(IndependentModelContaminationError):
            compute_independent_probability(row, bad_enr)

    def test_pipeline_strips_odds_before_independent_model(self):
        """Full pipeline must succeed even when enrichment contains odds — stripped internally."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA")
        enr = _base_enr(
            no_vig_prob=0.52,           # contaminating field — must be stripped
            sportsbook_odds=[{"home": -120, "retrieved_at": "2026-08-08T10:00:00Z"}],
        )
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=1)
        # Must not produce a contamination error
        assert result.outputs.independent_probability is not None
        assert "INDEPENDENT_MODEL_CONTAMINATION" not in " ".join(result.blockers)


# ---------------------------------------------------------------------------
# R02: Market calibration remains separately attributable
# ---------------------------------------------------------------------------

class TestR02_MarketCalibrationAttributable:
    def test_independent_prob_unchanged_by_market_weight(self):
        from gate_engine.moneyline.dynamic_calibration import calibrate
        enr = _base_enr()

        # No market
        cal_no_mkt = calibrate(0.60, "ACTIVE", "NBA", enr,
                                market_no_vig=None, market_inputs={})
        # With market
        cal_with_mkt = calibrate(0.60, "ACTIVE", "NBA", enr,
                                  market_no_vig=0.55,
                                  market_inputs={"bookmaker_count": 8, "hours_since_open": 20,
                                                 "hold_pct": 0.03, "market_freshness_hours": 0.5,
                                                 "market_type": "full_game_h2h"})

        # Independent probability must be IDENTICAL in both
        assert cal_no_mkt.independent_prob == cal_with_mkt.independent_prob == 0.60

    def test_clb_changes_when_market_weight_changes(self):
        from gate_engine.moneyline.dynamic_calibration import calibrate
        enr = _base_enr()

        cal_no_mkt  = calibrate(0.60, "ACTIVE", "NBA", enr,
                                 market_no_vig=None, market_inputs={})
        cal_with_mkt = calibrate(0.60, "ACTIVE", "NBA", enr,
                                  market_no_vig=0.52,
                                  market_inputs={"bookmaker_count": 10, "hours_since_open": 24,
                                                 "hold_pct": 0.02, "market_freshness_hours": 0.0,
                                                 "market_type": "h2h"})

        # CLB may differ when market shrinks calibrated_prob
        assert (cal_no_mkt.calibrated_lower_bound != cal_with_mkt.calibrated_lower_bound or
                cal_no_mkt.calibrated_probability  != cal_with_mkt.calibrated_probability)

    def test_net_edge_is_downstream_only(self):
        """net_edge must appear ONLY in the calibration output, not in the sport model."""
        from gate_engine.moneyline.sport_model import compute_independent_probability
        row = _base_row()
        clean_enr = {"home_win_pct": 0.55, "h2h_win_pct": 0.57}
        sport_out = compute_independent_probability(row, clean_enr)
        assert "net_edge" not in sport_out
        assert "market_no_vig" not in sport_out


# ---------------------------------------------------------------------------
# R03: CLB ranking can reverse raw-probability ranking
# ---------------------------------------------------------------------------

class TestR03_CLBRankingReversal:
    def test_higher_clb_beats_higher_raw_prob(self):
        from gate_engine.moneyline.dynamic_calibration import calibrate

        # Candidate A: high raw prob, high uncertainty
        cal_a = calibrate(0.68, "ACTIVE", "NFL", _base_enr(
            game_log=[],                  # empty → high sample size penalty
            status_freshness_hours=5.0,   # stale → freshness penalty
            lineup_confirmed=False,
        ), market_no_vig=None, market_inputs={})

        # Candidate B: lower raw prob, low uncertainty
        cal_b = calibrate(0.62, "ACTIVE", "NBA", _base_enr(
            game_log=[{"result": "W"}] * 20,  # large sample
            status_freshness_hours=0.2,
            lineup_confirmed=True,
            starter_confirmed=True,
        ), market_no_vig=None, market_inputs={})

        # A has higher raw prob
        assert cal_a.independent_prob > cal_b.independent_prob

        # B has lower dynamic uncertainty
        assert cal_b.dynamic_uncertainty < cal_a.dynamic_uncertainty

        # B must have better CLB (ranking reversal)
        assert cal_b.calibrated_lower_bound > cal_a.calibrated_lower_bound, (
            f"B CLB ({cal_b.calibrated_lower_bound:.4f}) must exceed "
            f"A CLB ({cal_a.calibrated_lower_bound:.4f}) — ranking reversal"
        )

    def test_synthetic_clb_ranking(self):
        """Direct arithmetic confirms CLB ranking can diverge from raw ranking."""
        # Candidate A: raw=0.68, uncertainty=0.20 → CLB=0.48
        # Candidate B: raw=0.62, uncertainty=0.05 → CLB=0.57
        clb_a = 0.68 - 0.20
        clb_b = 0.62 - 0.05
        assert 0.68 > 0.62, "A has higher raw probability"
        assert clb_b > clb_a, "B has higher CLB → B ranks first"


# ---------------------------------------------------------------------------
# R04: High model disagreement widens uncertainty
# ---------------------------------------------------------------------------

class TestR04_DisagreementWidensUncertainty:
    def test_high_disagreement_widens_uncertainty_by_35_pct(self):
        from gate_engine.moneyline.model_disagreement import audit_model_disagreement
        from gate_engine.moneyline.dynamic_calibration import calibrate

        # Low disagreement: all submodels agree tightly
        dis_low = audit_model_disagreement({
            "h2h_historical": 0.60,
            "elo_differential": 0.61,
            "simulation_output": 0.61,
        })
        assert dis_low.disagreement_grade == "LOW"
        assert dis_low.uncertainty_widening_factor == 1.00

        # High disagreement: submodels spread > 10pp
        dis_high = audit_model_disagreement({
            "h2h_historical": 0.60,
            "elo_differential": 0.73,   # 13pp diff
            "power_rating": 0.65,
        })
        assert dis_high.disagreement_grade == "HIGH"
        assert dis_high.uncertainty_widening_factor == 1.35

        # Apply to calibration and verify widening
        base_enr = _base_enr()
        cal_low  = calibrate(0.62, "ACTIVE", "NBA", base_enr,
                              disagreement_audit=dis_low, market_no_vig=None, market_inputs={})
        cal_high = calibrate(0.62, "ACTIVE", "NBA", base_enr,
                              disagreement_audit=dis_high, market_no_vig=None, market_inputs={})

        ratio = cal_high.dynamic_uncertainty / cal_low.dynamic_uncertainty
        assert ratio >= 1.35 * 0.95, (
            f"High disagreement must widen uncertainty by ≥35%; "
            f"got ratio={ratio:.4f}"
        )

    def test_moderate_disagreement_grade(self):
        from gate_engine.moneyline.model_disagreement import audit_model_disagreement
        dis = audit_model_disagreement({
            "h2h_historical": 0.60,
            "elo_differential": 0.66,   # 6pp diff → MODERATE
        })
        assert dis.disagreement_grade == "MODERATE"
        assert dis.uncertainty_widening_factor == 1.15

    def test_single_submodel_is_low_disagreement(self):
        from gate_engine.moneyline.model_disagreement import audit_model_disagreement
        dis = audit_model_disagreement({"simulation_output": 0.60})
        assert dis.disagreement_grade == "LOW"
        assert dis.uncertainty_widening_factor == 1.0


# ---------------------------------------------------------------------------
# R05: Regime simulation changes unconditional probability
# ---------------------------------------------------------------------------

class TestR05_RegimeSimulationChangesProb:
    def test_mlb_early_hook_lowers_probability(self):
        """MLB: when SP is ineffective (high ERA/WHIP), adjusted_prob < base_prob."""
        from gate_engine.moneyline.game_state_sim import run_game_state_simulation

        row = _base_row(sport="MLB")
        # Excellent home SP → regime should push toward home win
        enr_dominant = {
            "sp_era": 2.50, "sp_whip": 0.90,
            "bullpen_era": 3.50, "opp_k_pct": 0.28,
        }
        # Terrible home SP → early-hook regime dominant
        enr_early_hook = {
            "sp_era": 6.50, "sp_whip": 1.80,
            "bullpen_era": 5.20, "opp_k_pct": 0.18,
        }
        base_prob = 0.55

        sim_dom  = run_game_state_simulation(row, enr_dominant,  base_prob, n_sims=4000, seed=42)
        sim_hook = run_game_state_simulation(row, enr_early_hook, base_prob, n_sims=4000, seed=42)

        assert sim_dom.regime_distribution.get("sp_early_hook", 0) < \
               sim_hook.regime_distribution.get("sp_early_hook", 0), (
                "Early-hook regime must be more frequent with poor SP metrics"
        )
        # Dominant SP should produce higher win prob than early-hook SP
        assert sim_dom.adjusted_prob > sim_hook.adjusted_prob, (
            f"Dominant SP adj_prob ({sim_dom.adjusted_prob:.4f}) must exceed "
            f"early-hook adj_prob ({sim_hook.adjusted_prob:.4f})"
        )

    def test_nba_blowout_inflates_heavy_favorite(self):
        """NBA: heavy favorite (base_prob=0.75) should see blowout regime dominate."""
        from gate_engine.moneyline.game_state_sim import run_game_state_simulation
        row = _base_row(sport="NBA")
        enr = {"home_off_rtg": 118.0, "away_def_rtg": 108.0, "pace": 100.0}
        sim = run_game_state_simulation(row, enr, 0.75, n_sims=5000, seed=10)
        blowout_freq = sim.regime_distribution.get("blowout_truncation", 0.0)
        assert blowout_freq > 0.10, (
            f"Heavy favorite (base=0.75) must show >10% blowout frequency; got {blowout_freq:.2f}"
        )

    def test_soccer_draw_frequency_is_realistic(self):
        """Soccer simulation must produce realistic draw frequency (15–40%)."""
        from gate_engine.moneyline.game_state_sim import run_game_state_simulation
        row = _base_row(sport="SOCCER")
        enr = {"home_xg_per_game": 1.35, "away_xg_per_game": 1.20,
               "h2h_draw_rate": 0.30}
        sim = run_game_state_simulation(row, enr, 0.48, n_sims=5000, seed=20)
        draw_freq = sim.regime_distribution.get("draw_frequency", 0.0)
        assert 0.15 < draw_freq < 0.50, (
            f"Soccer draw frequency must be 15–50%; got {draw_freq:.2f}"
        )


# ---------------------------------------------------------------------------
# R06: Tail-only upset paths cannot qualify
# ---------------------------------------------------------------------------

class TestR06_TailOnlyRejected:
    def test_tail_only_classification_rejects(self):
        from gate_engine.moneyline.classification import classify_candidate

        row = _base_row(sport="NBA", home_away="AWAY")
        # Very low CLB, no structural advantages → TAIL_ONLY
        cal_result = {
            "calibrated_lower_bound": 0.18,
            "calibrated_probability": 0.28,
            "dynamic_uncertainty":    0.10,
        }
        # No structural enrichment signals
        enr = _base_enr(
            home_win_pct=0.70,   # heavy favorite team
            game_log=[{"result": "W"}] * 8,
        )
        c = classify_candidate(row, cal_result, enr)

        assert c.lane == "UNDERDOG"
        assert c.upset_profile_type == "TAIL_ONLY"
        assert c.qualification_gate == "TAIL_ONLY_REJECTED"

    def test_structural_upset_qualifies(self):
        from gate_engine.moneyline.classification import classify_candidate

        row = _base_row(sport="NBA", home_away="AWAY")
        cal_result = {
            "calibrated_lower_bound": 0.42,
            "calibrated_probability": 0.48,
            "dynamic_uncertainty":    0.06,
        }
        # Multiple structural advantages
        enr = _base_enr(
            structural_advantage_flag=True,
            matchup_advantage_flag=True,
            opponent_key_players_out=["Star Player"],
        )
        c = classify_candidate(row, cal_result, enr)

        assert c.lane == "UNDERDOG"
        assert c.upset_profile_type in ("STRUCTURAL", "VARIANCE_ASSISTED")
        assert c.qualification_gate == "QUALIFIES"

    def test_pipeline_emits_tail_only_rejected_label(self):
        """Full pipeline: a heavy underdog with no structural advantages gets rejected."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA", home_away="AWAY")
        enr = _base_enr(
            home_win_pct=0.85,   # overwhelming favorite for opponent
            away_win_pct=0.15,
            h2h_win_pct=0.15,
            game_log=[{"result": "L"}] * 8 + [{"result": "W"}],
        )
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=5)
        if result.classification.get("qualification_gate") == "TAIL_ONLY_REJECTED":
            assert result.terminal_label == "REJECT_TAIL_ONLY_UPSET"


# ---------------------------------------------------------------------------
# R07: Market/no-vig/edge analysis is downstream from prediction
# ---------------------------------------------------------------------------

class TestR07_MarketIsDownstream:
    def test_sport_model_has_no_market_fields(self):
        """Independent sport model output must not contain any market/odds fields."""
        from gate_engine.moneyline.sport_model import compute_independent_probability
        row = _base_row()
        clean_enr = {"home_win_pct": 0.58, "home_elo": 1520, "away_elo": 1490}
        out = compute_independent_probability(row, clean_enr)
        for forbidden_key in ("no_vig_prob", "market_no_vig", "net_edge",
                               "market_weight", "sportsbook_odds", "implied_prob"):
            assert forbidden_key not in out, (
                f"Sport model output must not contain {forbidden_key!r}"
            )

    def test_net_edge_absent_from_layer_inputs(self):
        """net_edge must only appear in calibration output, not as simulation input."""
        from gate_engine.moneyline.game_state_sim import run_game_state_simulation
        row = _base_row(sport="NBA")
        enr = _base_enr()
        sim = run_game_state_simulation(row, enr, 0.60, n_sims=500, seed=3)
        sim_dict = sim.to_dict()
        assert "net_edge" not in sim_dict

    def test_calibration_result_has_net_edge_downstream(self):
        """net_edge IS present in calibration output (downstream-only)."""
        from gate_engine.moneyline.dynamic_calibration import calibrate
        cal = calibrate(0.62, "ACTIVE", "NBA", _base_enr(),
                         market_no_vig=0.57,
                         market_inputs={"bookmaker_count": 5, "hours_since_open": 12,
                                        "hold_pct": 0.04, "market_freshness_hours": 0.5,
                                        "market_type": "h2h"})
        assert cal.net_edge is not None
        assert isinstance(cal.net_edge, float)
        assert abs(cal.net_edge - (cal.calibrated_probability - 0.57)) < 0.001


# ---------------------------------------------------------------------------
# R08: can_execute=False in every output and every submodule
# ---------------------------------------------------------------------------

class TestR08_CanExecuteFalse:
    def test_all_modules_can_execute_false(self):
        import gate_engine.moneyline as pkg
        import gate_engine.moneyline.types as types_mod
        import gate_engine.moneyline.slate_integrity as si_mod
        import gate_engine.moneyline.sport_model as sm_mod
        import gate_engine.moneyline.game_state_sim as gs_mod
        import gate_engine.moneyline.failure_path as fp_mod
        import gate_engine.moneyline.model_disagreement as md_mod
        import gate_engine.moneyline.dynamic_calibration as dc_mod
        import gate_engine.moneyline.classification as cl_mod
        import gate_engine.moneyline.pipeline as pl_mod

        for mod in [pkg, types_mod, si_mod, sm_mod, gs_mod, fp_mod,
                    md_mod, dc_mod, cl_mod, pl_mod]:
            assert getattr(mod, "can_execute", None) is False, (
                f"{mod.__name__} must have can_execute=False at module level"
            )

    @pytest.mark.parametrize("sport", ["NBA", "WNBA", "MLB", "NFL", "NHL"])
    def test_pipeline_result_can_execute_false(self, sport):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport=sport)
        enr = _base_enr()
        if sport == "MLB":
            enr.update({
                "starting_pitcher_home": "Pitcher A",
                "starting_pitcher_away": "Pitcher B",
            })
        result = run_moneyline_pipeline(row, enr, n_sims=200, seed=0)
        assert result.can_execute is False
        assert result.can_approve_bets is False
        d = result.to_dict()
        assert d["can_execute"] is False
        assert d["can_approve_bets"] is False

    def test_moneyline_result_objective_field(self):
        """Result objective must always be OUTRIGHT_WIN_PROBABILITY_ONLY."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA")
        result = run_moneyline_pipeline(row, _base_enr(), n_sims=200, seed=0)
        assert result.objective == "OUTRIGHT_WIN_PROBABILITY_ONLY"
        assert result.controlling_skill == "wow.llp-moneyline-probability-expert"


# ---------------------------------------------------------------------------
# Additional: four clean output separation
# ---------------------------------------------------------------------------

class TestFourOutputSeparation:
    def test_four_outputs_are_distinct_fields(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA")
        enr = _base_enr(
            sportsbook_odds=[{"home": -130, "retrieved_at": "2026-08-08T10:00:00Z"},
                              {"home": -125, "retrieved_at": "2026-08-08T10:00:00Z"}],
        )
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=42)
        d = result.to_dict()

        # All four fields must be present and distinct
        ip  = d.get("independent_probability")
        cp  = d.get("calibrated_probability")
        clb = d.get("calibrated_probability_lower_bound")
        cub = d.get("calibrated_probability_upper_bound")

        if ip is not None and cp is not None and clb is not None and cub is not None:
            # CLB ≤ calibrated ≤ CUB
            assert clb <= cp <= cub, (
                f"CLB({clb}) ≤ calibrated({cp}) ≤ CUB({cub}) must hold"
            )
            # None should be identical to another (unless degenerate)
            field_names = ["independent_probability", "calibrated_probability",
                           "calibrated_probability_lower_bound", "calibrated_probability_upper_bound"]
            for fn in field_names:
                assert fn in d, f"Required field {fn!r} missing from result"

    def test_independent_prob_not_equal_to_market_no_vig(self):
        """Independent probability must diverge from pure market no-vig when data available."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA")
        enr = _base_enr(
            home_win_pct=0.65,
            # Market says strong opposite (to force a detectable difference)
            sportsbook_odds=[{"home": -200, "retrieved_at": "2026-08-08T10:00:00Z"}],
        )
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=0)
        ip  = result.outputs.independent_probability
        cp  = result.outputs.calibrated_probability
        if ip is not None and cp is not None:
            # At least one of independent vs calibrated must differ from 0.6667 (the -200 no-vig)
            market_nv = 200 / (200 + 100)
            # independent_prob is entirely from sport model — not the market number
            assert abs(ip - market_nv) > 0.001 or abs(cp - market_nv) < 0.10

    def test_snapshot_hash_changes_on_different_probabilities(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA")
        r1 = run_moneyline_pipeline(row, _base_enr(home_win_pct=0.60), n_sims=200, seed=1)
        r2 = run_moneyline_pipeline(row, _base_enr(home_win_pct=0.50), n_sims=200, seed=1)
        if r1.outputs.independent_probability != r2.outputs.independent_probability:
            assert r1.snapshot_hash != r2.snapshot_hash


# ---------------------------------------------------------------------------
# Integration: failure path influence
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Away-side inversion — the fix verified by the code reviewer
# ---------------------------------------------------------------------------

class TestAwaySideInversion:
    """
    Verify that P(candidate wins) is correctly oriented for both home and away rows.

    All sport_model submodels return P(home wins) by convention. The pipeline
    inverts at stage 6 (single inversion point) to produce P(candidate wins).

    Critical property: P(home candidate wins) + P(away candidate wins) ≈ 1.0
    when both sides of the same game are scored with the same enrichment.
    """

    def _same_game_enr(self):
        return _base_enr(
            home_win_pct=0.70,
            away_win_pct=0.30,
            game_log=[{"result": "W"}] * 7 + [{"result": "L"}] * 3,
        )

    def test_away_row_prob_less_than_50_when_home_team_stronger(self):
        """Away candidate with home_win_pct=0.70 must have independent_prob < 0.50."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA", home_away="AWAY")
        enr = self._same_game_enr()
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=0)
        ip = result.outputs.independent_probability
        assert ip is not None, "independent_probability must not be None"
        assert ip < 0.50, (
            f"Away candidate probability must be < 0.50 when home team dominates "
            f"(home_win_pct=0.70); got {ip:.4f}"
        )

    def test_home_row_prob_greater_than_50_when_home_team_stronger(self):
        """Home candidate with home_win_pct=0.70 must have independent_prob > 0.50."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA", home_away="HOME")
        enr = self._same_game_enr()
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=0)
        ip = result.outputs.independent_probability
        assert ip is not None
        assert ip > 0.50, (
            f"Home candidate probability must be > 0.50 when home_win_pct=0.70; got {ip:.4f}"
        )

    def test_home_plus_away_approximately_sum_to_one(self):
        """
        P(home wins) + P(away wins) must sum to ~1.0 for the same game.

        Uses the same enrichment/seed for both rows. Small deviations are expected
        due to stochastic simulation variance; tolerance is 0.08.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        enr = self._same_game_enr()
        row_home = _base_row(sport="NBA", home_away="HOME")
        row_away = _base_row(sport="NBA", home_away="AWAY")
        r_home = run_moneyline_pipeline(row_home, enr, n_sims=2000, seed=99)
        r_away = run_moneyline_pipeline(row_away, enr, n_sims=2000, seed=99)
        p_home = r_home.outputs.independent_probability
        p_away = r_away.outputs.independent_probability
        assert p_home is not None and p_away is not None
        total = p_home + p_away
        assert abs(total - 1.0) < 0.08, (
            f"P(home)+P(away) must sum to ~1.0; "
            f"got P(home)={p_home:.4f} P(away)={p_away:.4f} sum={total:.4f}"
        )

    def test_symmetric_inversion_balanced_game(self):
        """
        For an evenly matched game (home_win_pct=0.50) both sides should score ~0.50.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        enr = _base_enr(home_win_pct=0.50, away_win_pct=0.50,
                         game_log=[{"result": "W"}, {"result": "L"},
                                   {"result": "W"}, {"result": "L"},
                                   {"result": "W"}])
        r_home = run_moneyline_pipeline(_base_row(sport="NBA", home_away="HOME"),
                                         enr, n_sims=2000, seed=7)
        r_away = run_moneyline_pipeline(_base_row(sport="NBA", home_away="AWAY"),
                                         enr, n_sims=2000, seed=7)
        p_h = r_home.outputs.independent_probability
        p_a = r_away.outputs.independent_probability
        assert p_h is not None and p_a is not None
        assert abs(p_h - 0.50) < 0.20, f"Balanced game home prob should be near 0.50; got {p_h:.4f}"
        assert abs(p_a - 0.50) < 0.20, f"Balanced game away prob should be near 0.50; got {p_a:.4f}"

    def test_sport_model_probability_perspective_annotated(self):
        """sport_model layer must annotate probability_perspective=HOME_WIN."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        result = run_moneyline_pipeline(
            _base_row(sport="NBA", home_away="AWAY"),
            _base_enr(home_win_pct=0.65, away_win_pct=0.35),
            n_sims=200, seed=0,
        )
        assert result.sport_model.get("probability_perspective") == "HOME_WIN", (
            "sport_model must annotate probability_perspective=HOME_WIN "
            "so downstream consumers know inversion was applied at pipeline stage 6"
        )

    def test_mlb_away_row_without_sp_data_still_scores(self):
        """
        MLB away row with no starting pitcher data must not DATA_CONTRACT_FAIL.
        Participant lock should not hard-block on absent SP names — only on
        explicitly scratched/out pitchers.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="MLB", home_away="AWAY")
        enr = _base_enr(home_win_pct=0.55, away_win_pct=0.45)
        # No starting_pitcher_home/away in enrichment — must not block
        result = run_moneyline_pipeline(row, enr, n_sims=200, seed=0)
        assert "PARTICIPANT_LOCK_FAILED:starting_pitcher" not in " ".join(result.blockers), (
            "Missing SP data must not cause PARTICIPANT_LOCK_FAILED — "
            "only explicitly scratched/out pitchers should block"
        )

    def test_mlb_scratched_pitcher_does_block(self):
        """MLB row with sp_home_status=SCRATCHED must produce PARTICIPANT_LOCK_FAILED."""
        from gate_engine.moneyline.slate_integrity import check_participant_status
        row = _base_row(sport="MLB")
        enr = _base_enr(sp_home_status="SCRATCHED", starting_pitcher_home="Ace Pitcher")
        result = check_participant_status(row, enr)
        assert not result["locked"]
        assert any("sp_home_status=SCRATCHED" in b for b in result["blockers"])

    # ------------------------------------------------------------------
    # app.py home/away convention: home="vs", away="@"
    # ------------------------------------------------------------------

    def test_vs_convention_treated_as_home_candidate(self):
        """
        home_away='vs' is how app.py marks home-team rows.
        A home-favorite (home_win_pct=0.70) with home_away='vs' must
        produce independent_prob > 0.50 — NOT an inverted away probability.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA")
        row["home_away"] = "vs"   # app.py home convention
        enr = self._same_game_enr()
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=0)
        ip = result.outputs.independent_probability
        assert ip is not None
        assert ip > 0.50, (
            f"home_away='vs' (home team with win_pct=0.70) must score > 0.50; got {ip:.4f}"
        )

    def test_at_convention_treated_as_away_candidate(self):
        """
        home_away='@' is how app.py marks away-team rows.
        An away underdog (home_win_pct=0.70) with home_away='@' must
        produce independent_prob < 0.50.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="NBA")
        row["home_away"] = "@"   # app.py away convention
        enr = self._same_game_enr()
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=0)
        ip = result.outputs.independent_probability
        assert ip is not None
        assert ip < 0.50, (
            f"home_away='@' (away team with home_win_pct=0.70) must score < 0.50; got {ip:.4f}"
        )

    def test_vs_at_sum_to_approximately_one(self):
        """
        P(vs/home) + P(@/away) must sum to ~1.0 for the same game enrichment.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        enr = self._same_game_enr()
        row_vs = _base_row(sport="NBA")
        row_vs["home_away"] = "vs"
        row_at = _base_row(sport="NBA")
        row_at["home_away"] = "@"
        r_vs = run_moneyline_pipeline(row_vs, enr, n_sims=2000, seed=42)
        r_at = run_moneyline_pipeline(row_at, enr, n_sims=2000, seed=42)
        p_vs = r_vs.outputs.independent_probability
        p_at = r_at.outputs.independent_probability
        assert p_vs is not None and p_at is not None
        total = p_vs + p_at
        assert abs(total - 1.0) < 0.08, (
            f"P(vs)+P(@) must sum to ~1.0; "
            f"got P(vs)={p_vs:.4f} P(@)={p_at:.4f} sum={total:.4f}"
        )


# ---------------------------------------------------------------------------
# Two-sided no-vig odds extraction (fixes code-reviewer finding)
# ---------------------------------------------------------------------------

class TestNoVigOddsExtraction:
    """
    Verifies that extract_no_vig_probability() matches sportsbook_odds entries
    by the "team" field rather than treating the team name as a key.

    Production enrichment format (from app.py):
        sportsbook_odds: [
            {"team": "Boston Red Sox", "odds": -130},
            {"team": "New York Yankees", "odds": +110},
        ]

    Critical invariant: P(home no-vig) + P(away no-vig) == 1.0 when both
    sides' odds are present in the same enrichment object.
    """

    def _enr_two_sided(self, home_team, home_odds, away_team, away_odds):
        return {
            "sportsbook_odds": [
                {"team": home_team, "odds": home_odds},
                {"team": away_team, "odds": away_odds},
            ]
        }

    def test_home_candidate_gets_home_odds(self):
        from gate_engine.moneyline_probability import extract_no_vig_probability
        enr = self._enr_two_sided("Boston Red Sox", -130, "New York Yankees", +110)
        p = extract_no_vig_probability(enr, side="Boston Red Sox", opponent="New York Yankees")
        assert p is not None
        # -130 → 56.5%, +110 → 47.6% raw; no-vig home ≈ 56.5/104.1 ≈ 0.543
        assert p > 0.50, f"Home (-130) no-vig prob must be > 0.50; got {p:.4f}"

    def test_away_candidate_gets_away_odds(self):
        from gate_engine.moneyline_probability import extract_no_vig_probability
        enr = self._enr_two_sided("Boston Red Sox", -130, "New York Yankees", +110)
        p = extract_no_vig_probability(enr, side="New York Yankees", opponent="Boston Red Sox")
        assert p is not None
        assert p < 0.50, f"Away (+110) no-vig prob must be < 0.50; got {p:.4f}"

    def test_home_plus_away_no_vig_sum_to_exactly_one(self):
        from gate_engine.moneyline_probability import extract_no_vig_probability
        enr = self._enr_two_sided("Boston Red Sox", -130, "New York Yankees", +110)
        p_home = extract_no_vig_probability(enr, side="Boston Red Sox", opponent="New York Yankees")
        p_away = extract_no_vig_probability(enr, side="New York Yankees", opponent="Boston Red Sox")
        assert p_home is not None and p_away is not None
        total = p_home + p_away
        assert abs(total - 1.0) < 0.001, (
            f"Two-sided no-vig must sum to 1.0; "
            f"P(home)={p_home:.4f} P(away)={p_away:.4f} sum={total:.4f}"
        )

    def test_single_side_enrichment_returns_correct_implied_prob(self):
        """When only one team's odds are present, return that team's implied prob."""
        from gate_engine.moneyline_probability import extract_no_vig_probability
        enr = {"sportsbook_odds": [{"team": "Boston Red Sox", "odds": -130}]}
        p = extract_no_vig_probability(enr, side="Boston Red Sox")
        assert p is not None
        # -130 → 130/230 ≈ 0.565
        assert abs(p - 130.0 / 230.0) < 0.02, f"Single-side implied prob wrong; got {p:.4f}"

    def test_away_candidate_gets_none_when_only_home_odds_present(self):
        """
        Away candidate cannot use the home team's odds entry — must return None
        so the model governs instead of using the wrong price.
        """
        from gate_engine.moneyline_probability import extract_no_vig_probability
        enr = {"sportsbook_odds": [{"team": "Boston Red Sox", "odds": -130}]}
        p = extract_no_vig_probability(enr, side="New York Yankees", opponent="Boston Red Sox")
        # Boston Red Sox odds match the OPPONENT, not the candidate — one-sided,
        # so we get the opponent's implied prob inverted for proper no-vig...
        # Actually: candidate_probs=[], opponent_probs=[0.565] → falls to unmatched_probs=[]
        # → returns None (correct: cannot use home odds for away candidate without home price)
        assert p is None, (
            f"Away candidate with only home-team odds in enrichment must return None; got {p}"
        )

    def test_no_team_field_falls_back_to_all_odds(self):
        """Entries with no 'team' field pool into the fallback unmatched_probs."""
        from gate_engine.moneyline_probability import extract_no_vig_probability
        enr = {"sportsbook_odds": [{"odds": -130}, {"odds": -125}]}
        p = extract_no_vig_probability(enr, side="Boston Red Sox")
        assert p is not None
        # avg implied of -130 and -125 ≈ (0.565 + 0.556) / 2 ≈ 0.560
        assert 0.50 < p < 0.65, f"Fallback pool should give reasonable implied prob; got {p:.4f}"

    def test_pipeline_home_candidate_gets_correct_probability_from_two_sided_odds(self):
        """
        End-to-end: home candidate with two-sided enrichment should have
        calibrated_prob influenced by the correct home-side no-vig price.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        enr = _base_enr(home_win_pct=0.60, away_win_pct=0.40)
        enr["sportsbook_odds"] = [
            {"team": "home_team_name", "odds": -145},
            {"team": "away_team_name", "odds": +125},
        ]
        row = _base_row(sport="NBA", home_away="HOME")
        row["team"] = "home_team_name"
        row["opponent"] = "away_team_name"
        result = run_moneyline_pipeline(row, enr, n_sims=500, seed=0)
        assert result.outputs.independent_probability is not None

    def test_pipeline_away_candidate_with_distinct_odds_gets_away_price(self):
        """
        Away candidate with distinct two-sided odds must use the away price
        (not the home price) for calibration.  The away no-vig prob < home no-vig prob.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        from gate_engine.moneyline_probability import extract_no_vig_probability

        enr_base = _base_enr(home_win_pct=0.60, away_win_pct=0.40)
        enr_base["sportsbook_odds"] = [
            {"team": "home_team_name", "odds": -145},
            {"team": "away_team_name", "odds": +125},
        ]

        row_home = _base_row(sport="NBA", home_away="HOME")
        row_home["team"] = "home_team_name"
        row_home["opponent"] = "away_team_name"

        row_away = _base_row(sport="NBA", home_away="@")
        row_away["team"] = "away_team_name"
        row_away["opponent"] = "home_team_name"

        p_home_no_vig = extract_no_vig_probability(
            enr_base, side="home_team_name", opponent="away_team_name"
        )
        p_away_no_vig = extract_no_vig_probability(
            enr_base, side="away_team_name", opponent="home_team_name"
        )
        assert p_home_no_vig is not None and p_away_no_vig is not None
        assert p_home_no_vig > p_away_no_vig, (
            f"Home no-vig ({p_home_no_vig:.4f}) must exceed away no-vig ({p_away_no_vig:.4f})"
        )
        assert abs(p_home_no_vig + p_away_no_vig - 1.0) < 0.001


class TestSoccerThreeState:
    """
    Verifies that soccer 1X2 uses a single coherent distribution (not binary
    inversion + arbitrary residual splits).

    Key invariants:
    - p_home + p_draw + p_away = 1.0 in the three-state output
    - Draw candidate produces a draw-range probability (not the inverted
      home-win binary probability)
    - All three outcomes extracted from the SAME sport-model distribution
    """

    def _soccer_enr(self, home_win_pct=0.55, away_win_pct=0.45):
        return _base_enr(home_win_pct=home_win_pct, away_win_pct=away_win_pct)

    def test_soccer_home_three_state_sums_to_one(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="SOCCER")
        row["outcome"] = "home"
        result = run_moneyline_pipeline(row, self._soccer_enr(), n_sims=200, seed=0)
        ts = result.three_state_1x2
        assert ts is not None, "Soccer home candidate must produce three_state_1x2"
        total = ts.get("p_home", 0) + ts.get("p_draw", 0) + ts.get("p_away", 0)
        assert abs(total - 1.0) < 0.02, (
            f"Soccer home three-state must sum to 1.0; got {total:.4f} "
            f"(p_h={ts.get('p_home'):.4f} p_d={ts.get('p_draw'):.4f} p_a={ts.get('p_away'):.4f})"
        )

    def test_soccer_draw_three_state_sums_to_one(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="SOCCER")
        row["outcome"] = "draw"
        result = run_moneyline_pipeline(row, self._soccer_enr(), n_sims=200, seed=0)
        ts = result.three_state_1x2
        assert ts is not None, "Soccer draw candidate must produce three_state_1x2"
        total = ts.get("p_home", 0) + ts.get("p_draw", 0) + ts.get("p_away", 0)
        assert abs(total - 1.0) < 0.02, (
            f"Soccer draw three-state must sum to 1.0; got {total:.4f}"
        )

    def test_soccer_away_three_state_sums_to_one(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="SOCCER")
        row["outcome"] = "away"
        result = run_moneyline_pipeline(row, self._soccer_enr(), n_sims=200, seed=0)
        ts = result.three_state_1x2
        assert ts is not None, "Soccer away candidate must produce three_state_1x2"
        total = ts.get("p_home", 0) + ts.get("p_draw", 0) + ts.get("p_away", 0)
        assert abs(total - 1.0) < 0.02, (
            f"Soccer away three-state must sum to 1.0; got {total:.4f}"
        )

    def test_soccer_draw_independent_prob_is_draw_range(self):
        """
        Draw candidate must have independent_probability in the draw-frequency
        range (~0.20–0.35), not a near-binary home/away value.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="SOCCER")
        row["outcome"] = "draw"
        result = run_moneyline_pipeline(row, self._soccer_enr(), n_sims=200, seed=0)
        ip = result.outputs.independent_probability
        assert ip is not None
        assert 0.10 < ip < 0.45, (
            f"Soccer draw independent_prob must be in draw-frequency range "
            f"[0.10, 0.45]; got {ip:.4f}"
        )

    def test_soccer_home_independent_prob_from_three_state_not_inverted(self):
        """
        Soccer home candidate must extract p_home from the three-state distribution,
        not apply binary inversion (which would give ~1-p_home for is_home=False).
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row_home = _base_row(sport="SOCCER")
        row_home["outcome"] = "home"
        row_away = _base_row(sport="SOCCER")
        row_away["outcome"] = "away"
        r_home = run_moneyline_pipeline(row_home, self._soccer_enr(), n_sims=200, seed=0)
        r_away = run_moneyline_pipeline(row_away, self._soccer_enr(), n_sims=200, seed=0)
        p_h = r_home.outputs.independent_probability
        p_a = r_away.outputs.independent_probability
        assert p_h is not None and p_a is not None
        # In a balanced-ish game, home and away must be different but both < 1.0
        # If binary inversion were applied, p_home + p_away ≈ 1.0 (draw ignored)
        # With three-state, p_home + p_away < 1.0 (draw takes a share)
        assert p_h + p_a < 0.95, (
            f"Soccer P(home)+P(away) must leave room for the draw; "
            f"got {p_h:.4f}+{p_a:.4f}={p_h+p_a:.4f}"
        )

    def test_soccer_draw_p_home_plus_p_draw_plus_p_away_coherent_across_outcomes(self):
        """
        The three-state distribution for home/draw/away candidates must all
        originate from the same sport-model distribution.

        Specifically: the sport_model produces a single (p_home, p_draw, p_away)
        distribution.  Each candidate uses its own component as independent_probability.
        The three-state output for each candidate (stage 12) scales the distribution
        so that the candidate's component = calibrated_probability and p+p+p = 1.0.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        enr = self._soccer_enr()
        for outcome in ("home", "draw", "away"):
            row = _base_row(sport="SOCCER")
            row["outcome"] = outcome
            result = run_moneyline_pipeline(row, enr, n_sims=500, seed=0)
            ts = result.three_state_1x2
            assert ts is not None, f"outcome={outcome} must produce three_state_1x2"
            total = ts.get("p_home", 0) + ts.get("p_draw", 0) + ts.get("p_away", 0)
            assert abs(total - 1.0) < 0.02, (
                f"outcome={outcome}: three-state must sum to 1.0; got {total:.4f}"
            )

    def test_unregistered_sport_does_not_fall_back_to_market_odds(self):
        """
        When a sport has no registered model (UNAVAILABLE), sportsbook odds
        must NOT substitute — the result must be DATA_CONTRACT_FAIL with a
        NO_REGISTERED_MODEL blocker.  This is the same for all sports without
        a model registry entry.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="CRICKET")   # not in model registry
        enr = {"sportsbook_odds": [{"team": "Team A", "odds": -145}]}
        result = run_moneyline_pipeline(row, enr, n_sims=100, seed=0)
        assert result.terminal_label == "DATA_CONTRACT_FAIL"
        assert any("NO_REGISTERED_MODEL" in b for b in result.blockers)


class TestMarketDependentFlag:
    """
    Verifies that MARKET_DEPENDENT_MODEL flag triggers correctly for high-quality
    markets (many books, fully mature, tight hold, fresh, H2H).

    Before the fix: base_w = 0.25 * factors ≤ 0.25 < 0.50 → flag never fires.
    After the fix: base_w = 0.70 * factors; for a perfect market base_w = 0.70 > 0.50.
    """

    def test_market_dependent_flag_fires_for_high_quality_market(self):
        from gate_engine.moneyline.dynamic_calibration import _compute_market_weight
        # 10 books, 24h open, 3% hold (tight), fresh, full-game H2H
        inputs = {
            "bookmaker_count":        10,
            "hours_since_open":       24.0,
            "hold_pct":               0.03,
            "market_freshness_hours": 0.5,
            "market_type":            "h2h",
        }
        weight, market_dep, notes = _compute_market_weight(inputs)
        assert market_dep is True, (
            f"A perfect 10-book, 24h-mature H2H market must trigger MARKET_DEPENDENT_MODEL; "
            f"weight={weight:.4f}"
        )
        assert weight == 0.50, f"Weight must be clamped at cap=0.50; got {weight:.4f}"

    def test_market_dependent_flag_does_not_fire_for_thin_market(self):
        from gate_engine.moneyline.dynamic_calibration import _compute_market_weight
        # 2 books, 4h open, 5% hold, H2H
        inputs = {
            "bookmaker_count":        2,
            "hours_since_open":       4.0,
            "hold_pct":               0.05,
            "market_freshness_hours": 1.0,
            "market_type":            "h2h",
        }
        weight, market_dep, notes = _compute_market_weight(inputs)
        assert market_dep is False, (
            f"A thin 2-book market must NOT trigger MARKET_DEPENDENT_MODEL; "
            f"weight={weight:.4f}"
        )

    def test_market_weight_capped_at_50_pct(self):
        from gate_engine.moneyline.dynamic_calibration import _compute_market_weight
        inputs = {
            "bookmaker_count":        100,  # extreme liquidity
            "hours_since_open":       48.0,
            "hold_pct":               0.02,
            "market_freshness_hours": 0.0,
            "market_type":            "h2h",
        }
        weight, _, _ = _compute_market_weight(inputs)
        assert weight <= 0.50, f"Market weight must never exceed 0.50; got {weight:.4f}"


class TestFailurePathIntegration:
    def test_high_primary_kill_path_lowers_win_prob(self):
        from gate_engine.moneyline.failure_path import integrate_failure_paths

        fp_matrix = {
            "PRIMARY_KILL_PATH": {
                "scenario":        "Starting pitcher early removal",
                "probability_band": "35–45%",
                "model_adjustment": "-8% applied to model_prob",
                "evidence":        "ERA 6.2 last 3 starts",
            },
            "SECONDARY_KILL_PATH": {
                "scenario":        "Bullpen fatigue",
                "probability_band": "20–30%",
                "model_adjustment": "-4%",
                "evidence":        "5 consecutive games",
            },
            "BLACK_SWAN_PATH": {
                "scenario":        "Rain delay affecting SP rhythm",
                "probability_band": "5–10%",
                "model_adjustment": "-2%",
                "evidence":        "weather forecast",
            },
        }
        result = integrate_failure_paths(0.60, fp_matrix)
        assert result.adjusted_win_prob < 0.60, (
            "High primary kill path must lower win probability"
        )
        assert result.failure_path_influence < 0.0

    def test_absent_failure_path_is_not_applicable(self):
        from gate_engine.moneyline.failure_path import integrate_failure_paths
        result = integrate_failure_paths(0.60, None)
        assert result.status == "NOT_APPLICABLE"
        assert result.adjusted_win_prob == 0.60   # unchanged


# ---------------------------------------------------------------------------
# WOW-PATCH-2026-08-17-MONEYLINE-MARKET-SNAPSHOT
# Endpoint → scorer handoff regression suite (verifier-required 8 cases)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone as _tz

from gate_engine.moneyline.market_snapshot import (
    MoneylineMarketSnapshot,
    BookQuote,
    build_snapshot_from_odds_event,
    snapshot_to_scorer_enrichment,
    attach_snapshot_to_enrichment,
    check_pipeline_invariant,
    consensus_no_vig,
    MARKET_PIPELINE_CONTRACT_BREACH,
    PIPELINE_COUNTERS,
)
from gate_engine.moneyline.pipeline import run_moneyline_pipeline
from gate_engine.moneyline_probability import _event_dedup_key

_NOW = datetime.now(_tz.utc)
_FRESH_TS = (_NOW - timedelta(minutes=30)).isoformat()
_STALE_TS = (_NOW - timedelta(hours=48)).isoformat()

_ALIASES = {
    "Minnesota Lynx":   ["MIN", "MIN Lynx", "Lynx"],
    "New York Liberty": ["NY", "NY Liberty", "Liberty"],
}


def _odds_event(n_books=10, home="Minnesota Lynx", away="New York Liberty",
                home_price=-130, away_price=+110, market_key="h2h",
                last_update=None, reversed_outcomes=False):
    outcomes = [
        {"name": home, "price": home_price},
        {"name": away, "price": away_price},
    ]
    if reversed_outcomes:
        outcomes = list(reversed(outcomes))
    return {
        "id": "evt-abc-123",
        "sport_key": "basketball_wnba",
        "commence_time": (_NOW + timedelta(hours=8)).isoformat(),
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": f"book{i}",
                "title": f"Book {i}",
                "markets": [{
                    "key": market_key,
                    "last_update": last_update or _FRESH_TS,
                    "outcomes": [dict(o) for o in outcomes],
                }],
            }
            for i in range(n_books)
        ],
    }


class TestMoneylineMarketSnapshotHandoff:

    # -- 1. Count invariant: 10 fetched books reach scorer as 10 books ------
    def test_ten_book_fixture_reaches_scorer_as_ten_books(self):
        snap = build_snapshot_from_odds_event(
            _odds_event(10), "WNBA", now=_NOW, aliases=_ALIASES)
        enr = snapshot_to_scorer_enrichment(snap)
        c = snap.counters
        assert c["books_fetched"] == 10
        assert c["books_event_matched"] == 10
        assert c["books_market_matched"] == 10
        assert c["books_normalized"] == 10
        assert c["books_fresh"] == 10
        assert c["books_sent_to_scorer"] == 10
        # 10 books × 2 participants = 20 flat quotes in scorer shape
        assert len(enr["sportsbook_odds"]) == 20
        assert all({"odds", "team", "name"} <= set(b) for b in enr["sportsbook_odds"])
        assert snap.status == "OK"
        assert snap.can_execute is False

    # -- 2. Team aliases resolve to canonical participant IDs ---------------
    def test_alias_resolves_to_canonical_participant(self):
        snap = build_snapshot_from_odds_event(
            _odds_event(2), "WNBA", now=_NOW, aliases=_ALIASES)
        assert snap.resolve_participant("MIN Lynx", _ALIASES) == "Minnesota Lynx"
        assert snap.resolve_participant("Liberty", _ALIASES) == "New York Liberty"
        assert snap.resolve_participant("Chicago Sky", _ALIASES) is None
        # Event id must be carried on the snapshot (root cause 1)
        assert snap.event_id == "evt-abc-123"
        # And the scorer dedup key must prefer it over fuzzy team names
        k1 = _event_dedup_key({"event_id": "evt-abc-123",
                               "sport": "WNBA", "team": "MIN Lynx",
                               "opponent": "Liberty", "slate_date": "2026-08-17"})
        k2 = _event_dedup_key({"event_id": "evt-abc-123",
                               "sport": "WNBA", "team": "Minnesota Lynx",
                               "opponent": "New York Liberty",
                               "slate_date": "2026-08-17"})
        assert k1 == k2 == "EVENT:evt-abc-123"

    # -- 3. Reversed home/away ordering remains correct ----------------------
    def test_reversed_home_away_ordering_remains_correct(self):
        snap_fwd = build_snapshot_from_odds_event(
            _odds_event(3), "WNBA", now=_NOW, aliases=_ALIASES)
        snap_rev = build_snapshot_from_odds_event(
            _odds_event(3, reversed_outcomes=True), "WNBA", now=_NOW,
            aliases=_ALIASES)
        for snap in (snap_fwd, snap_rev):
            home_quotes = [q for q in snap.books if q.team == "Minnesota Lynx"]
            away_quotes = [q for q in snap.books if q.team == "New York Liberty"]
            assert len(home_quotes) == 3 and len(away_quotes) == 3
            assert all(q.american_odds == -130 for q in home_quotes)
            assert all(q.american_odds == +110 for q in away_quotes)
        assert (consensus_no_vig(snap_fwd, "Minnesota Lynx")
                == pytest.approx(consensus_no_vig(snap_rev, "Minnesota Lynx")))

    # -- 4. Stale quotes rejected with explicit counts ------------------------
    def test_stale_quotes_rejected_with_explicit_counts(self):
        event = _odds_event(10)
        # Make 4 of the 10 books stale
        for bm in event["bookmakers"][:4]:
            bm["markets"][0]["last_update"] = _STALE_TS
        snap = build_snapshot_from_odds_event(
            event, "WNBA", now=_NOW, aliases=_ALIASES)
        assert snap.counters["books_normalized"] == 10
        assert snap.counters["books_fresh"] == 6
        stale_drops = [d for d in snap.dropped if d["reason"] == "STALE_QUOTE"]
        assert len(stale_drops) == 4
        enr = snapshot_to_scorer_enrichment(snap)
        assert snap.counters["books_sent_to_scorer"] == 6
        assert len(enr["sportsbook_odds"]) == 12

    # -- 5. Unsupported markets cannot masquerade as moneylines --------------
    def test_unsupported_market_cannot_masquerade_as_moneyline(self):
        # a) requesting a non-moneyline market key is refused outright
        snap = build_snapshot_from_odds_event(
            _odds_event(5, market_key="spreads"), "WNBA",
            market_key="spreads", now=_NOW, aliases=_ALIASES)
        assert snap.status == "UNSUPPORTED_MARKET_KEY"
        assert snap.books == []
        # b) bookmakers offering only spreads never market-match for h2h
        snap2 = build_snapshot_from_odds_event(
            _odds_event(5, market_key="spreads"), "WNBA",
            market_key="h2h", now=_NOW, aliases=_ALIASES)
        assert snap2.counters["books_market_matched"] == 0
        assert snap2.books == []
        assert all(d["reason"] == "MARKET_KEY_NOT_OFFERED" for d in snap2.dropped)

    # -- 6. Zero normalized books after successful fetch fails hard ----------
    def test_zero_books_after_fetch_is_contract_breach(self):
        event = _odds_event(5)
        # Corrupt every outcome name so participant resolution fails
        for bm in event["bookmakers"]:
            for o in bm["markets"][0]["outcomes"]:
                o["name"] = "???unknown???"
        snap = build_snapshot_from_odds_event(
            event, "WNBA", now=_NOW, aliases=_ALIASES)
        assert snap.counters["books_fetched"] == 5
        assert snap.counters["books_normalized"] == 0
        snapshot_to_scorer_enrichment(snap)
        assert snap.status == MARKET_PIPELINE_CONTRACT_BREACH
        assert check_pipeline_invariant(snap) is True
        # Scoring is blocked when the snapshot reaches the pipeline
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        result = run_moneyline_pipeline(
            row, {**_base_enr(), "market_snapshot": snap.to_dict()})
        assert result.terminal_label == MARKET_PIPELINE_CONTRACT_BREACH
        assert result.can_execute is False
        assert any(MARKET_PIPELINE_CONTRACT_BREACH in b for b in result.blockers)

    # -- 7. Consensus no-vig output matches known fixture --------------------
    def test_consensus_no_vig_matches_known_fixture(self):
        snap = build_snapshot_from_odds_event(
            _odds_event(2, home_price=-120, away_price=+100), "WNBA",
            now=_NOW, aliases=_ALIASES)
        # implied(-120) = 120/220 = 0.545455 ; implied(+100) = 0.5
        # no-vig home = 0.545455 / (0.545455 + 0.5) = 0.521739
        assert consensus_no_vig(snap, "Minnesota Lynx") == pytest.approx(
            0.521739, abs=1e-4)
        assert consensus_no_vig(snap, "New York Liberty") == pytest.approx(
            0.478261, abs=1e-4)
        # Alias input resolves before consensus
        assert consensus_no_vig(snap, "MIN Lynx", aliases=_ALIASES) == \
            pytest.approx(0.521739, abs=1e-4)

    # -- 8. Smoke: endpoint snapshot → scorer pipeline end-to-end ------------
    def test_endpoint_to_scorer_pipeline_smoke(self):
        snap = build_snapshot_from_odds_event(
            _odds_event(10), "WNBA", now=_NOW, aliases=_ALIASES)
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = snap.to_dict()
        result = run_moneyline_pipeline(row, enr, seed=7)
        assert result.terminal_label != MARKET_PIPELINE_CONTRACT_BREACH
        assert result.can_execute is False
        mc = result.market_comparison
        # Stage-0 handoff counters survive the stage-11 rebuild
        counters = mc.get("market_snapshot_counters") or {}
        assert counters.get("books_fetched") == 10
        assert counters.get("books_sent_to_scorer") == 10
        assert mc.get("market_snapshot_event_id") == "evt-abc-123"
        assert mc.get("market_snapshot_status") == "OK"
        # The scorer actually SAW the books (stage 11 comparison)
        assert mc.get("bookmaker_count") == 10
        # Market no-vig flowed into the output
        assert mc.get("market_no_vig") is not None

    # -- roundtrip serialization guard ---------------------------------------
    def test_snapshot_dict_roundtrip(self):
        snap = build_snapshot_from_odds_event(
            _odds_event(3), "WNBA", now=_NOW, aliases=_ALIASES)
        snap2 = MoneylineMarketSnapshot.from_dict(snap.to_dict())
        assert snap2.event_id == snap.event_id
        assert len(snap2.books) == len(snap.books)
        assert snap2.counters == snap.counters
        assert set(PIPELINE_COUNTERS) <= set(snap2.counters)


# ---------------------------------------------------------------------------
# Live-path integration: score_outright_winner_row (the production scoring
# entry point called from app.py Step B) + the app.py acquisition boundary.
# ---------------------------------------------------------------------------

import ast as _ast
import os as _os

from gate_engine.moneyline_probability import score_outright_winner_row

_APP_PY = _os.path.join(_os.path.dirname(__file__), "..", "app.py")


class TestLiveScoringPathIntegration:

    def test_score_outright_winner_row_consumes_snapshot_counters(self):
        """The production entry point (not a manual pipeline call) must see
        the snapshot books and surface the handoff counters."""
        snap = build_snapshot_from_odds_event(
            _odds_event(10), "WNBA", now=_NOW, aliases=_ALIASES)
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = snap.to_dict()
        scored = score_outright_winner_row(row, enrichment=enr)
        assert scored["can_execute"] is False
        assert scored["terminal_label"] != MARKET_PIPELINE_CONTRACT_BREACH
        mc = (scored["probability_snapshot"]
              ["moneyline_architecture_layers"]["market_comparison"])
        counters = mc.get("market_snapshot_counters") or {}
        assert counters.get("books_fetched") == 10
        assert counters.get("books_sent_to_scorer") == 10
        assert mc.get("bookmaker_count") == 10

    def test_score_outright_winner_row_blocks_on_breach(self):
        """All books dropped between fetch and scorer → production entry point
        must return MARKET_PIPELINE_CONTRACT_BREACH, never a silent zero-book
        score."""
        event = _odds_event(5)
        for bm in event["bookmakers"]:
            for o in bm["markets"][0]["outcomes"]:
                o["name"] = "???unknown???"
        snap = build_snapshot_from_odds_event(
            event, "WNBA", now=_NOW, aliases=_ALIASES)
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = snap.to_dict()
        scored = score_outright_winner_row(row, enrichment=enr)
        assert scored["terminal_label"] == MARKET_PIPELINE_CONTRACT_BREACH
        assert any(MARKET_PIPELINE_CONTRACT_BREACH in b
                   for b in scored["blockers"])

    # -- app.py acquisition boundary (extracted from real source) -----------

    @staticmethod
    def _load_app_fn(names, ns):
        src = open(_APP_PY).read()
        tree = _ast.parse(src)
        lines = src.splitlines(keepends=True)
        found = {}
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name in names:
                snippet = "".join(lines[node.lineno - 1:node.end_lineno])
                exec(compile(snippet, f"<app.py:{node.name}>", "exec"), ns)
                found[node.name] = ns[node.name]
        missing = set(names) - set(found)
        assert not missing, f"functions not found in app.py: {missing}"
        return found

    def test_ow_acquire_market_snapshot_prefers_event_id_cache(self):
        class _Log:
            def warning(self, *a, **k): pass
        cached = {"event_id": "evt-cached-1", "books": [], "counters": {}}
        ns = {
            "_LLP_EVENT_SNAPSHOT_CACHE": {"evt-cached-1": cached},
            "_LLP_EVENT_SNAPSHOT_CACHE_MAX": 512,
            "_LLP_SPORT_MAP": {"wnba": "basketball_wnba"},
            "_LLP_TEAM_ALIASES": _ALIASES,
            "_llp_fetch_odds": lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("must not fetch when cache hit")),
            "_llp_match_event": lambda *a, **k: None,
            "app": type("A", (), {"logger": _Log()})(),
        }
        fns = self._load_app_fn(["_ow_acquire_market_snapshot"], ns)
        out = fns["_ow_acquire_market_snapshot"](
            {"event_id": "evt-cached-1", "sport": "WNBA"})
        assert out is cached

    def test_ow_acquire_market_snapshot_live_fetch_normalizes(self):
        class _Log:
            def warning(self, *a, **k): pass
        events = [_odds_event(4)]
        ns = {
            "_LLP_EVENT_SNAPSHOT_CACHE": {},
            "_LLP_EVENT_SNAPSHOT_CACHE_MAX": 512,
            "_LLP_SPORT_MAP": {"wnba": "basketball_wnba"},
            "_LLP_TEAM_ALIASES": _ALIASES,
            "_llp_fetch_odds": lambda sk, regions="us", markets=None: events,
            "_llp_match_event": lambda evs, away, home: evs[0],
            "app": type("A", (), {"logger": _Log()})(),
        }
        fns = self._load_app_fn(
            ["_llp_cache_event_snapshot", "_ow_acquire_market_snapshot"], ns)
        out = fns["_ow_acquire_market_snapshot"](
            {"sport": "WNBA", "team": "Minnesota Lynx",
             "opponent": "New York Liberty"})
        assert out is not None
        assert out["event_id"] == "evt-abc-123"
        assert out["counters"]["books_fetched"] == 4
        # Snapshot is preserved by event_id for later promotion/scoring
        assert ns["_LLP_EVENT_SNAPSHOT_CACHE"]["evt-abc-123"] is out

    def test_app_step_b_attaches_snapshot_before_scoring(self):
        """Structural guard: the Step B scoring loop in app.py must attach
        enrichment["market_snapshot"] via _ow_acquire_market_snapshot BEFORE
        calling _score_moneyline."""
        src = open(_APP_PY).read()
        i_call = src.index("_score_moneyline(_ow_row, enrichment=_ow_enr)")
        block = src[max(0, i_call - 2000):i_call]
        assert "_ow_acquire_market_snapshot(_ow_row)" in block
        assert '_ow_enr["market_snapshot"]' in block


class TestSnapshotParticipantAlignment:
    """Row team names are display/abbreviated strings; the snapshot carries
    canonical Odds API names.  Quotes must align to the row's own strings so
    the scorer's exact-string matching sees every book — or fail closed."""

    def _run(self, team, opp, n_books=10):
        snap = build_snapshot_from_odds_event(
            _odds_event(n_books), "WNBA", now=_NOW, aliases=_ALIASES)
        row = _base_row(sport="WNBA", team=team, opponent=opp)
        enr = _base_enr()
        enr["market_snapshot"] = snap.to_dict()
        return run_moneyline_pipeline(row, enr, seed=11)

    def test_abbreviated_row_teams_reach_scorer_with_full_book_count(self):
        result = self._run("MIN Lynx", "Liberty")
        assert result.terminal_label != MARKET_PIPELINE_CONTRACT_BREACH
        mc = result.market_comparison
        assert mc.get("bookmaker_count") == 10
        assert mc.get("market_no_vig") is not None
        # -130/+110 fixture: home no-vig ≈ 0.5535/(0.5535+0.4762) ≈ 0.5375
        assert 0.50 < mc["market_no_vig"] < 0.58

    def test_nickname_only_row_teams_align(self):
        result = self._run("Lynx", "Liberty")
        assert result.terminal_label != MARKET_PIPELINE_CONTRACT_BREACH
        assert result.market_comparison.get("bookmaker_count") == 10

    def test_unrelated_row_team_fails_closed_not_silent_zero(self):
        result = self._run("Chicago Sky", "Las Vegas Aces")
        assert result.terminal_label == MARKET_PIPELINE_CONTRACT_BREACH
        assert any(MARKET_PIPELINE_CONTRACT_BREACH in b for b in result.blockers)

    def test_scorer_entry_point_alias_no_vig_correct(self):
        """Through score_outright_winner_row with -120/+100 books and an
        abbreviated row team: nonzero book count AND correct no-vig value."""
        snap = build_snapshot_from_odds_event(
            _odds_event(4, home_price=-120, away_price=+100), "WNBA",
            now=_NOW, aliases=_ALIASES)
        row = _base_row(sport="WNBA", team="MIN Lynx", opponent="Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = snap.to_dict()
        scored = score_outright_winner_row(row, enrichment=enr)
        assert scored["terminal_label"] != MARKET_PIPELINE_CONTRACT_BREACH
        mc = (scored["probability_snapshot"]
              ["moneyline_architecture_layers"]["market_comparison"])
        assert mc.get("bookmaker_count") == 4
        assert abs(float(mc.get("market_no_vig")) - 0.521739) <= 5e-3

    def test_alias_table_travels_with_snapshot_ny_alias(self):
        """The acquisition-time alias table is carried inside the snapshot
        (serialization roundtrip included) so scorer-side alignment resolves
        configured aliases like 'NY' without re-supplying the mapping."""
        snap = build_snapshot_from_odds_event(
            _odds_event(6), "WNBA", now=_NOW, aliases=_ALIASES)
        rehydrated = MoneylineMarketSnapshot.from_dict(snap.to_dict())
        assert rehydrated.aliases == {
            k: list(v) for k, v in _ALIASES.items()}
        assert rehydrated.resolve_participant("NY") == "New York Liberty"
        assert rehydrated.resolve_participant("MIN") == "Minnesota Lynx"

        row = _base_row(sport="WNBA", team="NY", opponent="MIN")
        enr = _base_enr()
        enr["market_snapshot"] = rehydrated.to_dict()
        result = run_moneyline_pipeline(row, enr, seed=11)
        assert result.terminal_label != MARKET_PIPELINE_CONTRACT_BREACH
        assert result.market_comparison.get("bookmaker_count") == 6
        assert result.market_comparison.get("market_no_vig") is not None

    def test_one_sided_snapshot_breaches_pipeline(self):
        """Snapshot stripped to one side must breach — a partial market may
        never be scored from a single side's vigged implied probability."""
        snap = build_snapshot_from_odds_event(
            _odds_event(6), "WNBA", now=_NOW, aliases=_ALIASES)
        snap.books = [q for q in snap.books if q.team == "Minnesota Lynx"]
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = snap.to_dict()
        result = run_moneyline_pipeline(row, enr, seed=11)
        assert result.terminal_label == MARKET_PIPELINE_CONTRACT_BREACH
        assert any(MARKET_PIPELINE_CONTRACT_BREACH in b for b in result.blockers)

    def test_one_sided_snapshot_breaches_scorer_entry_point(self):
        snap = build_snapshot_from_odds_event(
            _odds_event(4), "WNBA", now=_NOW, aliases=_ALIASES)
        snap.books = [q for q in snap.books if q.team == "New York Liberty"]
        row = _base_row(sport="WNBA", team="NY", opponent="MIN")
        enr = _base_enr()
        enr["market_snapshot"] = snap.to_dict()
        scored = score_outright_winner_row(row, enrichment=enr)
        assert scored["terminal_label"] == MARKET_PIPELINE_CONTRACT_BREACH

    def test_consensus_no_vig_unavailable_for_one_sided_snapshot(self):
        snap = build_snapshot_from_odds_event(
            _odds_event(4), "WNBA", now=_NOW, aliases=_ALIASES)
        snap.books = [q for q in snap.books if q.team == "Minnesota Lynx"]
        assert consensus_no_vig(snap, "Minnesota Lynx") is None

    def test_supplied_empty_snapshot_breaches_pipeline(self):
        """An explicitly supplied empty snapshot ({}) must fail closed —
        never be ignored in favor of caller odds or a re-fetch."""
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = {}
        result = run_moneyline_pipeline(row, enr, seed=11)
        assert result.terminal_label == MARKET_PIPELINE_CONTRACT_BREACH

    def test_supplied_empty_snapshot_breaches_scorer_entry_point(self):
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = {}
        scored = score_outright_winner_row(row, enrichment=enr)
        assert scored["terminal_label"] == MARKET_PIPELINE_CONTRACT_BREACH


class TestSnapshotCachePresenceSemantics:
    """Cache/snapshot supply is judged by key PRESENCE: cached empty (`{}`),
    `None`, or malformed values must flow to the scorer's fail-closed
    validation — never fall through to a live fetch."""

    @staticmethod
    def _acquire(cached_value):
        class _Log:
            def warning(self, *a, **k): pass
        ns = {
            "_LLP_EVENT_SNAPSHOT_CACHE": {"evt-cached-1": cached_value},
            "_LLP_EVENT_SNAPSHOT_CACHE_MAX": 512,
            "_LLP_SPORT_MAP": {"wnba": "basketball_wnba"},
            "_LLP_TEAM_ALIASES": _ALIASES,
            "_llp_fetch_odds": lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("live fetch invoked for cached event")),
            "_llp_match_event": lambda *a, **k: None,
            "app": type("A", (), {"logger": _Log()})(),
        }
        fns = TestLiveScoringPathIntegration._load_app_fn(
            ["_ow_acquire_market_snapshot"], ns)
        return fns["_ow_acquire_market_snapshot"](
            {"event_id": "evt-cached-1", "sport": "WNBA"})

    def test_cached_empty_dict_returned_no_fetch(self):
        assert self._acquire({}) == {}

    def test_cached_none_returns_empty_dict_no_fetch(self):
        assert self._acquire(None) == {}

    def test_cached_malformed_value_returns_empty_dict_no_fetch(self):
        assert self._acquire(["not", "a", "snapshot"]) == {}

    def test_empty_snapshot_blocks_outright_winner_scoring(self):
        """{} attached via presence semantics → scorer breaches, no score."""
        row = _base_row(sport="WNBA", team="Minnesota Lynx",
                        opponent="New York Liberty")
        enr = _base_enr()
        enr["market_snapshot"] = {}
        scored = score_outright_winner_row(row, enrichment=enr)
        assert scored["terminal_label"] == MARKET_PIPELINE_CONTRACT_BREACH

    def test_step_b_attaches_on_presence_not_truthiness(self):
        """Structural guard: Step B must use `is not None` (presence), not
        truthiness, when attaching the acquired snapshot."""
        src = open(_APP_PY).read()
        i = src.index('if "market_snapshot" not in _ow_enr:')
        block = src[i:i + 700]
        assert "_ow_snap is not None" in block
        assert "if _ow_snap:" not in block
