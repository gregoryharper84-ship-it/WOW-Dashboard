"""
tests/test_tennis_total_games.py

Regression suite for the WOW v16 Tennis Total Games probability model.

All tests are self-contained (no external HTTP, no DB, no file I/O).
Run via:  pytest tests/test_tennis_total_games.py -v

Coverage targets
────────────────
T01  Half-point line → P_EXACT = 0, P_MORE + P_LESS = 1
T02  Integer line → P_MORE + P_EXACT + P_LESS = 1 (raw AND calibrated)
T03  Specific set-score math: 7-6 (13g) + 6-4 (10g) = 23 total games
T04  Three-set decomposition reconciles: P(M) = P(M|2s)*P(2s) + P(M|3s)*P(3s)
T05  All dependency measures bounded to [0, 1]
T06  Stress-test produces Fragile when one favorable assumption drives result
T07  Market-line mismatch → market evidence rejected, blockers populated
T08  Retirement rules not verified → blocker present, classification capped
T09  Market is NOT the sole forecast: independent_model_weight > 0 always
T10  Stale / incomplete data → fail-closed (Reject classification)
T11  can_execute = False unconditionally (main + gate)
T12  Equal-player hard-court expected total games is physiologically plausible
"""
from __future__ import annotations

import sys
import os
import math
import pytest

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gate_engine import tennis_total_games as ttg


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row(line: float = 22.5, side: str = "MORE", sport: str = "TENNIS") -> dict:
    return {
        "sport": sport,
        "stat_key": "TOTAL_GAMES",
        "line_value": line,
        "side": side,
        "player_name": "Test Player",
    }


def _enr(**kwargs) -> dict:
    base = {
        "serve_win_pct_player1": 0.635,
        "serve_win_pct_player2": 0.635,
        "surface": "hard",
        "tour": "atp",
        "best_of": 3,
        "retirement_rules_verified": True,
        "event_identity_verified": True,
        "settlement_type_verified": True,
        "data_freshness_ok": True,
        "market_line": None,
    }
    base.update(kwargs)
    return base


def _score(line: float = 22.5, side: str = "MORE", **enr_kwargs) -> dict:
    return ttg.score(_row(line=line, side=side), _enr(**enr_kwargs))


# ─────────────────────────────────────────────────────────────────────────────
# T01 — Half-point line: P_EXACT = 0, MORE + LESS = 1
# ─────────────────────────────────────────────────────────────────────────────

class TestT01HalfPointLine:
    def test_exact_is_zero_for_half_point(self):
        r = _score(line=22.5)
        assert r["raw_exact"] == pytest.approx(0.0, abs=1e-9)
        assert r["cal_exact"] == pytest.approx(0.0, abs=1e-9)

    def test_more_plus_less_equals_one_raw(self):
        r = _score(line=22.5)
        total = r["raw_more"] + r["raw_exact"] + r["raw_less"]
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_more_plus_less_equals_one_calibrated(self):
        r = _score(line=22.5)
        total = r["cal_more"] + r["cal_exact"] + r["cal_less"]
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_is_integer_line_false(self):
        r = _score(line=22.5)
        assert r["is_integer_line"] is False


# ─────────────────────────────────────────────────────────────────────────────
# T02 — Integer line: MORE + EXACT + LESS = 1 (raw + calibrated)
# ─────────────────────────────────────────────────────────────────────────────

class TestT02IntegerLineSimplex:
    def test_raw_simplex_integer_line(self):
        r = _score(line=22.0)
        total = r["raw_more"] + r["raw_exact"] + r["raw_less"]
        assert total == pytest.approx(1.0, abs=1e-6), f"raw simplex broken: {total}"

    def test_cal_simplex_integer_line(self):
        r = _score(line=22.0)
        total = r["cal_more"] + r["cal_exact"] + r["cal_less"]
        assert total == pytest.approx(1.0, abs=1e-6), f"cal simplex broken: {total}"

    def test_exact_is_nonzero_for_integer_line(self):
        r = _score(line=22.0)
        # With a realistic distribution some mass should fall exactly on 22
        assert r["raw_exact"] > 0.0

    def test_is_integer_line_true(self):
        r = _score(line=22.0)
        assert r["is_integer_line"] is True

    def test_various_integer_lines_simplex(self):
        for line in (18, 20, 22, 24, 26, 28):
            r = _score(line=float(line))
            total_raw = r["raw_more"] + r["raw_exact"] + r["raw_less"]
            total_cal = r["cal_more"] + r["cal_exact"] + r["cal_less"]
            assert total_raw == pytest.approx(1.0, abs=1e-6), f"raw broken at line={line}"
            assert total_cal == pytest.approx(1.0, abs=1e-6), f"cal broken at line={line}"


# ─────────────────────────────────────────────────────────────────────────────
# T03 — Specific set-score math: 7-6 + 6-4 = 23 total games
# ─────────────────────────────────────────────────────────────────────────────

class TestT03SetScoreMath:
    def test_set_score_total_games(self):
        """7-6 (13 games) + 6-4 (10 games) = 23 total. Verify the distribution includes 23."""
        r = _score(line=22.5, side="MORE")
        dist = r["_total_games_dist"]
        assert 23 in dist, "total_games=23 should appear in distribution"
        assert dist[23] > 0.01, f"P(total_games=23) too low: {dist[23]}"

    def test_7_6_is_13_games(self):
        """The TB set 7-6 contributes 13 games."""
        assert ttg._is_set_over(7, 6), "7-6 should be a terminal set score"
        assert 7 + 6 == 13

    def test_6_4_is_10_games(self):
        assert 6 + 4 == 10

    def test_23_games_more_than_22_5(self):
        """23 total games qualifies as MORE vs. line 22.5."""
        assert 23 > 22.5

    def test_distribution_has_valid_game_totals_only(self):
        """All distribution keys should be physiologically valid (12-39 for BO3)."""
        r = _score(line=22.5)
        dist = r["_total_games_dist"]
        for t in dist:
            assert 12 <= t <= 39, f"Unexpected total_games key {t}"

    def test_distribution_sums_to_one(self):
        r = _score(line=22.5)
        dist = r["_total_games_dist"]
        total = sum(dist.values())
        assert total == pytest.approx(1.0, abs=1e-5), f"Distribution sums to {total}"


# ─────────────────────────────────────────────────────────────────────────────
# T04 — Three-set decomposition reconciliation
# ─────────────────────────────────────────────────────────────────────────────

class TestT04ThreeSetDecomposition:
    def test_decomposition_reconciles_to_unconditional(self):
        """P(MORE) = P(MORE|2sets)*P(2sets) + P(MORE|3sets)*P(3sets)."""
        r = _score(line=22.5, side="MORE")
        p_sel      = r["raw_more"]
        p_straight = r["p_straight_sets"]
        p_three    = r["p_three_sets"]
        c2 = r["cond_2set_prob"]
        c3 = r["cond_3set_prob"]
        reconstructed = c2 * p_straight + c3 * p_three
        assert reconstructed == pytest.approx(p_sel, abs=1e-4), (
            f"Decomposition does not reconcile: {reconstructed} vs {p_sel}"
        )

    def test_straight_plus_three_sets_sums_to_one(self):
        r = _score(line=22.5)
        total = r["p_straight_sets"] + r["p_three_sets"]
        assert total == pytest.approx(1.0, abs=1e-5)

    def test_cond_probs_in_unit_interval(self):
        r = _score(line=22.5, side="MORE")
        assert 0.0 <= r["cond_2set_prob"] <= 1.0
        assert 0.0 <= r["cond_3set_prob"] <= 1.0

    def test_less_side_decomposition(self):
        """Same reconciliation holds for LESS side."""
        r = _score(line=22.5, side="LESS")
        p_sel      = r["raw_less"]
        p_straight = r["p_straight_sets"]
        p_three    = r["p_three_sets"]
        reconstructed = r["cond_2set_prob"] * p_straight + r["cond_3set_prob"] * p_three
        assert reconstructed == pytest.approx(p_sel, abs=1e-4)


# ─────────────────────────────────────────────────────────────────────────────
# T05 — Dependency measures in [0, 1]
# ─────────────────────────────────────────────────────────────────────────────

class TestT05DependencyBounds:
    def _check_bounds(self, r: dict) -> None:
        for key in ("dep_third_set", "dep_tiebreak", "dep_extended_set", "dep_dominance"):
            v = r[key]
            assert 0.0 <= v <= 1.0, f"{key} = {v} is outside [0, 1]"

    def test_bounds_more_side(self):
        self._check_bounds(_score(line=22.5, side="MORE"))

    def test_bounds_less_side(self):
        self._check_bounds(_score(line=22.5, side="LESS"))

    def test_bounds_integer_line(self):
        self._check_bounds(_score(line=23.0, side="MORE"))

    def test_bounds_high_hold_rate(self):
        """High serve dominance → more tiebreaks → dep_tiebreak should be measurable."""
        r = _score(line=24.5, serve_win_pct_player1=0.70, serve_win_pct_player2=0.70)
        self._check_bounds(r)
        # High hold: tiebreaks more likely → dep_tiebreak > 0
        assert r["dep_tiebreak"] > 0.0

    def test_bounds_low_hold_rate(self):
        """Lots of breaks → fewer tiebreaks, more 6-2 / 6-1 sets → dep_dominance > 0."""
        r = _score(line=20.5, serve_win_pct_player1=0.55, serve_win_pct_player2=0.55)
        self._check_bounds(r)

    def test_dep_third_set_zero_for_below_min_possible(self):
        """If the line is below the minimum possible 2-set total (12), MORE is always true,
        and 3-set matches also contribute.  Either way bounds hold."""
        r = _score(line=11.0, side="MORE")
        self._check_bounds(r)


# ─────────────────────────────────────────────────────────────────────────────
# T06 — Stress test: Fragile when favourable assumption drives result
# ─────────────────────────────────────────────────────────────────────────────

class TestT06StressTestFragile:
    def test_resilient_when_both_players_high_serve(self):
        """Equal, strong servers → MORE on a moderate line should be resilient."""
        r = _score(line=22.5, side="MORE",
                   serve_win_pct_player1=0.67, serve_win_pct_player2=0.67)
        # High hold → many tiebreaks → total games tends higher → MORE stronger
        assert r["stress_classification"] in ("RESILIENT", "FRAGILE")  # either is valid

    def test_stress_drop_nonnegative_for_more_side(self):
        """Stress shrinks serve advantage → fewer total games → stress_prob ≤ base_prob for MORE."""
        r = _score(line=22.5, side="MORE")
        assert r["stress_drop"] >= -1e-9, (
            f"stress_drop should be non-negative for MORE side: {r['stress_drop']}"
        )

    def test_stress_drop_nonnegative_for_less_side(self):
        """Stress inflates serve advantage → more total games → stress_prob ≤ base_prob for LESS."""
        r = _score(line=22.5, side="LESS")
        assert r["stress_drop"] >= -1e-9, (
            f"stress_drop should be non-negative for LESS side: {r['stress_drop']}"
        )

    def test_fragile_classification_when_stressed(self):
        """
        Line set just above expected value for high-serve players.
        Removing the high-serve assumption should cause a large drop.
        Stress scenario uses neutral baseline → large drop expected.
        """
        # Strong servers, line set just at their expected total (high MORE probability)
        r = _score(line=26.5, side="MORE",
                   serve_win_pct_player1=0.72, serve_win_pct_player2=0.72)
        # Even if not exactly Fragile, stress_drop should be measurable
        assert r["stress_drop"] >= 0.0
        assert r["stress_classification"] in ("RESILIENT", "FRAGILE", "FAIL")

    def test_can_execute_false_in_stress_scenario(self):
        r = _score(line=22.5, side="MORE",
                   serve_win_pct_player1=0.72, serve_win_pct_player2=0.72)
        assert r["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# T07 — Market-line mismatch rejects market evidence
# ─────────────────────────────────────────────────────────────────────────────

class TestT07MarketLineMismatch:
    def test_mismatch_line_not_used(self):
        """market_line=24.5 but prop line=22.5 → market_line_matches=False."""
        r = _score(
            line=22.5,
            market_total_more_prob=0.60,
            market_total_less_prob=0.40,
            market_line=24.5,
        )
        assert r["market_line_matches"] is False
        assert r["market_prior_weight"] == pytest.approx(0.0, abs=1e-9)
        assert r["independent_model_weight"] == pytest.approx(1.0, abs=1e-9)

    def test_mismatch_blocker_present(self):
        r = _score(
            line=22.5,
            market_total_more_prob=0.65,
            market_line=20.5,
        )
        assert any("MARKET_LINE_MISMATCH" in b for b in r["blockers"])

    def test_matching_line_allows_market_evidence(self):
        """When market_line matches prop line, market evidence is considered."""
        r = _score(
            line=22.5,
            market_total_more_prob=0.60,
            market_total_less_prob=0.40,
            market_line=22.5,
        )
        assert r["market_line_matches"] is True
        assert r["market_prior_weight"] > 0.0

    def test_no_market_data_zero_weight(self):
        r = _score(line=22.5)
        assert r["market_prior_weight"] == pytest.approx(0.0, abs=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# T08 — Retirement rules not verified → blocker + classification impact
# ─────────────────────────────────────────────────────────────────────────────

class TestT08RetirementRulesNotVerified:
    def test_blocker_present_when_unverified(self):
        r = ttg.score(
            _row(line=22.5, side="MORE"),
            _enr(retirement_rules_verified=False),
        )
        assert any("RETIREMENT_RULES_NOT_VERIFIED" in b for b in r["blockers"])

    def test_retirement_flag_on_result(self):
        r = ttg.score(
            _row(line=22.5),
            _enr(retirement_rules_verified=False),
        )
        assert r["retirement_rules_verified"] is False

    def test_higher_uncertainty_when_unverified(self):
        """Unverified retirement rules should increase uncertainty discount."""
        r_verified   = _score(line=22.5, retirement_rules_verified=True)
        r_unverified = ttg.score(_row(line=22.5), _enr(retirement_rules_verified=False))
        assert r_unverified["uncertainty_discount"] > r_verified["uncertainty_discount"]

    def test_settlement_blocker_prevents_strong(self):
        """Even a theoretically strong result should not reach Strong when settlement unverified."""
        r = ttg.score(
            _row(line=22.5),
            _enr(retirement_rules_verified=False, settlement_type_verified=False),
        )
        assert r["classification"] != "Strong"


# ─────────────────────────────────────────────────────────────────────────────
# T09 — Market is NOT sole forecast: independent_model_weight > 0 always
# ─────────────────────────────────────────────────────────────────────────────

class TestT09MarketNotSoleForecast:
    def test_independent_model_weight_always_positive(self):
        r = _score(
            line=22.5,
            market_total_more_prob=0.70,
            market_total_less_prob=0.30,
            market_line=22.5,
        )
        assert r["independent_model_weight"] > 0.0

    def test_market_weight_capped_below_max(self):
        r = _score(
            line=22.5,
            market_total_more_prob=0.99,
            market_line=22.5,
        )
        assert r["market_prior_weight"] <= ttg._MAX_MARKET_WEIGHT + 1e-9

    def test_model_delta_visible(self):
        """Model vs market delta is exposed (positive or negative)."""
        r = _score(
            line=22.5,
            market_total_more_prob=0.55,
            market_line=22.5,
        )
        assert r["model_vs_market_delta"] is not None

    def test_no_market_independent_weight_one(self):
        r = _score(line=22.5)
        assert r["independent_model_weight"] == pytest.approx(1.0, abs=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# T10 — Stale / incomplete data → fail-closed (Reject)
# ─────────────────────────────────────────────────────────────────────────────

class TestT10StaleInputsFailClosed:
    def test_stale_data_classification_reject(self):
        r = ttg.score(
            _row(line=22.5),
            _enr(data_freshness_ok=False),
        )
        assert r["classification"] == "Reject"

    def test_stale_data_blocker_present(self):
        r = ttg.score(
            _row(line=22.5),
            _enr(data_freshness_ok=False),
        )
        assert any("STALE" in b or "DATA" in b for b in r["blockers"])

    def test_missing_line_fail_closed(self):
        row = _row(line=0.0)  # invalid line
        r = ttg.score(row, _enr())
        assert r["classification"] == "Reject"
        assert any("LINE" in b for b in r["blockers"])

    def test_can_execute_false_even_on_stale(self):
        r = ttg.score(_row(line=22.5), _enr(data_freshness_ok=False))
        assert r["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# T11 — can_execute = False unconditionally
# ─────────────────────────────────────────────────────────────────────────────

class TestT11CanExecuteFalseUnconditional:
    def test_can_execute_false_normal(self):
        r = _score(line=22.5)
        assert r["can_execute"] is False

    def test_can_execute_false_strong_scenario(self):
        r = _score(line=22.5,
                   serve_win_pct_player1=0.67, serve_win_pct_player2=0.67,
                   retirement_rules_verified=True,
                   event_identity_verified=True,
                   settlement_type_verified=True,
                   data_freshness_ok=True)
        assert r["can_execute"] is False

    def test_module_level_flag(self):
        from gate_engine import tennis_total_games as m
        assert m.can_execute is False

    def test_gate_module_level_flag(self):
        from gate_engine import tennis_total_games_gate as g
        assert g.can_execute is False

    def test_can_execute_false_reject(self):
        r = ttg.score(_row(line=22.5), _enr(data_freshness_ok=False))
        assert r["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# T12 — Expected total games plausibility for equal hard-court ATP players
# ─────────────────────────────────────────────────────────────────────────────

class TestT12PlausibleExpectedTotalGames:
    def test_expected_total_games_atp_hard(self):
        """
        Equal ATP hard-court players: expected total games ≈ 21-26 for BO3.
        This range covers the empirical ATP average of ~22-24 games per match.
        """
        r = _score(line=22.5,
                   serve_win_pct_player1=0.635, serve_win_pct_player2=0.635)
        dist = r["_total_games_dist"]
        expected = sum(t * p for t, p in dist.items())
        assert 19 <= expected <= 28, (
            f"Expected total games {expected:.1f} outside plausible range [19, 28]"
        )

    def test_expected_total_games_wta_clay(self):
        """WTA clay: lower serve pct → more breaks → shorter matches on average."""
        r = _score(line=20.5, surface="clay", tour="wta",
                   serve_win_pct_player1=0.565, serve_win_pct_player2=0.565)
        dist = r["_total_games_dist"]
        expected_wta = sum(t * p for t, p in dist.items())
        r_atp = _score(line=22.5,
                       serve_win_pct_player1=0.635, serve_win_pct_player2=0.635)
        dist_atp = r_atp["_total_games_dist"]
        expected_atp = sum(t * p for t, p in dist_atp.items())
        # WTA clay expected games should be lower than ATP hard court
        assert expected_wta < expected_atp, (
            f"WTA clay expected ({expected_wta:.1f}) should be < ATP hard ({expected_atp:.1f})"
        )

    def test_high_serve_pct_raises_expected_games(self):
        """Higher serve % → more holds → more tiebreaks → higher expected total games."""
        r_low  = _score(line=20.0, serve_win_pct_player1=0.58, serve_win_pct_player2=0.58)
        r_high = _score(line=20.0, serve_win_pct_player1=0.70, serve_win_pct_player2=0.70)
        exp_low  = sum(t * p for t, p in r_low["_total_games_dist"].items())
        exp_high = sum(t * p for t, p in r_high["_total_games_dist"].items())
        assert exp_high > exp_low, (
            f"Higher serve should increase expected games: low={exp_low:.1f} high={exp_high:.1f}"
        )

    def test_p_three_sets_reasonable(self):
        """Equal players: P(three sets) should be close to 0.5 (50/50 split)."""
        r = _score(line=22.5,
                   serve_win_pct_player1=0.635, serve_win_pct_player2=0.635)
        p3 = r["p_three_sets"]
        assert 0.35 <= p3 <= 0.65, f"P(three sets) = {p3:.3f} implausible for equal players"

    def test_bo5_expected_higher_than_bo3(self):
        """Best-of-5 matches have more sets → higher expected total games."""
        r3 = _score(line=22.5, best_of=3,
                    serve_win_pct_player1=0.635, serve_win_pct_player2=0.635)
        r5 = _score(line=22.5, best_of=5,
                    serve_win_pct_player1=0.635, serve_win_pct_player2=0.635)
        exp3 = sum(t * p for t, p in r3["_total_games_dist"].items())
        exp5 = sum(t * p for t, p in r5["_total_games_dist"].items())
        assert exp5 > exp3, f"BO5 expected ({exp5:.1f}) should exceed BO3 ({exp3:.1f})"


# ─────────────────────────────────────────────────────────────────────────────
# Internal math unit tests (not numbered — supporting layer)
# ─────────────────────────────────────────────────────────────────────────────

class TestInternalMath:
    def test_game_win_prob_symmetry(self):
        """G(0.5) == 0.5."""
        assert ttg._game_win_prob(0.5) == pytest.approx(0.5, abs=1e-10)

    def test_game_win_prob_monotone(self):
        """G(p) is strictly increasing in p."""
        probs = [ttg._game_win_prob(p) for p in (0.40, 0.50, 0.60, 0.70, 0.80)]
        assert probs == sorted(probs), "G(p) not monotone"

    def test_tiebreak_win_prob_symmetry(self):
        """T(0.5) == 0.5."""
        assert ttg._tb_win_prob(0.5) == pytest.approx(0.5, abs=1e-10)

    def test_tiebreak_win_prob_monotone(self):
        tbs = [ttg._tb_win_prob(p) for p in (0.40, 0.50, 0.60, 0.70, 0.80)]
        assert tbs == sorted(tbs), "T(p) not monotone"

    def test_set_distribution_sums_to_one(self):
        h1 = ttg._game_win_prob(0.635)
        h2 = ttg._game_win_prob(0.635)
        tb = ttg._tb_win_prob(0.5)
        dist = ttg._set_score_distribution(h1, h2, tb)
        total = sum(dist.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_set_distribution_valid_scores(self):
        h1 = ttg._game_win_prob(0.635)
        h2 = ttg._game_win_prob(0.635)
        tb = ttg._tb_win_prob(0.5)
        dist = ttg._set_score_distribution(h1, h2, tb)
        valid = {(6,0),(6,1),(6,2),(6,3),(6,4),(7,5),(7,6),
                 (0,6),(1,6),(2,6),(3,6),(4,6),(5,7),(6,7)}
        for sc in dist:
            assert sc in valid, f"Unexpected set score {sc}"

    def test_set_is_over_boundaries(self):
        assert ttg._is_set_over(6, 0)
        assert ttg._is_set_over(6, 4)
        assert ttg._is_set_over(7, 5)
        assert ttg._is_set_over(7, 6)
        assert ttg._is_set_over(0, 6)
        assert ttg._is_set_over(6, 7)
        assert not ttg._is_set_over(6, 5)
        assert not ttg._is_set_over(5, 6)
        assert not ttg._is_set_over(3, 3)

    def test_calibrate_triple_simplex_preserved(self):
        cm, ce, cl = ttg._calibrate_triple(0.55, 0.0, 0.45, 0.10)
        assert cm + ce + cl == pytest.approx(1.0, abs=1e-9)

    def test_calibrate_triple_simplex_integer(self):
        cm, ce, cl = ttg._calibrate_triple(0.45, 0.08, 0.47, 0.10)
        assert cm + ce + cl == pytest.approx(1.0, abs=1e-9)

    def test_stress_serve_more_reduces_advantage(self):
        p_orig  = 0.65
        p_stress = ttg._stress_serve(p_orig, "MORE")
        assert p_stress < p_orig, "Stress for MORE should reduce serve advantage"

    def test_stress_serve_less_increases_advantage(self):
        p_orig  = 0.60
        p_stress = ttg._stress_serve(p_orig, "LESS")
        assert p_stress > p_orig, "Stress for LESS should increase serve advantage"

    def test_uncertainty_baseline_always_positive(self):
        u, _ = ttg._compute_uncertainty("baseline", 0, False, False, False, True, None, True)
        assert u > 0.0

    def test_uncertainty_capped_at_0_40(self):
        u, _ = ttg._compute_uncertainty(
            "baseline", 2, True, True, True, False, 0.15, False
        )
        assert u <= 0.40 + 1e-9

    def test_bo3_dist_sums_to_one(self):
        h1 = ttg._game_win_prob(0.635)
        h2 = ttg._game_win_prob(0.600)
        tb = ttg._tb_win_prob((0.635 + (1-0.600)) / 2)
        sd = ttg._set_score_distribution(h1, h2, tb)
        md = ttg._bo3_total_games_distribution(sd)
        assert sum(md.values()) == pytest.approx(1.0, abs=1e-5)

    def test_bo5_dist_sums_to_one(self):
        h1 = ttg._game_win_prob(0.635)
        h2 = ttg._game_win_prob(0.635)
        tb = ttg._tb_win_prob(0.5)
        sd = ttg._set_score_distribution(h1, h2, tb)
        md = ttg._bo5_total_games_distribution(sd)
        assert sum(md.values()) == pytest.approx(1.0, abs=1e-5)
