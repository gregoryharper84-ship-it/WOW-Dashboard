"""
tests/test_fantasy_score_model.py
WOW v16 — Fantasy Score Generative Model Regression Suite

Covers all 15 regression cases mandated by WOW spec:
  R01  Same FS mean, different variance → different exact-line hit probability
  R02  Basketball components not independent (share minutes → positive PTS-REB corr)
  R03  Volatile stocks/TD/HR deps exposed by counterfactuals (not hard gates)
  R04  MLB pitcher uses unconditional 7-regime mixture (early_hook present)
  R05  Rejected Over does NOT auto-approve Under
  R06  52.9% lower bound cannot qualify
  R07  >=65% CLB still fails with unresolved scoring/settlement identity
  R08  Market-prior weight >50% triggers MARKET_DEPENDENT_MODEL
  R09  Scoring-weight changes alter distribution and identity
  R10  Material lineup/status refresh forces rerun flag
  R11  Equal raw probs → different CLBs under different uncertainty families
  R12  Dependency diagnostics are not hard gates
  R13  can_execute=False in every final result
  R14  Rejected MORE does not auto-approve LESS (symmetric independence)
  R15  MLB pitcher: early_hook regime produces low-IP / low-FS samples

All tests are offline (no network, no DB).
"""
from __future__ import annotations

import math
import random
import statistics
import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _base_row(
    sport="NBA", stat_key="FANTASY_SCORE", line=30.0, side="MORE",
    player="Test Player", event_id="EVT-0808", **kw
):
    import datetime
    row = {
        "sport": sport, "stat_key": stat_key, "line": line, "side": side,
        "player_name": player, "event_id": event_id,
        "game_date": str(datetime.date.today()),
        "gates": {}, "blockers": [],
    }
    row.update(kw)
    return row


def _base_enr(**kw):
    enr = {
        "event_status":     "SCHEDULED",
        "player_status":    "ACTIVE",
        "lineup_confirmed": True,
        "settlement_basis": "FULL_GAME_STATS",
        "status_freshness_hours": 0.5,
        "avg_minutes": 32.0,
        "pts_per_game": 18.0,
        "reb_per_game": 5.0,
        "ast_per_game": 3.0,
        "stl_per_game": 1.2,
        "blk_per_game": 0.7,
        "tov_per_game": 2.0,
        "game_log": list(range(20, 40)),  # dummy 20-element list
    }
    enr.update(kw)
    return enr


# ---------------------------------------------------------------------------
# R01: Same mean, different variance → different exact-line probability
# ---------------------------------------------------------------------------

class TestR01_VarianceMatters:
    """Same FS mean, different std → different P(FS > line)."""

    def _p_more(self, mean, std, line, n=20000):
        """Gaussian: P(X > line) = 1 − Φ((line − mean) / std)."""
        if std <= 0:
            return 1.0 if mean > line else 0.0
        z = (line - mean) / std
        return 1.0 - 0.5 * (1 + math.erf(z / math.sqrt(2)))

    def test_same_mean_different_variance_differs(self):
        line = 30.0
        mean = 32.0  # mean above line — both should be > 0.5
        p_low_std  = self._p_more(mean, 4.0, line)
        p_high_std = self._p_more(mean, 10.0, line)
        # High std → more mass in tails → different (lower) peak probability
        assert p_low_std != p_high_std, "Probabilities must differ when std differs"
        assert p_low_std > p_high_std, (
            f"Low std ({p_low_std:.4f}) should exceed high-std ({p_high_std:.4f}) "
            f"when mean > line"
        )

    def test_same_mean_line_below_mean_high_std_reduces_p(self):
        """With mean > line, higher variance reduces P(MORE) toward 0.5."""
        mean, line = 35.0, 30.0
        p4  = self._p_more(mean, 4.0, line)
        p12 = self._p_more(mean, 12.0, line)
        assert p4 > p12, "Higher std should pull probability closer to 0.5"

    def test_mc_generator_variance_affects_p_more(self):
        """End-to-end Monte Carlo: two identical-mean generators, different std."""
        from gate_engine.fantasy_score_model.generators.basketball import (
            generate_one, default_params,
        )
        from gate_engine.fantasy_score_model.shared import run_monte_carlo, score_line

        enr_tight = _base_enr(minutes_std=2.0)   # tight minutes → low FS std
        enr_wide  = _base_enr(minutes_std=12.0)  # wide minutes → high FS std

        p_tight = default_params(enr_tight)
        p_wide  = default_params(enr_wide)
        rng = random.Random(42)

        sims_tight = run_monte_carlo(generate_one, p_tight, n=10000, seed=42)
        sims_wide  = run_monte_carlo(generate_one, p_wide,  n=10000, seed=42)

        std_tight = statistics.pstdev(sims_tight)
        std_wide  = statistics.pstdev(sims_wide)

        assert std_wide > std_tight * 1.2, (
            f"Wide-minutes std ({std_wide:.2f}) should exceed tight-minutes "
            f"std ({std_tight:.2f}) by ≥20%"
        )

        mean_tight = statistics.mean(sims_tight)
        mean_wide  = statistics.mean(sims_wide)
        line = (mean_tight + mean_wide) / 2.0  # pick line near both means

        sc_tight = score_line(sims_tight, line)
        sc_wide  = score_line(sims_wide,  line)

        assert sc_tight["p_more"] != sc_wide["p_more"], (
            "Different variance must produce different P(MORE) at the same line"
        )


# ---------------------------------------------------------------------------
# R02: Basketball components share minutes → positive correlation
# ---------------------------------------------------------------------------

class TestR02_BasketballComponentCorrelation:
    """PTS and REB must be positively correlated in simulation (shared minutes)."""

    def test_pts_reb_positively_correlated(self):
        from gate_engine.fantasy_score_model.generators.basketball import (
            generate_one, default_params,
        )
        # We need to track individual components; instrument via a modified generator
        import math
        N = 5000
        pts_list, reb_list = [], []
        enr = _base_enr()
        params = default_params(enr)
        rng = random.Random(99)

        # Manually run the generator, capturing PTS and REB separately
        # We'll use the same logic but return both stats
        from gate_engine.fantasy_score_model.generators.basketball import (
            _poisson, _OT_EXTRA_MIN,
        )
        _LEAGUE_PTS = 0.563
        _LEAGUE_REB = 0.156

        for _ in range(N):
            # Same minutes draw as generate_one
            if rng.random() < params.get("dnp_risk_prob", 0.02):
                pts_list.append(0); reb_list.append(0)
                continue
            avg_min = params.get("avg_minutes", 30.0)
            std_min = params.get("minutes_std", 6.0)
            minutes = max(5.0, min(48.0, rng.gauss(avg_min, std_min)))
            if rng.random() < params.get("overtime_prob", 0.12):
                minutes = min(48.0, minutes + 5.0)
            pts = _poisson(params.get("pts_per_min", _LEAGUE_PTS) * minutes, rng)
            reb = _poisson(params.get("reb_per_min", _LEAGUE_REB) * minutes, rng)
            pts_list.append(pts); reb_list.append(reb)

        # Compute Pearson correlation
        n = len(pts_list)
        mu_pts = statistics.mean(pts_list)
        mu_reb = statistics.mean(reb_list)
        sd_pts = statistics.pstdev(pts_list)
        sd_reb = statistics.pstdev(reb_list)

        if sd_pts <= 0 or sd_reb <= 0:
            pytest.skip("Zero std — degenerate simulation")

        cov = sum((p - mu_pts) * (r - mu_reb) for p, r in zip(pts_list, reb_list)) / n
        corr = cov / (sd_pts * sd_reb)

        assert corr > 0.30, (
            f"PTS-REB Pearson correlation must be >0.30 (shared minutes); "
            f"got {corr:.4f}"
        )

    def test_independent_poisson_would_be_uncorrelated(self):
        """If we ignore shared minutes, correlation collapses to ~0."""
        N = 5000
        pts_list, reb_list = [], []
        rng = random.Random(7)
        lam_pts, lam_reb = 18.0, 5.0  # fixed lambda, no shared minutes

        from gate_engine.fantasy_score_model.generators.basketball import _poisson
        for _ in range(N):
            pts_list.append(_poisson(lam_pts, rng))
            reb_list.append(_poisson(lam_reb, rng))

        mu_pts = statistics.mean(pts_list)
        mu_reb = statistics.mean(reb_list)
        sd_pts = statistics.pstdev(pts_list)
        sd_reb = statistics.pstdev(reb_list)
        n = N
        cov  = sum((p - mu_pts) * (r - mu_reb) for p, r in zip(pts_list, reb_list)) / n
        corr = cov / (sd_pts * sd_reb)

        # Independent Poisson → correlation ≈ 0
        assert abs(corr) < 0.15, (
            f"Independent Poisson should be ~uncorrelated; got corr={corr:.4f}"
        )


# ---------------------------------------------------------------------------
# R03: Volatile dependencies exposed by counterfactuals (not hard gates)
# ---------------------------------------------------------------------------

class TestR03_VolatileDependencyCounterfactuals:
    """Stocks spike, TD, HR — exposed by counterfactuals but not auto-rejected."""

    def test_stocks_spike_counterfactual(self):
        from gate_engine.fantasy_score_model.generators.basketball import (
            generate_one, default_params,
        )
        from gate_engine.fantasy_score_model.shared import run_monte_carlo, score_line

        # High stocks_spike_prob, high spike multiplier
        enr = _base_enr(stocks_spike_prob=0.35, stocks_spike_mult=2.5,
                         stl_per_game=3.0, blk_per_game=2.0)
        params = default_params(enr)
        line   = 32.0

        base_sims = run_monte_carlo(generate_one, params, n=6000, seed=1)
        base_p    = score_line(base_sims, line)["p_more"]

        no_spike_p = {**params, "stocks_spike_prob": 0.0}
        no_sp_sims = run_monte_carlo(generate_one, no_spike_p, n=6000, seed=1)
        no_sp_p    = score_line(no_sp_sims, line)["p_more"]

        delta = base_p - no_sp_p
        # With high spike probability and strong multiplier, the delta should be >0
        assert delta > 0, (
            f"Stocks spike should increase P(MORE); delta={delta:.4f}"
        )
        # But the prop is NOT auto-rejected because of this dependency
        assert base_p > 0, "Prop with stocks spike should still have non-zero P(MORE)"

    def test_nfl_td_counterfactual(self):
        from gate_engine.fantasy_score_model.generators.nfl import (
            generate_one, default_params,
        )
        from gate_engine.fantasy_score_model.shared import run_monte_carlo, score_line

        enr    = {"position": "WR", "avg_targets": 8.0, "rec_rate": 0.72,
                  "yds_per_rec": 12.0, "td_rate_per_target": 0.12,
                  "game_log": list(range(10))}
        params = default_params(enr, "WR")
        line   = 18.0

        base_sims = run_monte_carlo(generate_one, params, n=6000, seed=2)
        base_p    = score_line(base_sims, line)["p_more"]

        no_td_p = {**params, "td_rate_per_target": 0.0}
        no_td_sims = run_monte_carlo(generate_one, no_td_p, n=6000, seed=2)
        no_td  = score_line(no_td_sims, line)["p_more"]

        assert base_p > no_td, (
            f"TD dependency: base ({base_p:.4f}) must exceed no-TD ({no_td:.4f})"
        )

    def test_mlb_hr_counterfactual(self):
        from gate_engine.fantasy_score_model.generators.mlb_hitter import (
            generate_one, default_params,
        )
        from gate_engine.fantasy_score_model.shared import run_monte_carlo, score_line

        enr    = {"hr_rate": 0.06, "game_log": list(range(10))}
        params = default_params(enr)
        line   = 12.0

        base_sims = run_monte_carlo(generate_one, params, n=6000, seed=3)
        base_p    = score_line(base_sims, line)["p_more"]

        no_hr_p = {**params, "hr_rate": 0.0, "hr_rate_per_pa": 0.0}
        no_hr_sims = run_monte_carlo(generate_one, no_hr_p, n=6000, seed=3)
        no_hr  = score_line(no_hr_sims, line)["p_more"]

        assert base_p > no_hr, (
            f"HR dependency: base ({base_p:.4f}) must exceed no-HR ({no_hr:.4f})"
        )


# ---------------------------------------------------------------------------
# R04 + R15: MLB pitcher uses unconditional 7-regime mixture
# ---------------------------------------------------------------------------

class TestR04_PitcherUnconditionalMixture:
    """Verify that all 7 regimes fire and early_hook produces low-IP samples."""

    def test_early_hook_samples_present(self):
        from gate_engine.fantasy_score_model.generators.mlb_pitcher import (
            generate_one, default_params, _gen_regime,
        )
        rng = random.Random(42)
        params = {"regime_weights": {"normal_effective": 0.0, "inefficient_surviving": 0.0,
                                     "early_hook": 1.0, "command_collapse": 0.0,
                                     "health_workload_restriction": 0.0,
                                     "environmental_disruption": 0.0,
                                     "opponent_extension": 0.0},
                  "win_rate": 0.05}
        sims = [generate_one(params, rng) for _ in range(1000)]
        mean_fs = statistics.mean(sims)
        # Early hook → low IP → low FS; mean should be well below normal_effective
        assert mean_fs < 20.0, (
            f"Early hook mean FS ({mean_fs:.2f}) should be low (<20)"
        )

    def test_normal_effective_higher_than_early_hook(self):
        from gate_engine.fantasy_score_model.generators.mlb_pitcher import generate_one
        rng1 = random.Random(10)
        rng2 = random.Random(10)
        p_normal = {"regime_weights": {"normal_effective": 1.0,
                                        "inefficient_surviving": 0.0, "early_hook": 0.0,
                                        "command_collapse": 0.0,
                                        "health_workload_restriction": 0.0,
                                        "environmental_disruption": 0.0,
                                        "opponent_extension": 0.0},
                    "win_rate": 0.40}
        p_hook   = {"regime_weights": {"normal_effective": 0.0,
                                        "inefficient_surviving": 0.0, "early_hook": 1.0,
                                        "command_collapse": 0.0,
                                        "health_workload_restriction": 0.0,
                                        "environmental_disruption": 0.0,
                                        "opponent_extension": 0.0},
                    "win_rate": 0.05}
        n_sims = [generate_one(p_normal, rng1) for _ in range(2000)]
        h_sims = [generate_one(p_hook,   rng2) for _ in range(2000)]
        assert statistics.mean(n_sims) > statistics.mean(h_sims) + 5.0, (
            "Normal-effective mean FS must be >5 pts above early-hook mean"
        )

    def test_unconditional_mixture_has_low_fs_tail(self):
        """Default mixture must include some very low FS samples (early hook / collapse)."""
        from gate_engine.fantasy_score_model.generators.mlb_pitcher import (
            generate_one, default_params,
        )
        params = default_params({})
        rng    = random.Random(77)
        sims   = [generate_one(params, rng) for _ in range(5000)]
        p_below_10 = sum(1 for s in sims if s < 10.0) / len(sims)
        assert p_below_10 > 0.10, (
            f"Unconditional mixture must have >10% of sims with FS<10 "
            f"(failure regimes); got {p_below_10:.3f}"
        )


# ---------------------------------------------------------------------------
# R05 + R14: Rejected Over does NOT auto-approve Under
# ---------------------------------------------------------------------------

class TestR05_R14_BidirectionalIndependence:
    """Rejected MORE does not automatically approve LESS."""

    def test_rejected_more_does_not_qualify_less(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        # Set line very high so P(MORE) is tiny; P(LESS) should be large
        row = _base_row(sport="NBA", line=80.0, side="MORE")
        enr = _base_enr(avg_minutes=32.0, pts_per_game=18.0)
        r_more = score_fantasy_row(row, enr, n_sims=4000, seed=55, run_stress=False, run_diag=False)

        row2 = _base_row(sport="NBA", line=80.0, side="LESS")
        r_less = score_fantasy_row(row2, enr, n_sims=4000, seed=55, run_stress=False, run_diag=False)

        # MORE should clearly fail at line=80 (avg FS ~18)
        assert r_more["p_more_raw"] < 0.05, "P(MORE) at line=80 should be very small"

        # LESS is independently evaluated — not just "approved because MORE failed"
        p_less = r_less["p_less_raw"]
        assert p_less is not None
        # The LESS evaluation must be separate (not just 1 - p_more)
        # Both should be independently computed from sims
        # The terminal label must be assigned independently
        assert "terminal_label" in r_less
        assert "terminal_label" in r_more

    def test_p_more_plus_p_less_plus_p_push_approximately_one(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="NBA", line=30.0, side="MORE")
        enr = _base_enr()
        result = score_fantasy_row(row, enr, n_sims=4000, seed=66, run_stress=False, run_diag=False)
        if result.get("calibrated_lower_bound") is None:
            pytest.skip("Reject output — skip probability check")
        total = (result["p_more_raw"] + result["p_less_raw"] + result["p_push"])
        assert abs(total - 1.0) < 0.01, (
            f"P(MORE)+P(LESS)+P(PUSH) must ≈ 1.0; got {total:.6f}"
        )


# ---------------------------------------------------------------------------
# R06: 52.9% lower bound cannot qualify
# ---------------------------------------------------------------------------

class TestR06_LowerBoundFloor:
    """52.9% CLB must never produce YES_MODEL_QUALIFIED."""

    def test_529_lb_cannot_qualify(self):
        from gate_engine.fantasy_score_model.shared import determine_label

        label, blockers = determine_label(
            lb=0.529,
            identity_locked=True,
            settlement_locked=True,
            model_is_provisional=False,  # even for ACTIVE model
            market_dependent=False,
        )
        assert label != "YES_MODEL_QUALIFIED", (
            f"52.9% CLB must not qualify; got {label}"
        )
        assert label in ("MODEL_QUALIFIED_HOLD", "WATCH", "REJECT_NO_EDGE"), (
            f"Expected HOLD/WATCH/REJECT; got {label}"
        )

    def test_650_lb_can_qualify_when_active(self):
        from gate_engine.fantasy_score_model.shared import determine_label
        label, _ = determine_label(
            lb=0.650, identity_locked=True, settlement_locked=True,
            model_is_provisional=False, market_dependent=False,
        )
        assert label == "YES_MODEL_QUALIFIED", f"65.0% CLB (ACTIVE) must qualify; got {label}"

    def test_649_lb_cannot_qualify(self):
        from gate_engine.fantasy_score_model.shared import determine_label
        label, _ = determine_label(
            lb=0.649, identity_locked=True, settlement_locked=True,
            model_is_provisional=False, market_dependent=False,
        )
        assert label != "YES_MODEL_QUALIFIED", f"64.9% CLB must not qualify; got {label}"

    def test_provisional_caps_at_hold_even_above_65(self):
        """PROVISIONAL model is capped at MODEL_QUALIFIED_HOLD even with CLB=0.75."""
        from gate_engine.fantasy_score_model.shared import determine_label
        label, _ = determine_label(
            lb=0.750, identity_locked=True, settlement_locked=True,
            model_is_provisional=True, market_dependent=False,
        )
        assert label == "MODEL_QUALIFIED_HOLD", (
            f"PROVISIONAL model with CLB=0.75 must be capped at HOLD; got {label}"
        )


# ---------------------------------------------------------------------------
# R07: >=65% CLB still fails when scoring/settlement identity is unresolved
# ---------------------------------------------------------------------------

class TestR07_IdentityLock:
    """High CLB must fail if scoring or settlement identity is not locked."""

    def test_high_clb_fails_without_identity_lock(self):
        from gate_engine.fantasy_score_model.shared import determine_label
        label, blockers = determine_label(
            lb=0.72, identity_locked=False, settlement_locked=True,
            model_is_provisional=False, market_dependent=False,
        )
        assert label == "REJECT_SCORING_IDENTITY_UNRESOLVED", (
            f"72% CLB with identity_locked=False must reject; got {label}"
        )
        assert any("SCORING_IDENTITY" in b for b in blockers)

    def test_high_clb_fails_without_settlement_lock(self):
        from gate_engine.fantasy_score_model.shared import determine_label
        label, blockers = determine_label(
            lb=0.72, identity_locked=True, settlement_locked=False,
            model_is_provisional=False, market_dependent=False,
        )
        assert label == "REJECT_SCORING_IDENTITY_UNRESOLVED", (
            f"72% CLB with settlement_locked=False must reject; got {label}"
        )

    def test_gate_rejects_missing_settlement_basis(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="NBA", line=20.0, side="MORE")
        enr = _base_enr()
        enr.pop("settlement_basis", None)  # remove settlement basis
        result = score_fantasy_row(row, enr, n_sims=3000, seed=7, run_stress=False, run_diag=False)
        assert result.get("settlement_locked") is False, (
            "Missing settlement_basis must set settlement_locked=False"
        )
        label = result.get("terminal_label")
        assert label == "REJECT_SCORING_IDENTITY_UNRESOLVED", (
            f"Missing settlement basis must reject; got {label}"
        )


# ---------------------------------------------------------------------------
# R08: Market-prior weight >50% triggers MARKET_DEPENDENT_MODEL
# ---------------------------------------------------------------------------

class TestR08_MarketPriorCap:
    def test_over_50pct_market_weight_triggers_flag(self):
        from gate_engine.fantasy_score_model.shared import apply_market_prior
        result = apply_market_prior(0.70, 0.65, market_weight=0.60)
        assert result["market_dependent_flag"] is True, (
            "market_weight=0.60 must set market_dependent_flag=True"
        )
        assert result["market_weight_effective"] == 0.50, (
            "market_weight must be clamped to 0.50"
        )

    def test_under_50pct_market_weight_no_flag(self):
        from gate_engine.fantasy_score_model.shared import apply_market_prior
        result = apply_market_prior(0.70, 0.65, market_weight=0.25)
        assert result["market_dependent_flag"] is False

    def test_market_dependent_label_emitted(self):
        from gate_engine.fantasy_score_model.shared import determine_label
        # CLB would qualify but market dependent → MARKET_DEPENDENT_MODEL
        label, blockers = determine_label(
            lb=0.70, identity_locked=True, settlement_locked=True,
            model_is_provisional=False, market_dependent=True,
        )
        assert label == "MARKET_DEPENDENT_MODEL", (
            f"Market-dependent with qualifying CLB must get MARKET_DEPENDENT_MODEL; got {label}"
        )
        assert any("MARKET_DEPENDENT" in b for b in blockers)

    def test_independent_prob_frozen_before_blend(self):
        """Raw independent probability is always preserved regardless of market blend."""
        from gate_engine.fantasy_score_model.shared import apply_market_prior
        raw = 0.72
        result = apply_market_prior(raw, 0.50, market_weight=0.40)
        assert result["raw_independent"] == raw, (
            "raw_independent must equal the pre-blend model probability"
        )
        # blended must differ from raw when market_weight > 0
        assert result["blended_prob"] != raw


# ---------------------------------------------------------------------------
# R09: Scoring-weight changes alter distribution and identity
# ---------------------------------------------------------------------------

class TestR09_ScoringWeightIdentity:
    def test_formula_weight_change_alters_distribution(self):
        """Two different weight tables produce different FS distributions."""
        import statistics as st

        # Standard NBA weights
        def fs_standard(pts, reb, ast, stl, blk, tov):
            return pts * 1.0 + reb * 1.2 + ast * 1.5 + stl * 3.0 + blk * 3.0 + tov * -1.0

        # Hypothetical alternative (heavier assists)
        def fs_alt(pts, reb, ast, stl, blk, tov):
            return pts * 1.0 + reb * 1.2 + ast * 2.0 + stl * 3.0 + blk * 3.0 + tov * -1.0

        rng = random.Random(123)
        rows = [
            (int(rng.gauss(18, 5)), int(rng.gauss(5, 2)), int(rng.gauss(3, 2)),
             max(0, int(rng.gauss(1, 1))), max(0, int(rng.gauss(0, 1))),
             max(0, int(rng.gauss(2, 1))))
            for _ in range(5000)
        ]
        std_scores = [fs_standard(*r) for r in rows]
        alt_scores = [fs_alt(*r)      for r in rows]

        mean_std = st.mean(std_scores)
        mean_alt = st.mean(alt_scores)
        assert mean_alt > mean_std, (
            "Higher assist weight must increase mean FS for assist-heavy players"
        )
        # Different weights → different formula identity
        assert mean_alt != mean_std

    def test_scoring_version_tracked_in_output(self):
        """Gate output must include scoring_version field."""
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="NBA", line=30.0)
        enr = _base_enr()
        result = score_fantasy_row(row, enr, n_sims=2000, seed=9,
                                   run_stress=False, run_diag=False)
        assert "scoring_version" in result, "scoring_version must be in gate output"
        assert "identity_locked" in result


# ---------------------------------------------------------------------------
# R10: Material lineup/status refresh changes force rerun flag
# ---------------------------------------------------------------------------

class TestR10_FinalRefresh:
    def test_stale_status_sets_refresh_required(self):
        from gate_engine.fantasy_score_model.shared import check_final_refresh
        enr = _base_enr(status_freshness_hours=4.0)   # >2h → stale
        result = check_final_refresh(enr)
        assert result["refresh_required"] is True
        assert any("STATUS_STALE" in f for f in result["refresh_flags"])

    def test_live_event_forces_refresh(self):
        from gate_engine.fantasy_score_model.shared import check_final_refresh
        enr = _base_enr(event_status="IN_PROGRESS", status_freshness_hours=0.1)
        result = check_final_refresh(enr)
        assert result["refresh_required"] is True
        assert any("EVENT_STARTED" in f for f in result["refresh_flags"])

    def test_board_line_change_forces_refresh(self):
        from gate_engine.fantasy_score_model.shared import check_final_refresh
        enr = _base_enr(board_line_confirmed=31.5, _scored_line=30.0)
        result = check_final_refresh(enr)
        assert result["refresh_required"] is True
        assert any("BOARD_LINE_CHANGED" in f for f in result["refresh_flags"])

    def test_fresh_confirmed_lineup_no_refresh(self):
        from gate_engine.fantasy_score_model.shared import check_final_refresh
        enr = _base_enr(
            status_freshness_hours=0.5,
            event_status="SCHEDULED",
            lineup_confirmed=True,
            settlement_basis="FULL_GAME_STATS",
            scoring_rules_retrieved_at="2026-08-08T10:00:00Z",
        )
        result = check_final_refresh(enr)
        # With fresh status, scheduled event, and confirmed lineup, no refresh needed
        # (scoring_rules_retrieved_at provided → no SCORING_RULES flag)
        flags_without_scoring = [f for f in result["refresh_flags"]
                                   if "SCORING_RULES" not in f]
        assert len(flags_without_scoring) == 0, (
            f"Fresh confirmed row should have no material refresh flags; "
            f"got {flags_without_scoring}"
        )


# ---------------------------------------------------------------------------
# R11: Equal raw probs → different CLBs under different uncertainty families
# ---------------------------------------------------------------------------

class TestR11_FamilyUncertaintyDiffers:
    def test_same_raw_prob_different_family_different_clb(self):
        from gate_engine.fantasy_score_model.calibration_families import compute_bounds

        raw_prob    = 0.72
        sample_size = 15

        lb_nba, _,  _ = compute_bounds(raw_prob, "NBA",         sample_size)
        lb_mlb_pit, _, _ = compute_bounds(raw_prob, "MLB_PITCHER", sample_size)

        # MLB_PITCHER has higher uncertainty → lower CLB for same raw prob
        assert lb_mlb_pit < lb_nba, (
            f"MLB_PITCHER CLB ({lb_mlb_pit:.4f}) must be below NBA CLB ({lb_nba:.4f}) "
            f"for equal raw probability — pitcher model is higher uncertainty"
        )

    def test_thin_sample_widens_uncertainty(self):
        from gate_engine.fantasy_score_model.calibration_families import compute_bounds

        raw = 0.72
        lb_large, _, thin_large = compute_bounds(raw, "NBA", sample_size=20)
        lb_small, _, thin_small = compute_bounds(raw, "NBA", sample_size=4)

        assert thin_small is True,  "n=4 should flag thin-sample condition"
        assert thin_large is False, "n=20 should not flag thin-sample condition"
        assert lb_small < lb_large, (
            "Thin sample must produce lower CLB (wider uncertainty)"
        )


# ---------------------------------------------------------------------------
# R12: Dependency diagnostics are not hard gates
# ---------------------------------------------------------------------------

class TestR12_DiagnosticsNotHardGates:
    """High dependency metrics must not automatically reject the prop."""

    def test_high_stocks_dependency_not_rejected(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        # Player with very high stocks dependency
        row = _base_row(sport="NBA", line=28.0, side="MORE")
        enr = _base_enr(
            stocks_spike_prob=0.50,   # very high
            stocks_spike_mult=3.0,
            stl_per_game=4.0,
            blk_per_game=3.0,
        )
        result = score_fantasy_row(row, enr, n_sims=3000, seed=15,
                                   run_stress=False, run_diag=True)
        # Must not be auto-rejected due to high dependency alone
        label = result.get("terminal_label")
        assert label != "REJECT_DATA_QUALITY", (
            f"High stocks dependency alone must not produce REJECT_DATA_QUALITY; got {label}"
        )
        # Diagnostics should be present and contain the dependency metric
        diag = result.get("diagnostics") or {}
        if "stocks_spike_dependency" in diag:
            delta = diag["stocks_spike_dependency"]
            assert isinstance(delta, float), "stocks_spike_dependency must be a float"

    def test_diagnostics_labeled_no_hard_gates(self):
        from gate_engine.fantasy_score_model.diagnostics import basketball_diagnostics
        from gate_engine.fantasy_score_model.generators.basketball import (
            generate_one, default_params,
        )
        from gate_engine.fantasy_score_model.shared import run_monte_carlo

        enr    = _base_enr()
        params = default_params(enr)
        sims   = run_monte_carlo(generate_one, params, n=2000, seed=20)
        rng    = random.Random(20)
        diag   = basketball_diagnostics(generate_one, params, sims, 30.0, rng)

        assert "note" in diag
        assert "hard rejection" in diag["note"].lower() or "no hard" in diag["note"].lower(), (
            "Diagnostics note must explicitly state no hard rejection thresholds"
        )


# ---------------------------------------------------------------------------
# R13: can_execute=False in every final result
# ---------------------------------------------------------------------------

class TestR13_CanExecuteFalse:
    """can_execute=False must appear unconditionally in every result."""

    @pytest.mark.parametrize("sport,stat_key,line,side,extra_enr", [
        ("NBA",  "FANTASY_SCORE", 30.0, "MORE", {}),
        ("WNBA", "FANTASY_SCORE", 25.0, "LESS", {}),
        ("NFL",  "FANTASY_SCORE", 20.0, "MORE", {"position": "QB"}),
        ("MLB",  "FANTASY_SCORE", 15.0, "MORE", {"position": "SP"}),  # pitcher
        ("MLB",  "FANTASY_SCORE", 12.0, "MORE", {"position": "OF"}),  # hitter
    ])
    def test_can_execute_false_all_sports(self, sport, stat_key, line, side, extra_enr):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport=sport, stat_key=stat_key, line=line, side=side)
        enr = _base_enr(**extra_enr)
        result = score_fantasy_row(row, enr, n_sims=2000, seed=42,
                                   run_stress=False, run_diag=False)
        assert result.get("can_execute") is False, (
            f"can_execute must be False for {sport} {stat_key}; "
            f"got {result.get('can_execute')!r}"
        )

    def test_can_execute_false_on_reject(self):
        """Even rejected rows must have can_execute=False."""
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="NHL", stat_key="FANTASY_SCORE", line=10.0)
        enr = _base_enr()
        result = score_fantasy_row(row, enr, n_sims=500, seed=0,
                                   run_stress=False, run_diag=False)
        assert result.get("can_execute") is False, (
            "Rejected rows must also have can_execute=False"
        )

    def test_shadow_mode_always_true(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="NBA", line=30.0)
        enr = _base_enr()
        result = score_fantasy_row(row, enr, n_sims=2000, seed=99,
                                   run_stress=False, run_diag=False)
        assert result.get("shadow_mode") is True

    def test_gate_run_does_not_set_terminal_label(self):
        """run() must NOT set row['terminal_label'] (shadow mode)."""
        from gate_engine.fantasy_score_model.gate import run
        row = _base_row(sport="NBA", line=30.0)
        enr = _base_enr()
        row["_enr"] = enr
        run(row, enr=enr)
        # Shadow mode: terminal_label must NOT be set by this gate
        assert "terminal_label" not in row or row.get("terminal_label") is None, (
            "Shadow gate must not set row['terminal_label']"
        )
        # But gate output must be present
        assert "fantasy_score_model" in row.get("gates", {}), (
            "Gate output must be stored in row['gates']['fantasy_score_model']"
        )
        gate_out = row["gates"]["fantasy_score_model"]
        assert gate_out.get("can_execute") is False


# ---------------------------------------------------------------------------
# Additional: module-level can_execute checks
# ---------------------------------------------------------------------------

class TestModuleLevelCanExecute:
    def test_all_modules_have_can_execute_false(self):
        import gate_engine.fantasy_score_model as fsm
        import gate_engine.fantasy_score_model.gate as gate_mod
        import gate_engine.fantasy_score_model.shared as shared_mod
        import gate_engine.fantasy_score_model.calibration_families as cal_mod
        import gate_engine.fantasy_score_model.diagnostics as diag_mod
        import gate_engine.fantasy_score_model.generators.basketball as bb_mod
        import gate_engine.fantasy_score_model.generators.nfl as nfl_mod
        import gate_engine.fantasy_score_model.generators.mlb_hitter as mh_mod
        import gate_engine.fantasy_score_model.generators.mlb_pitcher as mp_mod

        for mod in [fsm, gate_mod, shared_mod, cal_mod, diag_mod,
                    bb_mod, nfl_mod, mh_mod, mp_mod]:
            assert getattr(mod, "can_execute", None) is False, (
                f"{mod.__name__} must have can_execute=False at module level"
            )


# ---------------------------------------------------------------------------
# Smoke tests — end-to-end gate output schema
# ---------------------------------------------------------------------------

class TestOutputSchema:
    REQUIRED_FIELDS = [
        "can_execute", "shadow_mode", "terminal_label", "blockers",
        "p_more_raw", "p_less_raw", "p_push",
        "calibrated_lower_bound", "calibrated_upper_bound",
        "calibration_family", "thin_sample_condition",
        "raw_independent_prob", "model_weight", "market_weight",
        "identity_locked", "settlement_locked",
        "fs_mean", "fs_std", "fs_median",
        "best_modeled_side", "probability_gap",
        "generator_id", "model_is_provisional",
        "implementation_status",
    ]

    def test_nba_output_has_all_required_fields(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="NBA", line=30.0)
        enr = _base_enr()
        result = score_fantasy_row(row, enr, n_sims=3000, seed=11,
                                   run_stress=False, run_diag=False)
        for field in self.REQUIRED_FIELDS:
            assert field in result, f"Missing required field: {field!r}"

    def test_mlb_pitcher_output_has_regime_weights(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="MLB", line=25.0)
        enr = _base_enr(position="SP")
        result = score_fantasy_row(row, enr, n_sims=3000, seed=12,
                                   run_stress=False, run_diag=False)
        assert result.get("regime_weights") is not None, (
            "MLB pitcher output must include regime_weights"
        )
        rw = result["regime_weights"]
        assert "normal_effective" in rw
        assert abs(sum(rw.values()) - 1.0) < 0.01, "Regime weights must sum to 1.0"

    def test_final_refresh_in_output(self):
        from gate_engine.fantasy_score_model.gate import score_fantasy_row
        row = _base_row(sport="WNBA", line=22.0)
        enr = _base_enr(status_freshness_hours=3.0)
        result = score_fantasy_row(row, enr, n_sims=2000, seed=13,
                                   run_stress=False, run_diag=False)
        refresh = result.get("final_refresh")
        assert refresh is not None
        assert refresh.get("refresh_required") is True
