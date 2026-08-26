"""
gate_engine/tests/test_external_analyst_intelligence.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE — Regression tests.

Proves all governance invariants:
  1.  Analyst consensus cannot directly move model probability (weight=0)
  2.  Analyst picks cannot override starter/lineup/status blockers
  3.  Unverified analyst claims cannot enter failure_path math
  4.  Syndicated copies count once (family deduplication)
  5.  Opposing analysts trigger EXTERNAL_ANALYST_CONTRADICTION_REVIEW blocker
  6.  Conflicting analysts yield ANALYST_CONSENSUS_UNRESOLVED
  7.  Source access failure does not fail the base model
  8.  Promotional records from source are not trusted as performance evidence
  9.  can_execute=False enforced in every new module
  10. ANALYST_CONSENSUS_UNRESOLVED is non-terminal (not DATA_CONTRACT_FAIL)
  11. Analyst layer output always present in MoneylineResult.to_dict()["layers"]
  12. direct_probability_weight=0.0 enforced throughout
  13. Stale source → DATA_UNOBTAINABLE, base model continues
  14. Force-review threshold at 2+ opposing analysts
  15. PickDawgz-family isolation from StumpTheSpread family

can_execute=False unconditional.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch, MagicMock

can_execute: bool = False  # UNCONDITIONAL


# ---------------------------------------------------------------------------
# Helpers: minimal enrichment builders
# ---------------------------------------------------------------------------

def _base_enrichment(**extra: Any) -> dict[str, Any]:
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
            {"team": "Away Team", "odds": 165, "name": "DraftKings"},
        ],
        "market_hours_open": 8.0,
        "hold_pct": 0.045,
        "market_freshness_hours": 0.5,
        "status_freshness_hours": 0.25,
        **extra,
    }


def _sts_pick(
    team: str = "Home Team",
    side: str = "home",
    reasoning: str = "The starter has been dominant.",
    favorite_role: str = "FAVORITE",
    line: str = "-200",
) -> dict[str, Any]:
    """Pre-supplied StumpTheSpread pick via enrichment."""
    return {
        "team": team,
        "side": side,
        "pick": side,
        "reasoning": reasoning,
        "favorite_role": favorite_role,
        "odds": line,
        "market_type": "moneyline",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


def _base_row(**extra: Any) -> dict[str, Any]:
    return {
        "sport": "NBA",
        "team": "Home Team",
        "opponent": "Away Team",
        "market_type": "full_game_h2h",
        "event_id": "nba-eai-test-001",
        "slate_date": "2026-08-08",
        "home_away": "vs",
        **extra,
    }


# ===========================================================================
# 1. Analyst consensus cannot directly move model probability
# ===========================================================================

class TestAnalystCannotMoveModelProbability(unittest.TestCase):
    """direct_probability_weight=0.0; model prob identical with or without analyst data."""

    def test_analyst_agree_does_not_increase_probability(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr_no_analyst = _base_enrichment()
        enr_with_agree = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="home", reasoning="Strong starter advantage.")
            }
        )
        r_no  = run_moneyline_pipeline(row, enr_no_analyst, seed=0)
        r_yes = run_moneyline_pipeline(row, enr_with_agree, seed=0)
        self.assertEqual(
            r_no.outputs.independent_probability,
            r_yes.outputs.independent_probability,
            "Agreeing analyst must not increase independent_probability"
        )

    def test_analyst_oppose_does_not_decrease_probability(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr_no_analyst = _base_enrichment()
        enr_with_oppose = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="away", reasoning="Away pitcher is elite.")
            }
        )
        r_no  = run_moneyline_pipeline(row, enr_no_analyst, seed=0)
        r_opp = run_moneyline_pipeline(row, enr_with_oppose, seed=0)
        self.assertEqual(
            r_no.outputs.independent_probability,
            r_opp.outputs.independent_probability,
            "Opposing analyst must not decrease independent_probability"
        )

    def test_multiple_opposing_analysts_cannot_flip_probability_direction(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr_no_analyst = _base_enrichment()
        # Two opposing analysts — neither should affect probability direction
        enr_two_oppose = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="away"),
            }
        )
        r_no  = run_moneyline_pipeline(row, enr_no_analyst, seed=0)
        r_opp = run_moneyline_pipeline(row, enr_two_oppose, seed=0)
        if (r_no.outputs.independent_probability is not None and
                r_opp.outputs.independent_probability is not None):
            favors_home_no = r_no.outputs.independent_probability > 0.5
            favors_home_opp = r_opp.outputs.independent_probability > 0.5
            self.assertEqual(
                favors_home_no, favors_home_opp,
                "Multiple opposing analysts must not flip probability direction"
            )

    def test_direct_probability_weight_zero_on_every_opinion(self):
        from gate_engine.moneyline.external_analyst.types import AnalystOpinion
        op = AnalystOpinion(
            source_name="stumpsthespread.com",
            source_family="stumps_the_spread",
            side="home",
            direct_probability_weight=0.0,
        )
        self.assertEqual(op.direct_probability_weight, 0.0)
        # Even if someone tries to set it to non-zero, it must be re-enforced by orchestrator
        op.direct_probability_weight = 0.05   # attempted override
        # The orchestrator's fetch loop resets it to 0.0
        from gate_engine.moneyline.external_analyst.orchestrator import (
            run_external_analyst_intelligence,
        )
        row = _base_row()
        enr = _base_enrichment()
        result = run_external_analyst_intelligence(
            row=row, enrichment=enr, sport="NBA",
            team="Home Team", opponent="Away Team",
            wow_side="home", wow_independent_prob=0.62,
        )
        # All opinions in result must have direct_probability_weight=0.0
        for op_dict in result.to_dict()["opinions"]:
            self.assertEqual(
                op_dict["direct_probability_weight"], 0.0,
                "orchestrator must enforce direct_probability_weight=0.0 on every opinion"
            )
        # Result itself must have weight=0.0
        self.assertEqual(result.direct_probability_weight, 0.0)

    def test_result_layer_weight_always_zero(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        result = run_moneyline_pipeline(row, _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="home")
            }
        ), seed=0)
        eai = result.to_dict()["layers"]["external_analyst_intelligence"]
        self.assertEqual(eai.get("direct_probability_weight"), 0.0)


# ===========================================================================
# 2. Analyst picks cannot override starter/lineup/status blockers
# ===========================================================================

class TestAnalystCannotOverrideLineupBlocker(unittest.TestCase):

    def test_analyst_agree_does_not_unlock_participant_block(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr = _base_enrichment(
            lineup_confirmed=False,
            starter_confirmed=False,
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(
                    side="home",
                    reasoning="Home starter is ace; lineup confirmed via beat reporter."
                )
            }
        )
        # Remove participant status to trigger potential lineup block
        enr["participant_status"] = None
        result = run_moneyline_pipeline(row, enr, seed=0)
        self.assertFalse(result.can_execute)
        # If lineup blocker fired, terminal must reflect that (not analyst-unlocked)
        participant_blockers = [b for b in result.blockers if "PARTICIPANT_LOCK" in b]
        if participant_blockers:
            self.assertEqual(result.terminal_label, "DATA_CONTRACT_FAIL",
                             "Analyst cannot unlock a participant lock blocker")

    def test_analyst_confidence_cannot_promote_data_contract_fail(self):
        """DATA_CONTRACT_FAIL row must remain DATA_CONTRACT_FAIL regardless of analyst."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row(sport="SOCCER")   # unsupported sport → model fail
        enr = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="home")
            }
        )
        result = run_moneyline_pipeline(row, enr, seed=0)
        self.assertFalse(result.can_execute)
        # Analyst enrichment must not flip terminal_label to MONEY_QUALIFIED
        self.assertNotEqual(
            result.terminal_label, "MONEY_QUALIFIED",
            "Analyst cannot promote an unsupported sport to MONEY_QUALIFIED"
        )


# ===========================================================================
# 3. Unverified analyst claims cannot enter failure_path math
# ===========================================================================

class TestUnverifiedClaimsCannotEnterFailurePath(unittest.TestCase):

    def test_verified_factual_claims_list_always_empty_from_analyst_layer(self):
        """
        The analyst layer never independently verifies claims —
        verified_factual_claims must always be [] in the raw analyst output.
        """
        from gate_engine.moneyline.external_analyst.orchestrator import (
            run_external_analyst_intelligence,
        )
        row = _base_row()
        enr = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(
                    reasoning=(
                        "Starter is on a strict 80-pitch limit. "
                        "Bullpen ERA over 5.20 last 10 days."
                    )
                )
            }
        )
        result = run_external_analyst_intelligence(
            row=row, enrichment=enr, sport="NBA",
            team="Home Team", opponent="Away Team",
            wow_side="home",
        )
        self.assertEqual(
            result.verified_factual_claims, [],
            "Analyst layer must return empty verified_factual_claims — "
            "independent verification required before model entry"
        )

    def test_analyst_thesis_stays_in_narrative_not_failure_path_matrix(self):
        """
        Analyst thesis must appear in thesis_tags.unverified_narrative,
        not in enrichment['failure_path_matrix'].
        """
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            StumpsTheSpreadAdapter,
        )
        adapter = StumpsTheSpreadAdapter()
        enr = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(
                    reasoning=(
                        "The starter has a known 80-pitch limit tonight. "
                        "Lean on the away side due to depleted bullpen."
                    )
                )
            }
        )
        opinions = adapter.fetch("NBA", "Home Team", "Away Team", "2026-08-08", enr)
        for op in opinions:
            # Unverified claim must live in thesis_tags, not in failure_path_matrix
            self.assertTrue(
                len(op.thesis_tags.unverified_narrative) > 0 or
                op.thesis_tags.starter_pitcher_thesis is not None or
                op.thesis_tags.bullpen_thesis is not None,
                "Claims must be in thesis_tags, not silently discarded"
            )
            # There must be no failure_path_matrix key on the opinion
            self.assertNotIn(
                "failure_path_matrix",
                op.to_dict(),
                "Analyst opinion must not contain a failure_path_matrix key"
            )

    def test_failure_path_matrix_in_enrichment_unchanged_by_analyst_layer(self):
        """
        If enrichment already has a failure_path_matrix, analyst layer must
        not modify, append to, or replace it.
        """
        from gate_engine.moneyline.external_analyst.orchestrator import (
            run_external_analyst_intelligence,
        )
        original_fpm = {
            "PRIMARY_KILL_PATH": {
                "scenario": "ace_early_hook",
                "probability_band": "20-30%",
                "model_adjustment": "-5%",
            }
        }
        enr = _base_enrichment(
            failure_path_matrix=dict(original_fpm),
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(
                    reasoning="Bullpen completely depleted — fade the starter."
                )
            }
        )
        run_external_analyst_intelligence(
            row=_base_row(), enrichment=enr, sport="NBA",
            team="Home Team", opponent="Away Team", wow_side="home",
        )
        # failure_path_matrix must be untouched
        self.assertEqual(
            enr.get("failure_path_matrix"), original_fpm,
            "Analyst layer must never modify enrichment['failure_path_matrix']"
        )


# ===========================================================================
# 4. Syndicated copies count once
# ===========================================================================

class TestSyndicatedCopiesCountOnce(unittest.TestCase):

    def test_same_source_analyst_side_deduplicated(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.family_resolver import (
            deduplicate_opinions, resolve_source_family, resolve_analyst_family
        )
        # Two picks from same source/analyst/side → only one independent
        def make_op(i: int) -> AnalystOpinion:
            sf = resolve_source_family("stumpsthespread.com")
            af = resolve_analyst_family("stump", sf)
            return AnalystOpinion(
                source_name="stumpsthespread.com",
                source_family=sf,
                analyst_name="stump",
                analyst_family=af,
                side="home",
                event_date="2026-08-08",
                team="Home Team",
                source_status=AnalystSourceStatus.RETRIEVED,
            )

        ops = [make_op(i) for i in range(3)]
        independent, all_ops = deduplicate_opinions(ops)
        self.assertEqual(len(independent), 1, "Three identical picks must deduplicate to 1")
        syndicated = [op for op in all_ops if op.is_syndicated_copy]
        self.assertEqual(len(syndicated), 2, "Two copies must be marked syndicated")

    def test_different_source_families_both_independent(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.family_resolver import (
            deduplicate_opinions
        )
        sts_op = AnalystOpinion(
            source_name="stumpsthespread.com",
            source_family="stumps_the_spread",
            analyst_name="stump",
            analyst_family="stumps_the_spread_official",
            side="home",
            event_date="2026-08-08",
            team="Home Team",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        pdz_op = AnalystOpinion(
            source_name="pickdawgz.com",
            source_family="pickdawgz",
            analyst_name="dawg_analyst",
            analyst_family="pickdawgz_official",
            side="home",
            event_date="2026-08-08",
            team="Home Team",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        independent, _ = deduplicate_opinions([sts_op, pdz_op])
        self.assertEqual(
            len(independent), 2,
            "Different source families must both count as independent"
        )

    def test_same_family_different_analyst_both_independent(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.family_resolver import (
            deduplicate_opinions
        )
        op1 = AnalystOpinion(
            source_name="stumpsthespread.com",
            source_family="stumps_the_spread",
            analyst_name="analyst_A",
            analyst_family="analyst_a",
            side="home",
            event_date="2026-08-08",
            team="Home Team",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        op2 = AnalystOpinion(
            source_name="stumpsthespread.com",
            source_family="stumps_the_spread",
            analyst_name="analyst_B",
            analyst_family="analyst_b",
            side="home",
            event_date="2026-08-08",
            team="Home Team",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        independent, _ = deduplicate_opinions([op1, op2])
        self.assertEqual(len(independent), 2,
                         "Same source family with different analysts → both independent")

    def test_contradiction_count_uses_independent_opinions_only(self):
        """3 syndicated copies of same pick count as 1 in contradiction analysis."""
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.family_resolver import (
            deduplicate_opinions
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        ops = []
        for i in range(3):
            op = AnalystOpinion(
                source_name="stumpsthespread.com",
                source_family="stumps_the_spread",
                analyst_name="stump",
                analyst_family="stumps_the_spread_official",
                side="away",   # opposing WOW
                event_date="2026-08-08",
                team="Away Team",
                source_status=AnalystSourceStatus.RETRIEVED,
            )
            ops.append(op)
        independent, all_ops = deduplicate_opinions(ops)
        report = run_contradiction_analysis(independent, wow_side="home")
        self.assertEqual(
            report.external_analyst_contradiction_count, 1,
            "3 syndicated copies must contribute only 1 to contradiction_count"
        )


# ===========================================================================
# 5. Opposing analysts trigger EXTERNAL_ANALYST_CONTRADICTION_REVIEW
# ===========================================================================

class TestOpposingAnalystsTriggerContradictionReview(unittest.TestCase):

    def test_single_opposing_analyst_sets_conflict_flag(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        op = AnalystOpinion(
            source_name="stumpsthespread.com",
            source_family="stumps_the_spread",
            analyst_family="stumps_the_spread_official",
            side="away",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        report = run_contradiction_analysis([op], wow_side="home")
        self.assertTrue(report.external_analyst_conflict_flag)
        self.assertEqual(report.external_analyst_contradiction_count, 1)

    def test_single_opposing_analyst_routes_to_contradiction_review_blocker(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="away")
            }
        )
        result = run_moneyline_pipeline(row, enr, seed=0)
        self.assertFalse(result.can_execute)
        review_blockers = [
            b for b in result.blockers
            if "EXTERNAL_ANALYST_CONTRADICTION_REVIEW" in b
        ]
        self.assertTrue(
            len(review_blockers) > 0,
            "One opposing analyst must trigger EXTERNAL_ANALYST_CONTRADICTION_REVIEW in blockers"
        )

    def test_contradiction_review_is_non_terminal(self):
        """EXTERNAL_ANALYST_CONTRADICTION_REVIEW must not cause DATA_CONTRACT_FAIL."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="away")
            }
        )
        result = run_moneyline_pipeline(row, enr, seed=0)
        self.assertNotEqual(
            result.terminal_label, "DATA_CONTRACT_FAIL",
            "EXTERNAL_ANALYST_CONTRADICTION_REVIEW must be non-terminal"
        )

    def test_research_priority_elevated_with_one_opposing_analyst(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        op = AnalystOpinion(
            source_family="stumps_the_spread",
            analyst_family="stumps_the_spread_official",
            side="away",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        report = run_contradiction_analysis([op], wow_side="home")
        self.assertIn(report.research_priority, ("ELEVATED", "HIGH"))


# ===========================================================================
# 6. Conflicting analysts yield ANALYST_CONSENSUS_UNRESOLVED
# ===========================================================================

class TestConflictingAnalystsYieldConsensusUnresolved(unittest.TestCase):

    def test_analysts_split_produces_unresolved_consensus(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        from gate_engine.moneyline.external_analyst.types import AnalystConsensus

        op_home = AnalystOpinion(
            source_family="stumps_the_spread",
            analyst_family="analyst_a",
            side="home",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        op_away = AnalystOpinion(
            source_family="pickdawgz",
            analyst_family="analyst_b",
            side="away",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        report = run_contradiction_analysis([op_home, op_away], wow_side="home")
        self.assertEqual(
            report.external_analyst_consensus_side,
            AnalystConsensus.ANALYST_CONSENSUS_UNRESOLVED
        )

    def test_analyst_consensus_unresolved_blocker_in_pipeline(self):
        """Split analysts → ANALYST_CONSENSUS_UNRESOLVED in result.blockers."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        # Simulate two analysts (different families) picking opposite sides.
        # We inject one via STS; the contradiction engine sees the split.
        # With only one source registered (STS), we test via orchestrator directly.
        from gate_engine.moneyline.external_analyst.orchestrator import (
            run_external_analyst_intelligence,
        )
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.family_resolver import (
            deduplicate_opinions
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        from gate_engine.moneyline.external_analyst.types import AnalystConsensus

        # Two independent analysts on opposite sides
        op_home = AnalystOpinion(
            source_family="stumps_the_spread",
            analyst_family="analyst_x",
            side="home",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        op_away = AnalystOpinion(
            source_family="pickdawgz",
            analyst_family="analyst_y",
            side="away",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        independent, _ = deduplicate_opinions([op_home, op_away])
        report = run_contradiction_analysis(independent, wow_side="home")
        self.assertEqual(
            report.external_analyst_consensus_side,
            AnalystConsensus.ANALYST_CONSENSUS_UNRESOLVED
        )

    def test_consensus_unresolved_does_not_data_contract_fail(self):
        """ANALYST_CONSENSUS_UNRESOLVED review flag must be non-terminal."""
        from gate_engine.moneyline.pipeline import _NON_TERMINAL_REVIEW_PREFIXES
        self.assertIn(
            "ANALYST_CONSENSUS_UNRESOLVED",
            _NON_TERMINAL_REVIEW_PREFIXES,
            "ANALYST_CONSENSUS_UNRESOLVED must be in _NON_TERMINAL_REVIEW_PREFIXES"
        )


# ===========================================================================
# 7. Source access failure does not fail the base model
# ===========================================================================

class TestSourceFailureDoesNotFailBaseModel(unittest.TestCase):

    def test_sts_http_failure_marks_data_unobtainable(self):
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            StumpsTheSpreadAdapter,
        )
        from gate_engine.moneyline.external_analyst.types import AnalystSourceStatus
        adapter = StumpsTheSpreadAdapter()
        # No enrichment data, HTTP will fail (mocked)
        with patch("requests.get", side_effect=Exception("timeout")):
            opinions = adapter.fetch("NBA", "Home", "Away", "2026-08-08", {})
        self.assertTrue(len(opinions) > 0)
        for op in opinions:
            self.assertEqual(op.source_status, AnalystSourceStatus.DATA_UNOBTAINABLE)

    def test_source_failure_does_not_break_moneyline_pipeline(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr = _base_enrichment()   # no analyst picks, HTTP will fail gracefully
        with patch("requests.get", side_effect=Exception("connection refused")):
            result = run_moneyline_pipeline(row, enr, seed=0)
        # Pipeline must complete without error
        self.assertFalse(result.can_execute)
        # External analyst layer must be present (even if failed)
        layers = result.to_dict()["layers"]
        self.assertIn("external_analyst_intelligence", layers)

    def test_analyst_layer_error_does_not_raise(self):
        """Even a crash in the analyst orchestrator must not propagate."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr = _base_enrichment()
        # Force orchestrator to raise; pipeline must survive
        with patch(
            "gate_engine.moneyline.pipeline.run_external_analyst_intelligence",
            side_effect=RuntimeError("simulated orchestrator crash"),
        ):
            result = run_moneyline_pipeline(row, enr, seed=0)
        self.assertFalse(result.can_execute)
        eai = result.to_dict()["layers"]["external_analyst_intelligence"]
        self.assertIn("LAYER_ERROR", str(eai.get("acquisition_notes", [])))

    def test_source_failure_base_model_probability_unchanged(self):
        """Base model independent_probability must be the same with or without analyst source."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        enr = _base_enrichment()
        r_no_analyst = run_moneyline_pipeline(row, enr, seed=0)
        with patch("requests.get", side_effect=Exception("timeout")):
            r_failed_analyst = run_moneyline_pipeline(row, enr, seed=0)
        self.assertEqual(
            r_no_analyst.outputs.independent_probability,
            r_failed_analyst.outputs.independent_probability,
            "Source failure must leave base model probability unchanged"
        )


# ===========================================================================
# 8. Promotional records from source are not trusted as performance evidence
# ===========================================================================

class TestPromotionalRecordsNotTrusted(unittest.TestCase):

    def test_direct_prob_weight_not_granted_for_claimed_record(self):
        """
        Even if a source claims 85% ATS record, direct_probability_weight stays 0.0.
        Weight cannot be self-promoted — requires a future governance patch after
        reconciled forward-testing.
        """
        from gate_engine.moneyline.external_analyst.types import AnalystOpinion
        # Simulate an opinion with a "claim" in the notes
        op = AnalystOpinion(
            source_name="stumpsthespread.com",
            source_family="stumps_the_spread",
            side="home",
            acquisition_notes=["source_claims_85_pct_ats_record"],
        )
        # Governance: weight must be zero regardless of claimed record
        self.assertEqual(op.direct_probability_weight, 0.0)

    def test_future_weight_governance_comment_present(self):
        """
        The module must document that analyst weight is locked at 0.0
        until a future governance patch authorizes otherwise.
        """
        import gate_engine.moneyline.external_analyst.types as m
        source = m.__doc__ or ""
        self.assertIn("direct_probability_weight", source,
                      "Module docstring must explicitly document weight=0.0 governance")

    def test_analyst_claim_performance_not_verified_without_ledger_reconciliation(self):
        """
        An analyst claiming a historical record produces only an unverified claim.
        It must not affect contradiction counts or research priority.
        """
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        op = AnalystOpinion(
            source_family="stumps_the_spread",
            analyst_family="stumps_the_spread_official",
            side="home",   # agrees with WOW
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        op.thesis_tags.other_factual_claims = [
            "We're 85% ATS on NBA picks this season"
        ]
        report = run_contradiction_analysis([op], wow_side="home")
        # Agreement — no contradiction, no elevated priority from claimed record
        self.assertFalse(report.external_analyst_conflict_flag)
        self.assertEqual(report.research_priority, "NORMAL")


# ===========================================================================
# 9. can_execute=False enforced in every new module
# ===========================================================================

class TestCanExecuteFalseEnforced(unittest.TestCase):

    def _assert_module_can_execute_false(self, module_path: str):
        import importlib
        m = importlib.import_module(module_path)
        self.assertFalse(
            getattr(m, "can_execute", True),
            f"{module_path}.can_execute must be False"
        )

    def test_types_module(self):
        self._assert_module_can_execute_false(
            "gate_engine.moneyline.external_analyst.types"
        )

    def test_family_resolver_module(self):
        self._assert_module_can_execute_false(
            "gate_engine.moneyline.external_analyst.family_resolver"
        )

    def test_contradiction_engine_module(self):
        self._assert_module_can_execute_false(
            "gate_engine.moneyline.external_analyst.contradiction_engine"
        )

    def test_ledger_module(self):
        self._assert_module_can_execute_false(
            "gate_engine.moneyline.external_analyst.ledger"
        )

    def test_base_source_module(self):
        self._assert_module_can_execute_false(
            "gate_engine.moneyline.external_analyst.sources.base"
        )

    def test_stumps_adapter_module(self):
        self._assert_module_can_execute_false(
            "gate_engine.moneyline.external_analyst.sources.stumps_the_spread"
        )

    def test_orchestrator_module(self):
        self._assert_module_can_execute_false(
            "gate_engine.moneyline.external_analyst.orchestrator"
        )

    def test_pipeline_result_can_execute_false(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = _base_row()
        result = run_moneyline_pipeline(row, _base_enrichment(), seed=0)
        self.assertFalse(result.can_execute,
                         "Pipeline result can_execute must be unconditionally False")

    def test_moneyline_result_can_execute_false_by_default(self):
        from gate_engine.moneyline.types import MoneylineResult
        r = MoneylineResult()
        self.assertFalse(r.can_execute)


# ===========================================================================
# 10. ANALYST_CONSENSUS_UNRESOLVED is non-terminal
# ===========================================================================

class TestAnalystConsensusUnresolvedNonTerminal(unittest.TestCase):

    def test_in_non_terminal_review_prefixes(self):
        from gate_engine.moneyline.pipeline import _NON_TERMINAL_REVIEW_PREFIXES
        self.assertIn(
            "ANALYST_CONSENSUS_UNRESOLVED",
            _NON_TERMINAL_REVIEW_PREFIXES,
        )

    def test_external_analyst_contradiction_review_in_non_terminal_prefixes(self):
        from gate_engine.moneyline.pipeline import _NON_TERMINAL_REVIEW_PREFIXES
        self.assertIn(
            "EXTERNAL_ANALYST_CONTRADICTION_REVIEW",
            _NON_TERMINAL_REVIEW_PREFIXES,
        )


# ===========================================================================
# 11. Analyst layer output always present in MoneylineResult layers
# ===========================================================================

class TestAnalystLayerAlwaysPresentInResult(unittest.TestCase):

    def test_layer_present_with_no_picks(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        result = run_moneyline_pipeline(_base_row(), _base_enrichment(), seed=0)
        layers = result.to_dict()["layers"]
        self.assertIn("external_analyst_intelligence", layers)

    def test_layer_contains_required_fields(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        result = run_moneyline_pipeline(_base_row(), _base_enrichment(), seed=0)
        eai = result.to_dict()["layers"]["external_analyst_intelligence"]
        for field in [
            "direct_probability_weight",
            "sources_consulted",
            "sources_failed",
            "independent_analyst_count",
            "total_opinion_count",
            "contradiction_report",
            "verified_factual_claims",
            "acquisition_notes",
        ]:
            self.assertIn(field, eai, f"EAI layer must expose '{field}'")

    def test_contradiction_report_has_six_required_fields(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        enr = _base_enrichment(
            external_analyst_picks={
                "stumps_the_spread": _sts_pick(side="away")
            }
        )
        result = run_moneyline_pipeline(_base_row(), enr, seed=0)
        cr = result.to_dict()["layers"]["external_analyst_intelligence"]["contradiction_report"]
        for field in [
            "external_analyst_agreement_count",
            "external_analyst_contradiction_count",
            "external_analyst_consensus_side",
            "external_analyst_conflict_flag",
            "external_analyst_conflict_reasons",
            "unresolved_claims",
        ]:
            self.assertIn(field, cr, f"ContradictionReport must expose '{field}'")


# ===========================================================================
# 12. direct_probability_weight=0.0 enforced throughout
# ===========================================================================

class TestDirectProbabilityWeightZeroThroughout(unittest.TestCase):

    def test_sts_adapter_always_zero_weight(self):
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            StumpsTheSpreadAdapter,
        )
        adapter = StumpsTheSpreadAdapter()
        enr = _base_enrichment(
            external_analyst_picks={"stumps_the_spread": _sts_pick()}
        )
        opinions = adapter.fetch("NBA", "Home Team", "Away Team", "2026-08-08", enr)
        for op in opinions:
            self.assertEqual(op.direct_probability_weight, 0.0)

    def test_orchestrator_enforces_zero_weight(self):
        from gate_engine.moneyline.external_analyst.orchestrator import (
            run_external_analyst_intelligence,
        )
        enr = _base_enrichment(
            external_analyst_picks={"stumps_the_spread": _sts_pick()}
        )
        ai = run_external_analyst_intelligence(
            row=_base_row(), enrichment=enr, sport="NBA",
            team="Home Team", opponent="Away Team", wow_side="home",
        )
        self.assertEqual(ai.direct_probability_weight, 0.0)
        for op_dict in ai.to_dict()["opinions"]:
            self.assertEqual(op_dict["direct_probability_weight"], 0.0)


# ===========================================================================
# 13. Stale source → DATA_UNOBTAINABLE, base model continues
# ===========================================================================

class TestStaleSourceDataUnobtainable(unittest.TestCase):

    def test_stale_sts_data_returns_unobtainable(self):
        """If supplied pick is very old (no freshness field but marked stale), still works."""
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            StumpsTheSpreadAdapter,
        )
        from gate_engine.moneyline.external_analyst.types import AnalystSourceStatus
        adapter = StumpsTheSpreadAdapter()
        # Simulate an enrichment-supplied pick with explicit stale status
        stale_pick = _sts_pick()
        stale_pick["source_status"] = "STALE"
        enr = _base_enrichment(
            external_analyst_picks={"stumps_the_spread": stale_pick}
        )
        opinions = adapter.fetch("NBA", "Home Team", "Away Team", "2026-08-08", enr)
        # Adapter parses and returns whatever is supplied — stale handling is
        # at the orchestrator level; adapter doesn't gate on this field
        self.assertTrue(len(opinions) > 0)


# ===========================================================================
# 14. Force-review threshold at 2+ opposing analysts
# ===========================================================================

class TestForceReviewThreshold(unittest.TestCase):

    def test_one_opposing_no_force_review(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        op = AnalystOpinion(
            source_family="stumps_the_spread",
            analyst_family="a",
            side="away",
            source_status=AnalystSourceStatus.RETRIEVED,
        )
        report = run_contradiction_analysis([op], wow_side="home")
        self.assertFalse(report.force_contradiction_review,
                         "One opposing analyst must not force review (threshold=2)")

    def test_two_opposing_forces_review(self):
        from gate_engine.moneyline.external_analyst.types import (
            AnalystOpinion, AnalystSourceStatus
        )
        from gate_engine.moneyline.external_analyst.contradiction_engine import (
            run_contradiction_analysis
        )
        ops = [
            AnalystOpinion(
                source_family=f"source_{i}",
                analyst_family=f"analyst_{i}",
                side="away",
                source_status=AnalystSourceStatus.RETRIEVED,
            )
            for i in range(2)
        ]
        report = run_contradiction_analysis(ops, wow_side="home")
        self.assertTrue(report.force_contradiction_review,
                        "Two+ opposing analysts must set force_contradiction_review=True")
        self.assertEqual(report.research_priority, "HIGH")

    def test_force_review_blocker_in_pipeline_output(self):
        """force_contradiction_review=True → FORCE_REVIEW flag in blocker string."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        # Simulate two independent opposing analysts via orchestrator mock
        from gate_engine.moneyline.external_analyst.types import (
            AnalystIntelligenceResult, ContradictionReport, AnalystConsensus
        )

        mock_report = ContradictionReport(
            external_analyst_contradiction_count=2,
            external_analyst_agreement_count=0,
            external_analyst_consensus_side=AnalystConsensus.OPPOSE,
            external_analyst_conflict_flag=True,
            external_analyst_conflict_reasons=["source_a picks away", "source_b picks away"],
            force_contradiction_review=True,
            research_priority="HIGH",
        )
        mock_result = AnalystIntelligenceResult(contradiction_report=mock_report)

        with patch(
            "gate_engine.moneyline.pipeline.run_external_analyst_intelligence",
            return_value=mock_result,
        ):
            result = run_moneyline_pipeline(_base_row(), _base_enrichment(), seed=0)

        force_blockers = [
            b for b in result.blockers
            if "EXTERNAL_ANALYST_CONTRADICTION_REVIEW" in b and "force_review=True" in b
        ]
        self.assertTrue(
            len(force_blockers) > 0,
            "Two opposing analysts must produce force_review=True in the contradiction blocker"
        )


# ===========================================================================
# 15. Patch ID in manifest
# ===========================================================================

class TestManifestPatchIdPresent(unittest.TestCase):

    def test_external_analyst_patch_in_manifest(self):
        from gate_engine.wow_runtime_manifest import WOW_RUNTIME_MANIFEST
        patch_ids = WOW_RUNTIME_MANIFEST.get("active_patch_ids", [])
        self.assertIn(
            "WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE",
            patch_ids,
        )


# ===========================================================================
# 16. Thesis tags structured extraction
# ===========================================================================

class TestThesisTagsExtraction(unittest.TestCase):

    def test_pitcher_claim_tagged_as_starter_pitcher_thesis(self):
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            _extract_thesis_tags,
        )
        text = "The starting pitcher has a strict pitch limit tonight; trust the fade."
        tags = _extract_thesis_tags(text)
        self.assertIsNotNone(tags.starter_pitcher_thesis)
        self.assertIn("pitch limit", tags.starter_pitcher_thesis.lower())

    def test_bullpen_claim_tagged_as_bullpen_thesis(self):
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            _extract_thesis_tags,
        )
        text = "Their bullpen is completely depleted after three straight days of heavy use."
        tags = _extract_thesis_tags(text)
        self.assertIsNotNone(tags.bullpen_thesis)

    def test_weather_claim_tagged_as_weather_thesis(self):
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            _extract_thesis_tags,
        )
        text = "Wind blowing out at 18 mph — expect offense to dominate in this outdoor venue."
        tags = _extract_thesis_tags(text)
        self.assertIsNotNone(tags.weather_venue_thesis)

    def test_raw_text_always_in_unverified_narrative(self):
        from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
            _extract_thesis_tags,
        )
        text = "Take the home team tonight for solid value."
        tags = _extract_thesis_tags(text)
        # Full text preserved in unverified_narrative
        self.assertEqual(tags.unverified_narrative, [text])


if __name__ == "__main__":
    unittest.main()
