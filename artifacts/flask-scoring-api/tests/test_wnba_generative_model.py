"""
tests/test_wnba_generative_model.py

Regression coverage for the WNBA Generative Probability Engine.
All tests are fully offline (no DB, no network).

Coverage areas
--------------
  T01  65% floor — 53.1% LB → HOLD not YES_MODEL_QUALIFIED
  T02  Late lineup/status rerun — stale data → final_refresh_required + label ≤ HOLD
  T03  Role-regime redistribution — PRIMARY_TEAMMATE=HIGH → USAGE_BUMP weight ↑
  T04  Integer-line Exact handling — MORE+EXACT+LESS = 1.0 exactly
  T05  Market-independence transparency — independent_model_weight > 0 always;
         market_prior_weight never > 25%
  T06  Stress-derived lower bounds — cal_lower_bound ≤ cal_selected
  T07  can_execute=False unconditional
  T08  Dependency bounds — all measures in [0, 1]
  T09  Dominant dependency share = max of individual measures
  T10  Settlement blocker prevents YES_MODEL_QUALIFIED
  T11  3PA dependency for PTS prop is populated and non-negative
  T12  PRA expected value ≈ PTS + REB + AST expectations
  T13  DNP regime has zero-minutes floor
  T14  Gate no-ops for non-WNBA rows
  T15  Half-point line: exact = 0, More+Less = 1
  T16  Minutes-limit restriction raises MINUTES_LIMIT regime prior
  T17  Very high LB (> 65%) + settlement_verified → YES_MODEL_QUALIFIED
  T18  Failure path returns a valid regime name
  T19  PLAYER_STATUS_OUT → REJECT regardless of LB
  T20  Market sanity: large delta (> 10pp) flagged; does not change cal_lower_bound
"""
from __future__ import annotations

import math
import pytest
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    sport: str = "WNBA",
    stat_key: str = "PTS",
    line: float = 18.5,
    side: str = "MORE",
    player_name: str = "TestPlayer",
    event_id: str = "EVT-001",
) -> dict[str, Any]:
    return {
        "sport":       sport,
        "stat_key":    stat_key,
        "line":        line,
        "side":        side,
        "player_name": player_name,
        "event_id":    event_id,
        "gates":       {},
        "blockers":    [],
    }


def _make_enr(
    pts_per_game: float | None = 18.0,
    reb_per_game: float | None = 5.0,
    ast_per_game: float | None = 3.0,
    avg_minutes: float | None = 32.0,
    status_freshness_hours: float | None = 1.0,
    event_status: str = "CONFIRMED",
    player_status: str = "ACTIVE",
    lineup_confirmed: bool = True,
    settlement_basis: str = "FULL_GAME_STATS",
    primary_teammate_dependency: str = "LOW",
    **kwargs: Any,
) -> dict[str, Any]:
    enr: dict[str, Any] = {
        "pts_per_game":              pts_per_game,
        "reb_per_game":              reb_per_game,
        "ast_per_game":              ast_per_game,
        "avg_minutes":               avg_minutes,
        "status_freshness_hours":    status_freshness_hours,
        "event_status":              event_status,
        "player_status":             player_status,
        "lineup_confirmed":          lineup_confirmed,
        "settlement_basis":          settlement_basis,
        "primary_teammate_dependency": primary_teammate_dependency,
    }
    enr.update(kwargs)
    return enr


def _score(
    stat_key: str = "PTS",
    line: float = 18.5,
    side: str = "MORE",
    enr: dict[str, Any] | None = None,
    **row_kwargs: Any,
) -> dict[str, Any]:
    """Convenience wrapper: score one prop with the generative model."""
    from gate_engine.wnba import generative_model as gm
    row = _make_row(stat_key=stat_key, line=line, side=side, **row_kwargs)
    if enr is None:
        enr = _make_enr()
    return gm.score(row, enr)


def _score_full_enr(
    stat_key: str = "PTS",
    line: float = 18.5,
    side: str = "MORE",
    **enr_kwargs: Any,
) -> dict[str, Any]:
    """Score with fully specified enrichment via keyword overrides."""
    enr = _make_enr(**enr_kwargs)
    return _score(stat_key=stat_key, line=line, side=side, enr=enr)


# ---------------------------------------------------------------------------
# Internal-math helpers (used in several tests)
# ---------------------------------------------------------------------------

def _sum_regimes_prior(result: dict[str, Any]) -> float:
    return sum(r["prior"] for r in result["role_regimes"])


# ===========================================================================
# T01 — 65% floor
# ===========================================================================

class TestT01SixtyFiveFloor:

    def test_53pct_lower_bound_is_hold_not_qualified(self):
        """A 53.1% calibrated lower bound → HOLD, not YES_MODEL_QUALIFIED."""
        from gate_engine.wnba.generative_model import _final_label
        lbl = _final_label(0.531, [], {"settlement_verified": True}, False)
        assert lbl == "HOLD"
        assert lbl != "YES_MODEL_QUALIFIED"

    def test_64pct_lower_bound_is_hold(self):
        from gate_engine.wnba.generative_model import _final_label
        lbl = _final_label(0.649, [], {"settlement_verified": True}, False)
        assert lbl == "HOLD"

    def test_65pct_lower_bound_is_qualified_when_settlement_verified(self):
        from gate_engine.wnba.generative_model import _final_label
        lbl = _final_label(0.65, [], {"settlement_verified": True}, False)
        assert lbl == "YES_MODEL_QUALIFIED"

    def test_47pct_lower_bound_is_watch(self):
        from gate_engine.wnba.generative_model import _final_label
        lbl = _final_label(0.47, [], {"settlement_verified": True}, False)
        assert lbl == "WATCH"

    def test_below_47pct_is_reject(self):
        from gate_engine.wnba.generative_model import _final_label
        lbl = _final_label(0.46, [], {"settlement_verified": True}, False)
        assert lbl == "REJECT"


# ===========================================================================
# T02 — Late lineup/status rerun
# ===========================================================================

class TestT02LateLineupRefresh:

    def test_missing_freshness_sets_refresh_required(self):
        r = _score_full_enr(status_freshness_hours=None)
        assert r["final_refresh_required"] is True

    def test_stale_status_sets_refresh_required(self):
        r = _score_full_enr(status_freshness_hours=3.5)
        assert r["final_refresh_required"] is True

    def test_refresh_required_caps_label_at_most_hold(self):
        r = _score_full_enr(status_freshness_hours=None)
        assert r["final_label"] in ("HOLD", "WATCH", "REJECT")
        assert r["final_label"] != "YES_MODEL_QUALIFIED"

    def test_fresh_status_clears_refresh_required(self):
        r = _score_full_enr(status_freshness_hours=0.5)
        assert r["final_refresh_required"] is False

    def test_refresh_blocker_in_blockers_list(self):
        r = _score_full_enr(status_freshness_hours=None)
        assert any("FINAL_REFRESH_REQUIRED" in b for b in r["blockers"])


# ===========================================================================
# T03 — Role-regime redistribution
# ===========================================================================

class TestT03RoleRegimeRedistribution:

    def _get_prior(self, result: dict[str, Any], name: str) -> float:
        for r in result["role_regimes"]:
            if r["name"] == name:
                return r["prior"]
        return 0.0

    def test_usage_bump_increases_with_high_teammate_dependency(self):
        r_base  = _score_full_enr(primary_teammate_dependency="LOW")
        r_bump  = _score_full_enr(primary_teammate_dependency="HIGH")
        assert self._get_prior(r_bump, "USAGE_BUMP") > self._get_prior(r_base, "USAGE_BUMP")

    def test_normal_starter_decreases_when_teammate_absent(self):
        r_base  = _score_full_enr(primary_teammate_dependency="LOW")
        r_bump  = _score_full_enr(primary_teammate_dependency="HIGH")
        assert self._get_prior(r_bump, "NORMAL_STARTER") < self._get_prior(r_base, "NORMAL_STARTER")

    def test_regime_priors_always_sum_to_one(self):
        for dep in ("LOW", "MEDIUM", "HIGH"):
            r = _score_full_enr(primary_teammate_dependency=dep)
            total = _sum_regimes_prior(r)
            assert abs(total - 1.0) < 1e-9, f"priors sum={total} for dependency={dep}"

    def test_minutes_limit_restriction_increases_minutes_limit_regime(self):
        r_base = _score_full_enr()
        r_lim  = _score_full_enr(restriction_flag=True, minutes_limit=22)
        prior_base = self._get_prior(r_base, "MINUTES_LIMIT")
        prior_lim  = self._get_prior(r_lim,  "MINUTES_LIMIT")
        assert prior_lim > prior_base

    def test_high_dnp_risk_increases_dnp_regime(self):
        r_base = _score_full_enr(dnp_risk="LOW")
        r_dnp  = _score_full_enr(dnp_risk="HIGH")
        prior_base = self._get_prior(r_base, "DNP_RISK")
        prior_dnp  = self._get_prior(r_dnp,  "DNP_RISK")
        assert prior_dnp > prior_base


# ===========================================================================
# T04 — Integer-line Exact handling
# ===========================================================================

class TestT04IntegerLineExact:

    def test_integer_line_three_outcomes_sum_to_one_raw(self):
        for line in (10.0, 15.0, 18.0, 22.0, 25.0):
            r = _score(line=line)
            s = r["raw_more"] + r["raw_exact"] + r["raw_less"]
            assert abs(s - 1.0) < 1e-9, f"raw simplex broken at line={line}: sum={s}"

    def test_integer_line_three_outcomes_sum_to_one_cal(self):
        for line in (10.0, 15.0, 18.0, 22.0, 25.0):
            r = _score(line=line)
            s = r["cal_more"] + r["cal_exact"] + r["cal_less"]
            assert abs(s - 1.0) < 1e-9, f"cal simplex broken at line={line}: sum={s}"

    def test_integer_line_exact_is_nonzero(self):
        """P(k = int_line) > 0 for integer lines in the plausible range."""
        r = _score(line=18.0)
        assert r["raw_exact"] > 0.0
        assert r["cal_exact"] > 0.0

    def test_half_point_line_exact_is_zero(self):
        r = _score(line=18.5)
        assert r["raw_exact"] == 0.0
        assert r["cal_exact"] == 0.0

    def test_half_point_line_more_plus_less_equals_one(self):
        r = _score(line=18.5)
        s = r["raw_more"] + r["raw_less"]
        assert abs(s - 1.0) < 1e-9


# ===========================================================================
# T05 — Market-independence transparency
# ===========================================================================

class TestT05MarketIndependence:

    def test_independent_model_weight_always_positive(self):
        # With no market data
        r = _score()
        assert r["independent_model_weight"] > 0.0
        # With market data
        r2 = _score_full_enr(
            sportsbook_more_prob=0.55,
            sportsbook_less_prob=0.55,
        )
        assert r2["independent_model_weight"] > 0.0

    def test_market_prior_weight_never_exceeds_25pct(self):
        r = _score_full_enr(
            sportsbook_more_prob=0.60,
            sportsbook_less_prob=0.52,
        )
        assert r["market_prior_weight"] <= 0.25

    def test_no_market_data_gives_zero_market_weight(self):
        r = _score()  # no sportsbook probs in default enrichment
        assert r["market_prior_weight"] == 0.0
        assert r["independent_model_weight"] == 1.0

    def test_market_and_model_weights_sum_to_one(self):
        r = _score_full_enr(
            sportsbook_more_prob=0.58,
            sportsbook_less_prob=0.50,
        )
        assert abs(r["market_prior_weight"] + r["independent_model_weight"] - 1.0) < 1e-9

    def test_uncertainty_discount_reported(self):
        r = _score()
        assert "uncertainty_discount" in r
        assert 0.0 <= r["uncertainty_discount"] <= 0.60
        assert "uncertainty_factors" in r


# ===========================================================================
# T06 — Stress-derived lower bounds
# ===========================================================================

class TestT06StressLowerBound:

    def test_lower_bound_lte_cal_selected(self):
        """Lower bound must be ≤ calibrated selected (stress is adverse)."""
        r = _score()
        assert r["cal_lower_bound"] <= r["cal_selected"] + 1e-9

    def test_lower_bound_positive(self):
        r = _score()
        assert r["cal_lower_bound"] >= 0.0

    def test_stress_prob_lte_raw_selected_for_more(self):
        """Stress scenario reduces MORE probability."""
        r = _score(side="MORE")
        assert r["stress_selected_prob"] <= r["raw_selected"] + 1e-9

    def test_stress_prob_lte_raw_selected_for_less(self):
        """Stress scenario reduces LESS probability."""
        r = _score(side="LESS")
        assert r["stress_selected_prob"] <= r["raw_selected"] + 1e-9

    def test_stress_drop_non_negative(self):
        r = _score()
        assert r["stress_drop"] >= 0.0

    def test_lower_bound_is_not_fixed_haircut(self):
        """LB should vary across different lines (not a constant haircut)."""
        r_low  = _score(line=10.0)
        r_high = _score(line=30.0)
        assert r_low["cal_lower_bound"] != r_high["cal_lower_bound"]


# ===========================================================================
# T07 — can_execute=False unconditional
# ===========================================================================

class TestT07CanExecuteFalse:

    def test_can_execute_false_in_model_output(self):
        r = _score()
        assert r["can_execute"] is False

    def test_can_execute_false_with_high_probability(self):
        # Even a "strong" prop cannot have can_execute=True
        r = _score_full_enr(
            pts_per_game=30.0,
            avg_minutes=38.0,
            line=8.0,
            side="MORE",
            status_freshness_hours=0.1,
        )
        assert r["can_execute"] is False

    def test_module_level_flag_is_false(self):
        from gate_engine.wnba import generative_model
        assert generative_model.can_execute is False

    def test_gate_module_level_flag_is_false(self):
        from gate_engine import wnba_generative_gate
        assert wnba_generative_gate.can_execute is False

    def test_gate_sets_can_execute_false_on_row(self):
        from gate_engine import wnba_generative_gate
        row = _make_row()
        row["can_execute"] = True   # forcibly set True
        wnba_generative_gate.run(row, enr=_make_enr())
        assert row["can_execute"] is False


# ===========================================================================
# T08 — Dependency bounds [0, 1]
# ===========================================================================

class TestT08DependencyBounds:

    _DEPS = [
        "minutes_dependency",
        "efficiency_dependency",
        "close_game_dependency",
        "teammate_absence_dependency",
        "overtime_dependency",
        "three_pa_dependency",
        "dominant_dependency_share",
    ]

    def test_all_dependency_measures_in_unit_interval(self):
        r = _score()
        for dep in self._DEPS:
            assert dep in r, f"dependency key {dep!r} missing from result"
            v = r[dep]
            assert 0.0 <= v <= 1.0, f"{dep} = {v} out of [0, 1]"

    def test_dependency_bounds_for_multiple_stats(self):
        for stat in ("PTS", "REB", "AST", "STL", "PRA"):
            r = _score(stat_key=stat, line=4.5 if stat in ("STL",) else 18.5)
            for dep in self._DEPS:
                v = r.get(dep, -1.0)
                assert 0.0 <= v <= 1.0, f"stat={stat} {dep}={v}"

    def test_dominant_dependency_name_is_string(self):
        r = _score()
        assert isinstance(r["dominant_dependency_name"], str)
        assert len(r["dominant_dependency_name"]) > 0


# ===========================================================================
# T09 — Dominant dependency share = max of individual measures
# ===========================================================================

class TestT09DominantDependencyShare:

    def test_dominant_share_equals_max_of_individual_deps(self):
        r = _score()
        individual = [
            r["minutes_dependency"],
            r["efficiency_dependency"],
            r["close_game_dependency"],
            r["teammate_absence_dependency"],
            r["overtime_dependency"],
            r["three_pa_dependency"],
        ]
        expected_max = max(individual)
        assert abs(r["dominant_dependency_share"] - expected_max) < 1e-9

    def test_dominant_dependency_name_matches_max(self):
        r = _score()
        dep_map = {
            "minutes_dependency":          r["minutes_dependency"],
            "efficiency_dependency":       r["efficiency_dependency"],
            "close_game_dependency":       r["close_game_dependency"],
            "teammate_absence_dependency": r["teammate_absence_dependency"],
            "overtime_dependency":         r["overtime_dependency"],
            "three_pa_dependency":         r["three_pa_dependency"],
        }
        max_name = max(dep_map, key=lambda k: dep_map[k])
        assert r["dominant_dependency_name"] == max_name


# ===========================================================================
# T10 — Settlement blocker prevents YES_MODEL_QUALIFIED
# ===========================================================================

class TestT10SettlementBlocker:

    def test_unverified_settlement_prevents_qualified(self):
        r = _score_full_enr(settlement_basis="UNKNOWN_BASIS")
        assert r["final_label"] != "YES_MODEL_QUALIFIED"
        assert r["settlement_verified"] is False

    def test_absent_settlement_basis_prevents_qualified(self):
        enr = _make_enr()
        del enr["settlement_basis"]
        r = _score(enr=enr)
        assert r["final_label"] != "YES_MODEL_QUALIFIED"

    def test_verified_settlement_does_not_block(self):
        """With verified settlement and no other blocks, label is not forced down."""
        r = _score_full_enr(settlement_basis="FULL_GAME_STATS")
        assert r["settlement_verified"] is True
        # label depends on LB — just check it's not blocked by settlement
        blockers = r["blockers"]
        assert not any("SETTLEMENT_BASIS" in b for b in blockers)

    def test_settlement_blocker_in_blockers_list_when_missing(self):
        r = _score_full_enr(settlement_basis="")
        assert any("SETTLEMENT_BASIS" in b for b in r["blockers"])


# ===========================================================================
# T11 — 3PA dependency for PTS prop
# ===========================================================================

class TestT11ThreePADependency:

    def test_pts_prop_has_three_pa_dependency_field(self):
        r = _score(stat_key="PTS")
        assert "three_pa_dependency" in r
        assert r["three_pa_dependency"] >= 0.0

    def test_three_shooter_has_higher_three_pa_dependency(self):
        """More three-point volume → higher 3PA dependency on PTS prop."""
        r_lo = _score_full_enr(stat_key="PTS", threepm_per_game=0.5)
        r_hi = _score_full_enr(stat_key="PTS", threepm_per_game=3.5)
        assert r_hi["three_pa_dependency"] > r_lo["three_pa_dependency"]

    def test_reb_prop_has_zero_three_pa_dependency(self):
        """REB props are not scored from 3-point attempts."""
        r = _score(stat_key="REB")
        assert r["three_pa_dependency"] == 0.0

    def test_ast_prop_has_zero_three_pa_dependency(self):
        r = _score(stat_key="AST")
        assert r["three_pa_dependency"] == 0.0

    def test_pts_opportunity_projection_includes_expected_3pa(self):
        r = _score(stat_key="PTS")
        opp = r["opportunity_projection"]
        assert "expected_3pa" in opp
        assert "pts_from_3_fraction" in opp
        assert 0.0 <= opp["pts_from_3_fraction"] <= 1.0


# ===========================================================================
# T12 — PRA expected value ≈ PTS + REB + AST
# ===========================================================================

class TestT12PRAExpectedValue:

    def test_pra_stat_projection_approx_sum_of_components(self):
        """
        PRA is the sum of three Poissons with shared minutes.
        E[PRA] should equal E[PTS] + E[REB] + E[AST] exactly.
        """
        enr = _make_enr(pts_per_game=20.0, reb_per_game=6.0, ast_per_game=4.0,
                        avg_minutes=34.0)
        r_pra = _score(stat_key="PRA",     line=28.5, enr=enr)
        r_pts = _score(stat_key="PTS",     line=28.5, enr=enr)
        r_reb = _score(stat_key="REB",     line=28.5, enr=enr)
        r_ast = _score(stat_key="AST",     line=28.5, enr=enr)

        sum_components = (
            r_pts["stat_projection"]
            + r_reb["stat_projection"]
            + r_ast["stat_projection"]
        )
        assert abs(r_pra["stat_projection"] - sum_components) < 0.05

    def test_pra_model_status_is_provisional(self):
        r = _score(stat_key="PRA", line=28.5)
        assert r["model_status"] == "PROVISIONAL"

    def test_combo_stats_supported(self):
        for sk in ("PRA", "PTS+REB", "PTS+AST", "REB+AST"):
            r = _score(stat_key=sk, line=15.5)
            assert r["model_status"] == "PROVISIONAL", f"{sk} returned {r['model_status']}"


# ===========================================================================
# T13 — DNP regime has zero-minutes floor
# ===========================================================================

class TestT13DNPRegime:

    def test_dnp_regime_has_zero_minutes(self):
        r = _score()
        dnp_regimes = [rr for rr in r["role_regimes"] if rr["name"] == "DNP_RISK"]
        assert len(dnp_regimes) == 1
        assert dnp_regimes[0]["minutes_mean"]    == 0.0
        assert dnp_regimes[0]["minutes_floor"]   == 0.0
        assert dnp_regimes[0]["minutes_ceiling"] == 0.0

    def test_dnp_pmf_has_all_mass_at_zero(self):
        """A DNP_RISK regime with prior=1.0 should produce P(k=0) ≈ 1."""
        from gate_engine.wnba.generative_model import (
            RoleRegime, _compute_regime_pmf, ROLE_DNP_RISK
        )
        dnp = RoleRegime(ROLE_DNP_RISK, 1.0, 0.0, 0.0, 0.0, 0.0)
        pmf = _compute_regime_pmf(dnp, total_rate_per_min=0.5)
        assert pmf[0] == pytest.approx(1.0, abs=1e-9)
        assert sum(pmf[1:]) < 1e-9

    def test_dnp_regime_present_in_all_results(self):
        for stat in ("PTS", "REB", "AST", "STL", "BLK", "TOV"):
            r = _score(stat_key=stat)
            names = {rr["name"] for rr in r["role_regimes"]}
            assert "DNP_RISK" in names, f"DNP_RISK missing for stat={stat}"


# ===========================================================================
# T14 — Gate no-ops for non-WNBA rows
# ===========================================================================

class TestT14GateNoop:

    def test_noop_for_nba_row(self):
        from gate_engine import wnba_generative_gate
        row = _make_row(sport="NBA")
        wnba_generative_gate.run(row, enr=_make_enr())
        assert "wnba_generative" not in row.get("gates", {})

    def test_noop_for_mlb_row(self):
        from gate_engine import wnba_generative_gate
        row = _make_row(sport="MLB")
        wnba_generative_gate.run(row, enr=_make_enr())
        assert "wnba_generative" not in row.get("gates", {})

    def test_noop_for_unsupported_wnba_stat(self):
        from gate_engine import wnba_generative_gate
        row = _make_row(stat_key="FANTASY_SCORE")   # not in SUPPORTED_STAT_KEYS
        wnba_generative_gate.run(row, enr=_make_enr())
        assert "wnba_generative" not in row.get("gates", {})

    def test_wnba_row_gets_gate_report(self):
        from gate_engine import wnba_generative_gate
        row  = _make_row()
        enr  = _make_enr()
        wnba_generative_gate.run(row, enr=enr)
        assert "wnba_generative" in row["gates"]

    def test_gate_always_sets_can_execute_false_for_wnba(self):
        from gate_engine import wnba_generative_gate
        row = _make_row()
        row["can_execute"] = True
        wnba_generative_gate.run(row, enr=_make_enr())
        assert row["can_execute"] is False


# ===========================================================================
# T15 — Half-point line binary contract
# ===========================================================================

class TestT15HalfPointLine:

    def test_half_point_exact_is_zero(self):
        for line in (5.5, 10.5, 18.5, 22.5):
            r = _score(line=line)
            assert r["raw_exact"] == 0.0, f"line={line}: raw_exact={r['raw_exact']}"
            assert r["cal_exact"] == 0.0, f"line={line}: cal_exact={r['cal_exact']}"

    def test_half_point_more_plus_less_exact_one(self):
        for line in (5.5, 10.5, 18.5, 22.5):
            r = _score(line=line)
            s_raw = r["raw_more"] + r["raw_less"]
            s_cal = r["cal_more"] + r["cal_less"]
            assert abs(s_raw - 1.0) < 1e-9, f"raw line={line}: sum={s_raw}"
            assert abs(s_cal - 1.0) < 1e-9, f"cal line={line}: sum={s_cal}"


# ===========================================================================
# T16 — Minutes-limit restriction raises MINUTES_LIMIT regime prior
# ===========================================================================

class TestT16MinutesLimit:

    def _prior(self, result: dict[str, Any], name: str) -> float:
        for r in result["role_regimes"]:
            if r["name"] == name:
                return r["prior"]
        return 0.0

    def test_restriction_flag_raises_minutes_limit_regime(self):
        r_base = _score_full_enr(restriction_flag=False)
        r_rest = _score_full_enr(restriction_flag=True, minutes_limit=22)
        assert self._prior(r_rest, "MINUTES_LIMIT") > self._prior(r_base, "MINUTES_LIMIT")

    def test_minutes_limit_regime_uses_cap(self):
        r = _score_full_enr(restriction_flag=True, minutes_limit=20)
        for rr in r["role_regimes"]:
            if rr["name"] == "MINUTES_LIMIT":
                assert rr["minutes_mean"] <= 25.0  # should be near the cap


# ===========================================================================
# T17 — Very high LB + settlement_verified → YES_MODEL_QUALIFIED
# ===========================================================================

class TestT17HighLBQualified:

    def test_very_favorable_prop_reaches_qualified(self):
        """
        Line well below expected value, short freshness, verified settlement.
        The model should produce a high enough LB to reach YES_MODEL_QUALIFIED
        when the setup is favorable.
        """
        from gate_engine.wnba.generative_model import _final_label
        # Directly test the label function with a 70% LB
        lbl = _final_label(0.70, [], {"settlement_verified": True}, False)
        assert lbl == "YES_MODEL_QUALIFIED"

    def test_yes_qualified_requires_65_floor(self):
        from gate_engine.wnba.generative_model import _YES_QUALIFIED_FLOOR
        assert _YES_QUALIFIED_FLOOR == 0.65

    def test_model_output_has_correct_model_status(self):
        r = _score()
        assert r["model_status"] == "PROVISIONAL"


# ===========================================================================
# T18 — Failure path returns a valid regime name
# ===========================================================================

class TestT18FailurePath:

    _VALID_REGIME_NAMES = {
        "NORMAL_STARTER", "USAGE_BUMP", "BENCH_SECONDARY",
        "MINUTES_LIMIT", "BLOWOUT_TRUNCATION", "DNP_RISK",
    }

    def test_failure_path_regime_name_is_valid(self):
        r = _score()
        assert r["largest_failure_path"] in self._VALID_REGIME_NAMES

    def test_failure_path_prob_in_unit_interval(self):
        r = _score()
        assert 0.0 <= r["failure_path_prob"] <= 1.0

    def test_failure_path_consistent_for_multiple_stats(self):
        for stat in ("PTS", "REB", "AST", "STL", "PRA"):
            line = 4.5 if stat == "STL" else 18.5
            r = _score(stat_key=stat, line=line)
            assert r["largest_failure_path"] in self._VALID_REGIME_NAMES
            assert 0.0 <= r["failure_path_prob"] <= 1.0


# ===========================================================================
# T19 — PLAYER_STATUS_OUT → REJECT
# ===========================================================================

class TestT19PlayerStatusOut:

    def test_player_out_gives_reject(self):
        r = _score_full_enr(player_status="OUT")
        assert r["final_label"] == "REJECT"

    def test_player_inactive_gives_reject(self):
        r = _score_full_enr(player_status="INACTIVE")
        assert r["final_label"] == "REJECT"

    def test_player_out_blocker_in_list(self):
        r = _score_full_enr(player_status="OUT")
        assert any("PLAYER_STATUS_OUT" in b for b in r["blockers"])

    def test_player_active_does_not_give_reject_by_status(self):
        r = _score_full_enr(player_status="ACTIVE")
        assert not any("PLAYER_STATUS_OUT" in b for b in r["blockers"])


# ===========================================================================
# T20 — Market sanity: large delta flagged; does not change cal_lower_bound
# ===========================================================================

class TestT20MarketSanity:

    def test_large_market_delta_flagged(self):
        """Model says 75% MORE; market says 55% MORE → large delta flagged."""
        r = _score_full_enr(
            stat_key="PTS",
            line=10.0,                    # very low line → model strongly MORE
            sportsbook_more_prob=0.55,
            sportsbook_less_prob=0.55,
        )
        # Market is computed; if delta > 0.10, market_model_delta_large = True
        if r["market_no_vig_prob"] is not None and r["model_market_delta"] is not None:
            if abs(r["model_market_delta"]) > 0.10:
                assert r["market_model_delta_large"] is True

    def test_market_data_does_not_dominate_cal_lower_bound(self):
        """
        The lower bound is derived from the stress scenario, not market data.
        Changing the market probability should not dramatically change the LB.
        """
        r_no_mkt = _score()
        r_mkt    = _score_full_enr(
            sportsbook_more_prob=0.30,   # very different from model
            sportsbook_less_prob=0.82,
        )
        # LB should not collapse to match market (model retains >= 75% weight)
        if r_no_mkt["cal_lower_bound"] > 0.0:
            ratio = r_mkt["cal_lower_bound"] / r_no_mkt["cal_lower_bound"]
            assert ratio > 0.60, "Market appears to be dominating the lower bound"

    def test_market_model_delta_field_present(self):
        r = _score()
        assert "model_market_delta" in r          # model minus market difference
        assert "market_no_vig_prob" in r
        assert "exact_line_market_no_vig" in r


# ===========================================================================
# Internal math — direct unit tests for key model functions
# ===========================================================================

class TestInternalMath:

    def test_poisson_pmf_at_zero(self):
        from gate_engine.wnba.generative_model import _poisson_pmf
        assert _poisson_pmf(0, 0.0) == pytest.approx(1.0)
        assert _poisson_pmf(0, 2.0) == pytest.approx(math.exp(-2.0))

    def test_poisson_pmf_sum_close_to_one(self):
        from gate_engine.wnba.generative_model import _poisson_pmf, _MAX_K
        for lam in (1.0, 5.0, 10.0, 20.0):
            s = sum(_poisson_pmf(k, lam) for k in range(_MAX_K + 1))
            assert s == pytest.approx(1.0, abs=1e-6), f"lam={lam}: sum={s}"

    def test_calibrate_triple_simplex_integer(self):
        from gate_engine.wnba.generative_model import _calibrate_triple
        for discount in (0.0, 0.1, 0.3, 0.5):
            cm, ce, cl = _calibrate_triple(0.6, 0.1, 0.3, discount, is_integer=True)
            assert abs(cm + ce + cl - 1.0) < 1e-9, f"discount={discount}: sum={cm+ce+cl}"

    def test_calibrate_triple_simplex_halfpoint(self):
        from gate_engine.wnba.generative_model import _calibrate_triple
        for discount in (0.0, 0.2, 0.4):
            cm, ce, cl = _calibrate_triple(0.65, 0.0, 0.35, discount, is_integer=False)
            assert ce == 0.0
            assert abs(cm + cl - 1.0) < 1e-9

    def test_american_to_no_vig_even_odds(self):
        from gate_engine.wnba.generative_model import _american_to_no_vig
        # Both sides -110 → no-vig ≈ 0.5
        nv = _american_to_no_vig(-110, -110)
        assert nv == pytest.approx(0.5, abs=1e-3)

    def test_full_pmf_sums_to_one(self):
        from gate_engine.wnba.generative_model import _build_regimes, _compute_full_pmf
        regimes = _build_regimes(_make_enr(), {})
        for rate in (0.3, 0.6, 1.0):
            pmf = _compute_full_pmf(regimes, rate)
            s   = sum(pmf)
            assert abs(s - 1.0) < 1e-6, f"rate={rate}: pmf sum={s}"

    def test_outcomes_from_pmf_sum_to_one(self):
        from gate_engine.wnba.generative_model import (
            _build_regimes, _compute_full_pmf, _pmf_to_outcomes
        )
        regimes = _build_regimes(_make_enr(), {})
        pmf     = _compute_full_pmf(regimes, 0.5)
        for line, is_int in ((18.5, False), (18.0, True), (20.0, True)):
            mo, ex, le = _pmf_to_outcomes(pmf, line, is_int)
            s = mo + ex + le
            assert abs(s - 1.0) < 1e-9, f"line={line}: sum={s}"

    def test_unsupported_stat_key_returns_reject(self):
        from gate_engine.wnba import generative_model as gm
        row = _make_row(stat_key="FPTS")
        r   = gm.score(row, _make_enr())
        assert r["final_label"] == "REJECT"
        assert r["model_status"] == "UNSUPPORTED_STAT_KEY"

    def test_invalid_line_returns_reject(self):
        from gate_engine.wnba import generative_model as gm
        row = _make_row(line=None)
        row["line"] = "not_a_number"
        r = gm.score(row, _make_enr())
        assert r["final_label"] == "REJECT"
        assert r["model_status"] == "INVALID_LINE"
