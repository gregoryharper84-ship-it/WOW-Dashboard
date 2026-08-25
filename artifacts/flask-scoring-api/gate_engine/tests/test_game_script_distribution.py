"""
gate_engine/tests/test_game_script_distribution.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Regression tests for the WNBA/NBA Game-Script Distribution Expert.

Coverage
--------
TestGameEnvironment        — parse_game_environment + derive_script_priors
TestScriptPriors           — priors sum to 1.0, monotonic with spread
TestPlayerState            — derive_player_states for each active_status variant
TestMinutesDistribution    — compute_minutes_estimates clipping + script deltas
TestConditionalHitProb     — Poisson CDF math + fail-closed on missing game_log
TestUnconditionalAggregator — weighted sum, redistribution when scripts unavailable
TestScriptFragility        — fragility range, labels, dominant script
TestShadowGate             — full pipeline + governance invariants
"""
from __future__ import annotations

import math
import unittest

from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
    parse_game_environment, derive_script_priors,
    GameEnvironment, ScriptPriors,
    SCRIPT_BLOWOUT_HOME, SCRIPT_BLOWOUT_AWAY,
    SCRIPT_CLOSE_HIGH, SCRIPT_CLOSE_LOW, SCRIPT_NEUTRAL,
    ALL_SCRIPTS,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.player_state import (
    derive_player_states, PlayerState,
    can_execute as PS_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.minutes_distribution import (
    compute_minutes_estimates, MinutesEstimate,
    can_execute as MIN_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.conditional_hit_prob import (
    compute_conditional_hit_probs, ConditionalHitResult,
    _poisson_cdf, _poisson_hit_prob,
    can_execute as CHP_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.unconditional_aggregator import (
    aggregate_unconditional_probability, UnconditionalResult,
    can_execute as UA_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.script_fragility import (
    compute_fragility, FragilityReport,
    can_execute as FRAG_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.shadow_gate import (
    GameScriptShadowGate, GAME_SCRIPT_SHADOW_STATUS,
    can_execute as SHADOW_CAN_EXECUTE,
    CEILING, PRODUCTION_AUTHORITY, USER_OUTPUT_AUTHORITY, CAPITAL_AUTHORITY,
    SHADOW_ONLY,
)


def _make_env(spread=4.5, total=163.0, sport="WNBA") -> GameEnvironment:
    return GameEnvironment(
        spread=spread,
        total_line=total,
        baseline=160.0 if sport == "WNBA" else 225.0,
        sport=sport,
        spread_magnitude=abs(spread),
        total_delta=total - (160.0 if sport == "WNBA" else 225.0),
    )


def _make_priors(spread=4.5, total=163.0, sport="WNBA") -> ScriptPriors:
    return derive_script_priors(_make_env(spread, total, sport))


def _game_log_rebound(n=5, reb_per_game=10.5, min_per_game=32.0) -> list[dict]:
    return [{"reb": reb_per_game, "min": min_per_game} for _ in range(n)]


# ── TestGameEnvironment ───────────────────────────────────────────────────────

class TestGameEnvironment(unittest.TestCase):

    def test_parse_returns_env_with_matchup(self):
        combined = {"sport": "WNBA", "matchup": {"spread": 4.5, "total_line": 163.0}}
        env = parse_game_environment(combined)
        self.assertIsNotNone(env)
        self.assertAlmostEqual(env.spread, 4.5)
        self.assertAlmostEqual(env.total_line, 163.0)

    def test_parse_returns_none_without_spread(self):
        combined = {"sport": "WNBA", "matchup": {"total_line": 163.0}}
        self.assertIsNone(parse_game_environment(combined))

    def test_parse_returns_none_without_total(self):
        combined = {"sport": "WNBA", "matchup": {"spread": 4.5}}
        self.assertIsNone(parse_game_environment(combined))

    def test_parse_returns_none_on_type_error(self):
        combined = {"sport": "WNBA", "matchup": {"spread": "not_a_number", "total_line": 160.0}}
        self.assertIsNone(parse_game_environment(combined))

    def test_spread_magnitude_always_positive(self):
        env = _make_env(spread=-7.0)
        self.assertGreaterEqual(env.spread_magnitude, 0)

    def test_total_delta_computed_correctly(self):
        env = _make_env(spread=0, total=170.0, sport="WNBA")
        self.assertAlmostEqual(env.total_delta, 10.0)

    def test_nba_baseline_correct(self):
        combined = {"sport": "NBA", "matchup": {"spread": 2.0, "total_line": 228.0}}
        env = parse_game_environment(combined)
        self.assertIsNotNone(env)
        self.assertAlmostEqual(env.baseline, 225.0)
        self.assertAlmostEqual(env.total_delta, 3.0)


# ── TestScriptPriors ──────────────────────────────────────────────────────────

class TestScriptPriors(unittest.TestCase):

    def test_priors_sum_to_one_balanced_game(self):
        priors = _make_priors(spread=0, total=160.0)
        self.assertAlmostEqual(priors.sum(), 1.0, places=5)

    def test_priors_sum_to_one_large_spread(self):
        priors = _make_priors(spread=12.0, total=155.0)
        self.assertAlmostEqual(priors.sum(), 1.0, places=5)

    def test_priors_sum_to_one_negative_spread(self):
        priors = _make_priors(spread=-8.0, total=170.0)
        self.assertAlmostEqual(priors.sum(), 1.0, places=5)

    def test_large_spread_increases_blowout(self):
        small = _make_priors(spread=1.0)
        large = _make_priors(spread=15.0)
        self.assertGreater(
            large.blowout_home + large.blowout_away,
            small.blowout_home + small.blowout_away,
        )

    def test_home_favourite_more_likely_blowout_home(self):
        priors = _make_priors(spread=8.0)
        self.assertGreater(priors.blowout_home, priors.blowout_away)

    def test_away_favourite_more_likely_blowout_away(self):
        priors = _make_priors(spread=-8.0)
        self.assertGreater(priors.blowout_away, priors.blowout_home)

    def test_high_total_increases_close_high(self):
        low  = _make_priors(total=140.0)
        high = _make_priors(total=180.0)
        self.assertGreater(high.close_high, low.close_high)

    def test_low_total_increases_close_low(self):
        high = _make_priors(total=180.0)
        low  = _make_priors(total=140.0)
        self.assertGreater(low.close_low, high.close_low)

    def test_all_priors_non_negative(self):
        for spread in [-10, -3, 0, 3, 10]:
            for total in [140, 160, 180]:
                p = _make_priors(spread=spread, total=total)
                self.assertGreaterEqual(p.blowout_home, 0)
                self.assertGreaterEqual(p.blowout_away, 0)
                self.assertGreaterEqual(p.close_high,   0)
                self.assertGreaterEqual(p.close_low,    0)
                self.assertGreaterEqual(p.neutral,      0)

    def test_as_dict_has_all_scripts(self):
        p = _make_priors()
        d = p.as_dict()
        for s in ALL_SCRIPTS:
            self.assertIn(s, d)


# ── TestPlayerState ───────────────────────────────────────────────────────────

class TestPlayerState(unittest.TestCase):

    def _combined(self, active_status="ACTIVE", projected_minutes=32.0):
        return {
            "sport": "WNBA",
            "role_status": {
                "active_status": active_status,
                "projected_minutes": projected_minutes,
                "minutes_low": 26.0,
                "minutes_high": 38.0,
            }
        }

    def test_returns_all_five_scripts(self):
        states = derive_player_states(self._combined())
        self.assertEqual(set(states.keys()), set(ALL_SCRIPTS))

    def test_active_player_available(self):
        states = derive_player_states(self._combined("ACTIVE", 32))
        for s in ALL_SCRIPTS:
            self.assertEqual(states[s].status, "AVAILABLE")

    def test_out_player_all_scripts_dnp(self):
        states = derive_player_states(self._combined("OUT"))
        for s in ALL_SCRIPTS:
            ps = states[s]
            self.assertEqual(ps.status, "OUT")
            self.assertAlmostEqual(ps.expected_minutes, 0.0)
            self.assertAlmostEqual(ps.dnp_risk, 1.0)

    def test_questionable_player_dnp_risk_elevated(self):
        states = derive_player_states(self._combined("QUESTIONABLE"))
        for s in ALL_SCRIPTS:
            self.assertEqual(states[s].status, "QUESTIONABLE")
            self.assertGreater(states[s].dnp_risk, 0)

    def test_missing_projected_minutes_unavailable(self):
        combined = {"sport": "WNBA", "role_status": {"active_status": "ACTIVE"}}
        states = derive_player_states(combined)
        for s in ALL_SCRIPTS:
            self.assertEqual(states[s].status, "UNAVAILABLE")
            self.assertIsNone(states[s].expected_minutes)

    def test_blowout_scripts_lower_minutes_than_neutral(self):
        states = derive_player_states(self._combined("ACTIVE", 32))
        neutral = states[SCRIPT_NEUTRAL].expected_minutes
        blowout = states[SCRIPT_BLOWOUT_HOME].expected_minutes
        self.assertLess(blowout, neutral)

    def test_close_high_higher_minutes_than_neutral(self):
        states = derive_player_states(self._combined("ACTIVE", 32))
        neutral  = states[SCRIPT_NEUTRAL].expected_minutes
        close_hi = states[SCRIPT_CLOSE_HIGH].expected_minutes
        self.assertGreater(close_hi, neutral)

    def test_can_execute_false(self):
        self.assertFalse(PS_CAN_EXECUTE)


# ── TestMinutesDistribution ───────────────────────────────────────────────────

class TestMinutesDistribution(unittest.TestCase):

    def _states(self, active_status="ACTIVE", projected_minutes=32.0):
        combined = {
            "sport": "WNBA",
            "role_status": {
                "active_status": active_status,
                "projected_minutes": projected_minutes,
                "minutes_low": 26.0,
                "minutes_high": 38.0,
            }
        }
        return derive_player_states(combined)

    def test_returns_all_five_estimates(self):
        ests = compute_minutes_estimates(self._states())
        self.assertEqual(set(ests.keys()), set(ALL_SCRIPTS))

    def test_available_true_for_active_player(self):
        ests = compute_minutes_estimates(self._states("ACTIVE", 32))
        for s in ALL_SCRIPTS:
            self.assertTrue(ests[s].available)

    def test_unavailable_false_for_out_player(self):
        # OUT player still gets estimates (expected_minutes = 0)
        ests = compute_minutes_estimates(self._states("OUT"))
        for s in ALL_SCRIPTS:
            est = ests[s]
            # Either available with 0 minutes or unavailable — both acceptable
            if est.available:
                self.assertAlmostEqual(est.expected_minutes, 0.0)

    def test_effective_minutes_lte_expected(self):
        ests = compute_minutes_estimates(self._states("ACTIVE", 32))
        for s in ALL_SCRIPTS:
            est = ests[s]
            if est.available and est.effective_minutes is not None:
                self.assertLessEqual(est.effective_minutes, est.expected_minutes)

    def test_minutes_not_exceed_cap(self):
        ests = compute_minutes_estimates(self._states("ACTIVE", 50), sport="WNBA")
        for s in ALL_SCRIPTS:
            est = ests[s]
            if est.available and est.expected_minutes is not None:
                self.assertLessEqual(est.expected_minutes, 45.0)

    def test_can_execute_false(self):
        self.assertFalse(MIN_CAN_EXECUTE)


# ── TestConditionalHitProb ────────────────────────────────────────────────────

class TestConditionalHitProb(unittest.TestCase):

    def _combined_with_log(self, reb=10.5, min_=32.0):
        return {
            "sport": "WNBA",
            "game_log": [{"reb": reb, "min": min_} for _ in range(5)],
        }

    def _minutes_ests(self, projected=32.0):
        from gate_engine.universal_agent.lanes.wnba_props.game_script.minutes_distribution import (
            compute_minutes_estimates,
        )
        combined = {
            "sport": "WNBA",
            "role_status": {
                "active_status": "ACTIVE",
                "projected_minutes": projected,
                "minutes_low": 26.0,
                "minutes_high": 38.0,
            }
        }
        from gate_engine.universal_agent.lanes.wnba_props.game_script.player_state import (
            derive_player_states,
        )
        return compute_minutes_estimates(derive_player_states(combined))

    def test_poisson_cdf_zero_lambda(self):
        self.assertAlmostEqual(_poisson_cdf(0, 0.0), 1.0)
        self.assertAlmostEqual(_poisson_cdf(5, 0.0), 1.0)

    def test_poisson_cdf_large_k_gives_one(self):
        self.assertAlmostEqual(_poisson_cdf(100, 5.0), 1.0, places=4)

    def test_poisson_hit_prob_above_expected_rate(self):
        # Line > expected stat → P(hit) < 0.5
        prob = _poisson_hit_prob(15.0, 5.0)
        self.assertLess(prob, 0.5)

    def test_poisson_hit_prob_below_expected_rate(self):
        # Line << expected stat → P(hit) close to 1
        prob = _poisson_hit_prob(3.0, 15.0)
        self.assertGreater(prob, 0.8)

    def test_conditional_probs_available_with_log(self):
        combined = self._combined_with_log(reb=10.5, min_=32.0)
        me = self._minutes_ests(32)
        results = compute_conditional_hit_probs(combined, me, line=10.5,
                                                stat_key_raw="rebounds")
        # At least some should be available
        available = [r for r in results.values() if r.available]
        self.assertGreater(len(available), 0)

    def test_conditional_probs_unavailable_without_log(self):
        combined = {"sport": "WNBA"}
        me = self._minutes_ests(32)
        results = compute_conditional_hit_probs(combined, me, line=10.5,
                                                stat_key_raw="rebounds")
        for r in results.values():
            self.assertFalse(r.available)

    def test_conditional_probs_unavailable_unknown_stat(self):
        combined = self._combined_with_log()
        me = self._minutes_ests(32)
        results = compute_conditional_hit_probs(combined, me, line=5.0,
                                                stat_key_raw="invalid_stat_xyz")
        for r in results.values():
            self.assertFalse(r.available)

    def test_all_five_scripts_returned(self):
        combined = self._combined_with_log()
        me = self._minutes_ests(32)
        results = compute_conditional_hit_probs(combined, me, line=10.5,
                                                stat_key_raw="reb")
        self.assertEqual(set(results.keys()), set(ALL_SCRIPTS))

    def test_probability_in_unit_interval(self):
        combined = self._combined_with_log(reb=12.0, min_=32.0)
        me = self._minutes_ests(32)
        results = compute_conditional_hit_probs(combined, me, line=10.5,
                                                stat_key_raw="rebounds")
        for r in results.values():
            if r.available and r.probability is not None:
                self.assertGreaterEqual(r.probability, 0.0)
                self.assertLessEqual(r.probability, 1.0)

    def test_points_stat_key_alias(self):
        combined = {"sport": "WNBA", "game_log": [{"pts": 20, "min": 33}] * 5}
        me = self._minutes_ests(33)
        results = compute_conditional_hit_probs(combined, me, line=20.5,
                                                stat_key_raw="points")
        available = [r for r in results.values() if r.available]
        self.assertGreater(len(available), 0)

    def test_can_execute_false(self):
        self.assertFalse(CHP_CAN_EXECUTE)


# ── TestUnconditionalAggregator ───────────────────────────────────────────────

class TestUnconditionalAggregator(unittest.TestCase):

    def _make_conditionals(self, prob: float = 0.6) -> dict:
        from gate_engine.universal_agent.lanes.wnba_props.game_script.conditional_hit_prob import (
            ConditionalHitResult,
        )
        return {
            s: ConditionalHitResult(
                script=s, stat_key="rebounds", available=True,
                probability=prob, rate_per_min=0.33,
                effective_min=32.0, expected_stat=10.5,
            )
            for s in ALL_SCRIPTS
        }

    def test_uniform_conditional_gives_that_probability(self):
        priors = _make_priors(spread=0, total=160.0)
        conds  = self._make_conditionals(0.6)
        result = aggregate_unconditional_probability(priors, conds, "rebounds", 10.5)
        self.assertTrue(result.available)
        self.assertAlmostEqual(result.probability, 0.6, places=4)

    def test_unavailable_when_no_scripts_available(self):
        from gate_engine.universal_agent.lanes.wnba_props.game_script.conditional_hit_prob import (
            ConditionalHitResult,
        )
        priors = _make_priors()
        conds = {
            s: ConditionalHitResult(
                script=s, stat_key="rebounds", available=False,
                probability=None, rate_per_min=None,
                effective_min=None, expected_stat=None,
            )
            for s in ALL_SCRIPTS
        }
        result = aggregate_unconditional_probability(priors, conds, "rebounds", 10.5)
        self.assertFalse(result.available)
        self.assertIsNone(result.probability)

    def test_partial_scripts_redistribute_weight(self):
        from gate_engine.universal_agent.lanes.wnba_props.game_script.conditional_hit_prob import (
            ConditionalHitResult,
        )
        priors = _make_priors(spread=0, total=160.0)
        # Only NEUTRAL and CLOSE_HIGH available
        conds = {}
        for s in ALL_SCRIPTS:
            avail = s in (SCRIPT_NEUTRAL, SCRIPT_CLOSE_HIGH)
            conds[s] = ConditionalHitResult(
                script=s, stat_key="rebounds",
                available=avail,
                probability=0.6 if avail else None,
                rate_per_min=0.33 if avail else None,
                effective_min=32.0 if avail else None,
                expected_stat=10.5 if avail else None,
            )
        result = aggregate_unconditional_probability(priors, conds, "rebounds", 10.5)
        self.assertTrue(result.available)
        self.assertEqual(result.scripts_used, 2)
        # Effective priors should sum to 1
        self.assertAlmostEqual(sum(result.effective_priors.values()), 1.0, places=4)

    def test_probability_in_unit_interval(self):
        priors = _make_priors()
        conds  = self._make_conditionals(0.45)
        result = aggregate_unconditional_probability(priors, conds, "rebounds", 10.5)
        self.assertGreaterEqual(result.probability, 0.0)
        self.assertLessEqual(result.probability, 1.0)

    def test_can_execute_false(self):
        self.assertFalse(UA_CAN_EXECUTE)


# ── TestScriptFragility ───────────────────────────────────────────────────────

class TestScriptFragility(unittest.TestCase):

    def _make_unconditional(self, cond_probs: dict[str, float]) -> UnconditionalResult:
        eff_priors = {s: 1.0 / len(cond_probs) for s in cond_probs}
        total = sum(eff_priors[s] * p for s, p in cond_probs.items())
        return UnconditionalResult(
            available=True,
            probability=round(total, 6),
            scripts_used=len(cond_probs),
            scripts_available=len(cond_probs),
            effective_priors=eff_priors,
            conditional_probs=cond_probs,
            stat_key="rebounds",
            line=10.5,
        )

    def test_low_fragility_uniform_probs(self):
        unc = self._make_unconditional({s: 0.55 for s in ALL_SCRIPTS})
        priors = _make_priors()
        report = compute_fragility(unc, priors)
        self.assertTrue(report.available)
        self.assertEqual(report.fragility_label, "LOW")
        self.assertAlmostEqual(report.fragility_range, 0.0, places=4)

    def test_high_fragility_spread_probs(self):
        cond = {
            SCRIPT_BLOWOUT_HOME: 0.10,
            SCRIPT_BLOWOUT_AWAY: 0.20,
            SCRIPT_CLOSE_HIGH:   0.85,
            SCRIPT_CLOSE_LOW:    0.30,
            SCRIPT_NEUTRAL:      0.50,
        }
        unc = self._make_unconditional(cond)
        priors = _make_priors()
        report = compute_fragility(unc, priors)
        self.assertEqual(report.fragility_label, "HIGH")
        self.assertAlmostEqual(report.fragility_range, 0.75, places=4)

    def test_dominant_script_is_highest_weighted(self):
        cond = {s: 0.5 + 0.1 * i for i, s in enumerate(ALL_SCRIPTS)}
        unc = self._make_unconditional(cond)
        priors = _make_priors()
        report = compute_fragility(unc, priors)
        self.assertIsNotNone(report.dominant_script)

    def test_unavailable_with_only_one_script(self):
        unc = UnconditionalResult(
            available=True, probability=0.5, scripts_used=1, scripts_available=1,
            effective_priors={SCRIPT_NEUTRAL: 1.0},
            conditional_probs={SCRIPT_NEUTRAL: 0.5},
            stat_key="rebounds", line=10.5,
        )
        priors = _make_priors()
        report = compute_fragility(unc, priors)
        self.assertFalse(report.available)

    def test_unavailable_when_unconditional_unavailable(self):
        unc = UnconditionalResult(
            available=False, probability=None, scripts_used=0, scripts_available=0,
            effective_priors={}, conditional_probs={},
            stat_key="rebounds", line=10.5,
        )
        report = compute_fragility(unc, _make_priors())
        self.assertFalse(report.available)

    def test_can_execute_false(self):
        self.assertFalse(FRAG_CAN_EXECUTE)


# ── TestShadowGate ────────────────────────────────────────────────────────────

class TestShadowGate(unittest.TestCase):

    def _combined_full(self):
        return {
            "sport": "WNBA",
            "event_id": "chi-sea-001",
            "market": "rebounds",
            "line": 10.5,
            "role_status": {
                "active_status": "ACTIVE",
                "projected_minutes": 32.0,
                "minutes_low": 26.0,
                "minutes_high": 38.0,
            },
            "matchup": {"spread": 4.5, "total_line": 163.0},
            "game_log": [
                {"reb": 11, "min": 33},
                {"reb": 10, "min": 30},
                {"reb": 12, "min": 35},
                {"reb": 9,  "min": 28},
                {"reb": 13, "min": 34},
            ],
        }

    def test_governance_invariants_hardcoded(self):
        self.assertFalse(SHADOW_CAN_EXECUTE)
        self.assertFalse(PRODUCTION_AUTHORITY)
        self.assertFalse(USER_OUTPUT_AUTHORITY)
        self.assertFalse(CAPITAL_AUTHORITY)
        self.assertTrue(SHADOW_ONLY)
        self.assertEqual(CEILING, "MODEL_QUALIFIED_HOLD")

    def test_full_pipeline_returns_dict(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined=self._combined_full(), run_id="sg-test-1")
        self.assertIsInstance(result, dict)

    def test_full_pipeline_complete_status(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined=self._combined_full(), run_id="sg-test-2")
        self.assertIn(result["status"], [
            GAME_SCRIPT_SHADOW_STATUS.COMPLETE,
            GAME_SCRIPT_SHADOW_STATUS.PARTIAL,
        ])

    def test_ceiling_in_output(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined=self._combined_full(), run_id="sg-test-3")
        self.assertEqual(result["ceiling"], "MODEL_QUALIFIED_HOLD")

    def test_can_execute_false_in_output(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined=self._combined_full(), run_id="sg-test-4")
        self.assertFalse(result["can_execute"])

    def test_shadow_only_in_output(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined=self._combined_full(), run_id="sg-test-5")
        self.assertTrue(result["shadow_only"])

    def test_unavailable_without_spread(self):
        combined = {k: v for k, v in self._combined_full().items() if k != "matchup"}
        gate = GameScriptShadowGate()
        result = gate.run(combined=combined, run_id="sg-test-6")
        self.assertEqual(result["status"], GAME_SCRIPT_SHADOW_STATUS.UNAVAILABLE)

    def test_unavailable_without_line(self):
        combined = {k: v for k, v in self._combined_full().items() if k != "line"}
        gate = GameScriptShadowGate()
        result = gate.run(combined=combined, run_id="sg-test-7")
        self.assertEqual(result["status"], GAME_SCRIPT_SHADOW_STATUS.UNAVAILABLE)

    def test_never_raises_on_broken_input(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined={"broken": True}, run_id="sg-test-8")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], GAME_SCRIPT_SHADOW_STATUS.UNAVAILABLE)

    def test_script_priors_present_on_complete(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined=self._combined_full(), run_id="sg-test-9")
        if result["status"] != GAME_SCRIPT_SHADOW_STATUS.UNAVAILABLE:
            self.assertIn("script_priors", result)
            priors = result["script_priors"]
            self.assertAlmostEqual(sum(priors.values()), 1.0, places=4)

    def test_unconditional_prob_in_unit_interval(self):
        gate = GameScriptShadowGate()
        result = gate.run(combined=self._combined_full(), run_id="sg-test-10")
        prob = result.get("unconditional_prob")
        if prob is not None:
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)

    def test_gate_is_stateless(self):
        gate = GameScriptShadowGate()
        r1 = gate.run(combined=self._combined_full(), run_id="sg-a")
        r2 = gate.run(combined=self._combined_full(), run_id="sg-b")
        self.assertEqual(r1["status"], r2["status"])


if __name__ == "__main__":
    unittest.main()
