"""
gate_engine/tests/test_mlb_starter_change.py
WOW-PATCH-2026-08-08-MLB-SP-SCRATCH — Regression test suite

Test plan
---------
SC01  Aug 8 Rays-Mariners scenario: no fixed ~10pp automatic downgrade from scratch alone.
SC02  Point-estimate delta comes from ERA quality difference, not a scratch-penalty constant.
SC03  Uncertainty expansion is separate from point estimate (no double-counting).
SC04  Clearly inferior emergency replacement materially lowers point estimate.
SC05  Clearly superior replacement raises point estimate.
SC06  Unresolved replacement plan → fail-closed (should_hold=True, no fabricated delta).
SC07  OPENER_BULK architecture treated as legitimate plan, not degraded starter.
SC08  BULLPEN_GAME architecture treated as legitimate plan.
SC09  Non-MLB sport is a no-op (NO_CHANGE_DETECTED).
SC10  No sp_change_detected → no-op.
SC11  ERA delta cap prevents more than 8pp point-estimate shift.
SC12  Missing original_era uses MLB average as reference (not fail-closed).
SC13  Uncertainty components are individually auditable (no double-count of ERA delta).
SC14  Scratched away team inverts the home-perspective probability adjustment.
SC15  Pipeline integration: UNRESOLVED → MODEL_QUALIFIED_HOLD label.
SC16  Pipeline integration: quality delta reaches independent_probability output.
SC17  Pipeline integration: uncertainty expansion reaches calibrated_lower_bound.
SC18  Calibration: starter_change_uncertainty is a named component in uncertainty_components.
SC19  can_execute remains False in all outputs.
SC20  Audit trail output contains all required fields from the patch spec.
"""
from __future__ import annotations

import math
import sys
import os
import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.moneyline.mlb_starter_change import (
    StarterChangeClassification,
    StarterChangePlan,
    ReplacementArchitecture,
    ResearchLabel,
    analyze_mlb_starter_change,
    _ERA_TO_PROB_SLOPE,
    _MAX_PROB_ADJUSTMENT,
    PATCH_ID,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mlb_row(**kw) -> dict:
    row = {
        "sport":      "MLB",
        "team":       "Tampa Bay Rays",
        "opponent":   "Seattle Mariners",
        "slate_date": "2026-08-08",
        "market_type": "h2h",
        "home_away":  "AWAY",   # Rays were the away team
    }
    row.update(kw)
    return row


def _enr_scratch(
    *,
    sp_change_detected: bool = True,
    sp_change_side: str = "away",
    sp_original_era: float = 3.45,
    sp_replacement_plan_era: float | None = 4.10,
    sp_replacement_architecture: str = "OPENER_BULK",
    sp_bullpen_workload: str = "FRESH",
    sp_late_trigger: str = "injury",
    sp_leverage_arms_available: bool = True,
    **kw,
) -> dict:
    enr: dict = {
        "home_win_pct":   0.48,
        "away_win_pct":   0.52,
        "event_status":   "SCHEDULED",
        "lineup_confirmed": True,
        "starter_confirmed": False,
        "sp_change_detected": sp_change_detected,
        "sp_change_side":   sp_change_side,
        "sp_original_era":  sp_original_era,
        "sp_replacement_architecture": sp_replacement_architecture,
        "sp_bullpen_workload": sp_bullpen_workload,
        "sp_late_trigger": sp_late_trigger,
        "sp_leverage_arms_available": sp_leverage_arms_available,
    }
    if sp_replacement_plan_era is not None:
        enr["sp_replacement_plan_era"] = sp_replacement_plan_era
    enr.update(kw)
    return enr


# ---------------------------------------------------------------------------
# SC01: Aug 8 Rays-Mariners — no fixed 9-10pp automatic penalty
# ---------------------------------------------------------------------------

class TestSC01_NoFixedScratchPenalty:
    """
    Griffin Jax scratched, Rays use opener/bulk plan.
    ERA difference between Jax (~3.45) and replacement plan (~4.10) is 0.65 runs.
    Expected probability shift = 0.65 * ERA_TO_PROB_SLOPE ≈ 1.6pp, NOT ~9-10pp.
    """

    def test_sc01_point_estimate_not_9_to_10pp(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_original_era=3.45,
            sp_replacement_plan_era=4.10,   # replacement slightly worse
            sp_change_side="away",          # Rays are away team
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification != StarterChangeClassification.UNRESOLVED_REPLACEMENT
        # The probability adjustment must be well below 9pp (0.09)
        assert abs(result.probability_adjustment) < 0.05, (
            f"Fixed scratch penalty detected: |prob_adj|={abs(result.probability_adjustment):.4f} "
            f"exceeds 5pp threshold. Expected quality-delta-based shift ~1.6pp for 0.65 ERA gap."
        )

    def test_sc01_adjustment_proportional_to_era_gap(self):
        era_gap = 4.10 - 3.45   # 0.65 runs
        expected_raw_shift = era_gap * _ERA_TO_PROB_SLOPE   # ≈ 0.0163
        row = _mlb_row()
        enr = _enr_scratch(
            sp_original_era=3.45,
            sp_replacement_plan_era=4.10,
            sp_change_side="away",
        )
        result = analyze_mlb_starter_change(row, enr)
        # For away scratch, the away team has a worse pitcher →
        # home team benefits → prob_adj (home perspective) is positive
        assert result.probability_adjustment > 0.0, (
            "Away team scratch with worse replacement should produce positive "
            "home-perspective probability adjustment"
        )
        assert abs(abs(result.probability_adjustment) - expected_raw_shift) < 0.005, (
            f"Expected adj ≈ {expected_raw_shift:.4f}, got {result.probability_adjustment:.4f}"
        )

    def test_sc01_classification_roughly_neutral_for_small_gap(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_original_era=3.45,
            sp_replacement_plan_era=3.90,   # only 0.45 gap → ROUGHLY_NEUTRAL
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.ROUGHLY_NEUTRAL


# ---------------------------------------------------------------------------
# SC02: Point estimate moves from quality difference, not fixed penalty
# ---------------------------------------------------------------------------

class TestSC02_QualityDeltaDriven:
    """Prove that different ERA gaps produce proportionally different adjustments."""

    @pytest.mark.parametrize("orig,repl,expected_abs_delta", [
        (3.50, 3.50, 0.000),   # identical quality → zero delta
        (3.50, 4.50, 0.025),   # 1.0 ERA difference → ~2.5pp
        (3.50, 5.50, 0.050),   # 2.0 ERA difference → ~5.0pp
        (3.50, 3.00, 0.013),   # upgrade: better replacement → positive for pitcher's team
    ])
    def test_sc02_era_proportional(self, orig, repl, expected_abs_delta):
        row = _mlb_row(home_away="HOME")  # home pitcher scratched
        enr = _enr_scratch(
            sp_original_era=orig,
            sp_replacement_plan_era=repl,
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert abs(abs(result.probability_adjustment) - expected_abs_delta) < 0.003, (
            f"ERA gap {repl-orig:+.1f} → expected |adj| ≈ {expected_abs_delta:.3f}, "
            f"got {result.probability_adjustment:.4f}"
        )

    def test_sc02_zero_gap_zero_delta(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_original_era=4.00,
            sp_replacement_plan_era=4.00,  # no quality difference
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.probability_adjustment == 0.0


# ---------------------------------------------------------------------------
# SC03: Uncertainty expansion is separate from point estimate (no double-counting)
# ---------------------------------------------------------------------------

class TestSC03_NoDoubleCounting:
    """
    Uncertainty expansion must NOT be added to the probability_adjustment.
    The two effects operate at different pipeline stages.
    """

    def test_sc03_adjustment_does_not_include_uncertainty(self):
        row = _mlb_row(home_away="HOME")
        enr = _enr_scratch(
            sp_original_era=3.50,
            sp_replacement_plan_era=5.00,   # large ERA gap → 3.75pp quality delta
            sp_change_side="home",
            sp_bullpen_workload="DEPLETED",
            sp_leverage_arms_available=False,
        )
        result = analyze_mlb_starter_change(row, enr)

        # Quality delta (1.5 ERA × 0.025) = 3.75pp, capped at 8pp → 3.75pp
        expected_adj = -1.50 * _ERA_TO_PROB_SLOPE   # negative (home pitcher worse)
        assert abs(result.probability_adjustment - expected_adj) < 0.003, (
            f"probability_adjustment={result.probability_adjustment:.4f} "
            f"should equal quality delta only ({expected_adj:.4f})"
        )

        # Uncertainty expansion should be POSITIVE and SEPARATE
        assert result.uncertainty_expansion > 0.0, "Uncertainty expansion should be > 0"

        # The two effects should NOT sum to a fixed constant
        total = abs(result.probability_adjustment) + result.uncertainty_expansion
        assert total != abs(result.probability_adjustment), (
            "uncertainty_expansion must not be zero when workload is DEPLETED"
        )

    def test_sc03_notes_state_no_double_counting(self):
        row = _mlb_row()
        enr = _enr_scratch()
        result = analyze_mlb_starter_change(row, enr)
        joined = " ".join(result.notes)
        assert "no_double_counting" in joined.lower() or "double" in joined.lower(), (
            "Audit notes must explicitly state no double-counting"
        )

    def test_sc03_point_estimate_delta_matches_probability_adjustment(self):
        row = _mlb_row()
        enr = _enr_scratch(sp_original_era=3.50, sp_replacement_plan_era=4.50)
        result = analyze_mlb_starter_change(row, enr)
        assert result.point_estimate_delta == result.probability_adjustment, (
            "point_estimate_delta and probability_adjustment must be equal "
            "(both represent the quality-delta component)"
        )

    def test_sc03_uncertainty_delta_matches_uncertainty_expansion(self):
        row = _mlb_row()
        enr = _enr_scratch()
        result = analyze_mlb_starter_change(row, enr)
        assert result.uncertainty_calibration_delta == result.uncertainty_expansion


# ---------------------------------------------------------------------------
# SC04: Clearly inferior replacement materially lowers point estimate
# ---------------------------------------------------------------------------

class TestSC04_InferiorReplacement:
    """Emergency replacement (ERA 5.5) vs ace starter (ERA 2.8) → material decrease."""

    def test_sc04_material_decrease_inferior_replacement(self):
        row = _mlb_row(home_away="HOME")
        enr = _enr_scratch(
            sp_original_era=2.80,
            sp_replacement_plan_era=5.50,   # 2.7-run ERA gap → strong downgrade
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.KNOWN_DOWNGRADE
        # Expected: -(2.7 × 0.025) = -0.0675pp shift, capped at -0.08
        assert result.probability_adjustment < -0.05, (
            f"Inferior replacement (ERA gap 2.7) must materially lower point estimate; "
            f"got probability_adjustment={result.probability_adjustment:.4f}"
        )

    def test_sc04_strong_downgrade_classification(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_original_era=3.00,
            sp_replacement_plan_era=5.00,   # 2.0-run gap → KNOWN_DOWNGRADE
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.KNOWN_DOWNGRADE

    def test_sc04_moderate_downgrade_classification(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_original_era=3.50,
            sp_replacement_plan_era=4.20,   # 0.70-run gap → KNOWN_DOWNGRADE (moderate)
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.KNOWN_DOWNGRADE


# ---------------------------------------------------------------------------
# SC05: Clearly superior replacement raises point estimate
# ---------------------------------------------------------------------------

class TestSC05_SuperiorReplacement:
    """Replacement pitcher (ERA 2.8) better than original (ERA 4.5) → raise estimate."""

    def test_sc05_point_estimate_increases(self):
        row = _mlb_row(home_away="HOME")
        enr = _enr_scratch(
            sp_original_era=4.50,
            sp_replacement_plan_era=2.80,   # -1.7-run gap → upgrade
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.KNOWN_UPGRADE
        # Home pitcher better → home win prob increases → positive adjustment
        assert result.probability_adjustment > 0.03, (
            f"Superior replacement (ERA gap -1.7) should raise home point estimate; "
            f"got {result.probability_adjustment:.4f}"
        )

    def test_sc05_upgrade_classification(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_original_era=5.00,
            sp_replacement_plan_era=3.00,
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.KNOWN_UPGRADE


# ---------------------------------------------------------------------------
# SC06: Unresolved replacement plan → fail-closed HOLD
# ---------------------------------------------------------------------------

class TestSC06_UnresolvedPlanFailClosed:

    def test_sc06_no_replacement_era_triggers_hold(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_replacement_plan_era=None,   # explicitly absent
            sp_replacement_architecture="UNKNOWN",
        )
        # Remove also any arms or bullpen data
        enr.pop("sp_replacement_plan_era", None)
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.UNRESOLVED_REPLACEMENT
        assert result.should_hold is True

    def test_sc06_no_fabricated_point_delta(self):
        row = _mlb_row()
        enr = _enr_scratch(sp_replacement_plan_era=None)
        enr.pop("sp_replacement_plan_era", None)
        result = analyze_mlb_starter_change(row, enr)
        assert result.probability_adjustment == 0.0, (
            "Unresolved plan must NOT fabricate a point-estimate improvement"
        )

    def test_sc06_zero_uncertainty_expansion_on_unresolved(self):
        """Uncertainty expansion is also 0.0 for UNRESOLVED — hold is the response, not widening."""
        row = _mlb_row()
        enr = _enr_scratch(sp_replacement_plan_era=None)
        enr.pop("sp_replacement_plan_era", None)
        result = analyze_mlb_starter_change(row, enr)
        assert result.uncertainty_expansion == 0.0

    def test_sc06_research_label_is_unresolved(self):
        row = _mlb_row()
        enr = _enr_scratch(sp_replacement_plan_era=None)
        enr.pop("sp_replacement_plan_era", None)
        result = analyze_mlb_starter_change(row, enr)
        assert result.final_research_label == ResearchLabel.UNRESOLVED

    def test_sc06_arms_list_can_resolve_era(self):
        """sp_replacement_arms list with weighted ERA avoids UNRESOLVED."""
        row = _mlb_row()
        enr: dict = {
            "home_win_pct": 0.48,
            "event_status": "SCHEDULED",
            "sp_change_detected": True,
            "sp_change_side": "away",
            "sp_original_era": 3.50,
            "sp_replacement_architecture": "OPENER_BULK",
            "sp_bullpen_workload": "FRESH",
            "sp_replacement_arms": [
                {"role": "opener", "era": 3.80, "expected_innings": 2.0},
                {"role": "bulk",   "era": 4.40, "expected_innings": 4.0},
            ],
        }
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification != StarterChangeClassification.UNRESOLVED_REPLACEMENT
        # Weighted ERA: (3.80*2 + 4.40*4) / 6 = (7.6+17.6)/6 = 25.2/6 = 4.2
        assert result.replacement_pitching_plan_expectation is not None
        assert abs(result.replacement_pitching_plan_expectation - 4.20) < 0.05


# ---------------------------------------------------------------------------
# SC07: OPENER_BULK treated as legitimate architecture
# ---------------------------------------------------------------------------

class TestSC07_OpenerBulkLegitimate:

    def test_sc07_opener_bulk_not_degraded_state(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_replacement_architecture="OPENER_BULK",
            sp_original_era=3.45,
            sp_replacement_plan_era=4.10,   # slight quality gap, not automatic large penalty
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.replacement_architecture == ReplacementArchitecture.OPENER_BULK
        assert result.classification != StarterChangeClassification.UNRESOLVED_REPLACEMENT
        # Must NOT produce an arbitrary large negative adjustment
        assert abs(result.probability_adjustment) < 0.05, (
            "OPENER_BULK architecture must not trigger an automatic large penalty"
        )

    def test_sc07_audit_notes_state_legitimate_architecture(self):
        row = _mlb_row()
        enr = _enr_scratch(sp_replacement_architecture="OPENER_BULK")
        result = analyze_mlb_starter_change(row, enr)
        joined = " ".join(result.notes)
        assert "legitimate" in joined.lower() or "OPENER_BULK" in joined, (
            "Audit notes must acknowledge OPENER_BULK as a legitimate plan"
        )

    def test_sc07_opener_bulk_adds_architecture_uncertainty_not_quality_penalty(self):
        """The only OPENER_BULK effect when ERA is neutral is uncertainty_expansion, not point delta."""
        row = _mlb_row()
        enr = _enr_scratch(
            sp_replacement_architecture="OPENER_BULK",
            sp_original_era=4.10,
            sp_replacement_plan_era=4.10,  # identical ERA → zero quality delta
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.probability_adjustment == 0.0, (
            "Zero ERA gap must produce zero probability_adjustment regardless of architecture"
        )
        assert result.uncertainty_expansion > 0.0, (
            "OPENER_BULK architecture should add uncertainty_expansion even at neutral ERA"
        )


# ---------------------------------------------------------------------------
# SC08: BULLPEN_GAME treated as legitimate architecture
# ---------------------------------------------------------------------------

class TestSC08_BullpenGameLegitimate:

    def test_sc08_bullpen_game_not_auto_downgrade(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_replacement_architecture="BULLPEN_GAME",
            sp_original_era=4.50,
            sp_replacement_plan_era=3.80,  # bullpen plan clearly better (−0.70 ERA gap)
        )
        result = analyze_mlb_starter_change(row, enr)
        # ERA gap = 3.80 − 4.50 = −0.70, well past _UPGRADE_THRESHOLD of −0.50
        assert result.classification == StarterChangeClassification.KNOWN_UPGRADE, (
            f"era_delta={result.quality_delta_era:.2f}: bullpen plan 0.70 ERA better than "
            f"original must classify as KNOWN_UPGRADE, got {result.classification}"
        )
        assert result.replacement_architecture == ReplacementArchitecture.BULLPEN_GAME

    def test_sc08_bullpen_game_highest_uncertainty_architecture(self):
        """BULLPEN_GAME carries more architecture uncertainty than OPENER_BULK."""
        row = _mlb_row()
        enr_bullpen = _enr_scratch(
            sp_replacement_architecture="BULLPEN_GAME",
            sp_original_era=4.00,
            sp_replacement_plan_era=4.00,  # neutral ERA
        )
        enr_opener = _enr_scratch(
            sp_replacement_architecture="OPENER_BULK",
            sp_original_era=4.00,
            sp_replacement_plan_era=4.00,  # neutral ERA
        )
        r_bullpen = analyze_mlb_starter_change(row, enr_bullpen)
        r_opener  = analyze_mlb_starter_change(row, enr_opener)
        assert r_bullpen.uncertainty_expansion > r_opener.uncertainty_expansion, (
            "BULLPEN_GAME must carry more architecture uncertainty than OPENER_BULK"
        )


# ---------------------------------------------------------------------------
# SC09: Non-MLB sport is a no-op
# ---------------------------------------------------------------------------

class TestSC09_NonMLBNoOp:

    @pytest.mark.parametrize("sport", ["NBA", "WNBA", "NFL", "NHL", "SOCCER"])
    def test_sc09_non_mlb_no_change(self, sport):
        row = _mlb_row(sport=sport)
        enr = _enr_scratch()
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.NO_CHANGE_DETECTED
        assert result.probability_adjustment == 0.0
        assert result.uncertainty_expansion == 0.0
        assert result.should_hold is False


# ---------------------------------------------------------------------------
# SC10: No sp_change_detected → no-op
# ---------------------------------------------------------------------------

class TestSC10_NoChangeNoOp:

    def test_sc10_false_flag_no_adjustment(self):
        row = _mlb_row()
        enr = _enr_scratch(sp_change_detected=False)
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.NO_CHANGE_DETECTED
        assert result.probability_adjustment == 0.0

    def test_sc10_absent_flag_no_adjustment(self):
        row = _mlb_row()
        enr = {
            "home_win_pct": 0.50,
            "event_status": "SCHEDULED",
            # sp_change_detected is absent
        }
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.NO_CHANGE_DETECTED
        assert result.probability_adjustment == 0.0


# ---------------------------------------------------------------------------
# SC11: ERA delta cap at ±8pp
# ---------------------------------------------------------------------------

class TestSC11_ERADeltaCap:

    def test_sc11_extreme_downgrade_capped(self):
        """ERA gap of 10 runs would give 25pp shift — must be capped at 8pp."""
        row = _mlb_row(home_away="HOME")
        enr = _enr_scratch(
            sp_original_era=0.50,
            sp_replacement_plan_era=10.00,  # extreme gap → would be 24pp uncapped
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert abs(result.probability_adjustment) <= _MAX_PROB_ADJUSTMENT + 0.001, (
            f"Point-estimate adjustment must be capped at ±{_MAX_PROB_ADJUSTMENT}; "
            f"got {result.probability_adjustment:.4f}"
        )

    def test_sc11_extreme_upgrade_capped(self):
        row = _mlb_row(home_away="HOME")
        enr = _enr_scratch(
            sp_original_era=10.00,
            sp_replacement_plan_era=0.50,
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert abs(result.probability_adjustment) <= _MAX_PROB_ADJUSTMENT + 0.001


# ---------------------------------------------------------------------------
# SC12: Missing original_era uses MLB average (not fail-closed)
# ---------------------------------------------------------------------------

class TestSC12_MissingOriginalERA:

    def test_sc12_missing_original_era_uses_mlb_average(self):
        from gate_engine.moneyline.mlb_starter_change import _MLB_AVG_STARTER_ERA
        row = _mlb_row()
        enr = _enr_scratch(sp_replacement_plan_era=5.50)
        enr.pop("sp_original_era", None)   # remove original ERA
        result = analyze_mlb_starter_change(row, enr)
        # Must not be UNRESOLVED_REPLACEMENT — missing original ERA is not fatal
        assert result.classification != StarterChangeClassification.UNRESOLVED_REPLACEMENT
        assert result.original_pitching_plan_expectation == _MLB_AVG_STARTER_ERA

    def test_sc12_replacement_era_absent_is_unresolved(self):
        """Only missing REPLACEMENT era triggers UNRESOLVED (not missing original)."""
        row = _mlb_row()
        enr: dict = {
            "event_status": "SCHEDULED",
            "sp_change_detected": True,
            "sp_change_side": "home",
            # sp_original_era absent AND sp_replacement_plan_era absent
        }
        result = analyze_mlb_starter_change(row, enr)
        assert result.classification == StarterChangeClassification.UNRESOLVED_REPLACEMENT
        assert result.should_hold is True


# ---------------------------------------------------------------------------
# SC13: Uncertainty components are individually auditable
# ---------------------------------------------------------------------------

class TestSC13_UncertaintyComponents:

    def test_sc13_components_dict_present(self):
        row = _mlb_row()
        enr = _enr_scratch(
            sp_replacement_architecture="OPENER_BULK",
            sp_bullpen_workload="TAXED",
            sp_leverage_arms_available=False,
        )
        result = analyze_mlb_starter_change(row, enr)
        assert "architecture" in result.uncertainty_components
        assert "workload" in result.uncertainty_components
        assert "no_leverage_arms" in result.uncertainty_components
        assert "total_capped" in result.uncertainty_components

    def test_sc13_depleted_workload_higher_than_fresh(self):
        row = _mlb_row()
        enr_fresh    = _enr_scratch(sp_bullpen_workload="FRESH")
        enr_depleted = _enr_scratch(sp_bullpen_workload="DEPLETED")
        r_fresh    = analyze_mlb_starter_change(row, enr_fresh)
        r_depleted = analyze_mlb_starter_change(row, enr_depleted)
        assert r_depleted.uncertainty_components["workload"] > \
               r_fresh.uncertainty_components["workload"]

    def test_sc13_no_leverage_arms_adds_to_uncertainty(self):
        row = _mlb_row()
        enr_yes = _enr_scratch(sp_leverage_arms_available=True)
        enr_no  = _enr_scratch(sp_leverage_arms_available=False)
        r_yes = analyze_mlb_starter_change(row, enr_yes)
        r_no  = analyze_mlb_starter_change(row, enr_no)
        assert r_no.uncertainty_components["no_leverage_arms"] > \
               r_yes.uncertainty_components["no_leverage_arms"]

    def test_sc13_uncertainty_components_do_not_overlap_with_era_delta(self):
        """
        The uncertainty components dict must NOT contain era_delta or probability
        adjustment keys — those belong only to the quality-delta effect, not here.
        Checked via exact key membership, not substring, to avoid false positives
        from component names like 'no_leverage_arms' that happen to contain 'era'.
        """
        _QUALITY_DELTA_KEYS = {
            "era_delta", "probability_adjustment", "point_estimate_delta",
            "raw_shift", "quality_delta",
        }
        row = _mlb_row()
        enr = _enr_scratch()
        result = analyze_mlb_starter_change(row, enr)
        for key in result.uncertainty_components:
            assert key not in _QUALITY_DELTA_KEYS, (
                f"Uncertainty component key '{key}' belongs to the quality-delta — "
                "not the uncertainty expansion. These must not overlap."
            )


# ---------------------------------------------------------------------------
# SC14: Away-side scratch inverts home-perspective adjustment
# ---------------------------------------------------------------------------

class TestSC14_ScratchedTeamPerspective:

    def test_sc14_home_scratch_negative_home_prob(self):
        """Home pitcher worse → home win prob decreases → negative adjustment."""
        row = _mlb_row(home_away="HOME")
        enr = _enr_scratch(
            sp_original_era=3.00,
            sp_replacement_plan_era=5.00,
            sp_change_side="home",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.probability_adjustment < 0.0, (
            "Worse home pitcher should produce negative home-perspective adjustment"
        )

    def test_sc14_away_scratch_positive_home_prob(self):
        """Away pitcher worse → home benefits → positive home-perspective adjustment."""
        row = _mlb_row(home_away="AWAY")
        enr = _enr_scratch(
            sp_original_era=3.00,
            sp_replacement_plan_era=5.00,
            sp_change_side="away",
        )
        result = analyze_mlb_starter_change(row, enr)
        assert result.probability_adjustment > 0.0, (
            "Worse away pitcher should produce positive home-perspective adjustment"
        )

    def test_sc14_same_era_gap_opposite_signs(self):
        """Same ERA gap on opposite sides should produce equal-magnitude opposite adjustments."""
        row = _mlb_row()
        enr_home = _enr_scratch(
            sp_original_era=3.50, sp_replacement_plan_era=5.00, sp_change_side="home")
        enr_away = _enr_scratch(
            sp_original_era=3.50, sp_replacement_plan_era=5.00, sp_change_side="away")
        r_home = analyze_mlb_starter_change(row, enr_home)
        r_away = analyze_mlb_starter_change(row, enr_away)
        assert abs(r_home.probability_adjustment + r_away.probability_adjustment) < 0.001, (
            "Same ERA gap on opposite sides must produce equal-magnitude opposite adjustments"
        )


# ---------------------------------------------------------------------------
# SC15: Pipeline integration — UNRESOLVED → MODEL_QUALIFIED_HOLD
# ---------------------------------------------------------------------------

class TestSC15_PipelineHoldIntegration:
    """Full pipeline: unresolved replacement plan must produce MODEL_QUALIFIED_HOLD."""

    def _make_pipeline_enr(self, **kw) -> dict:
        enr: dict = {
            "home_win_pct":     0.48,
            "away_win_pct":     0.52,
            "home_elo":         1480,
            "away_elo":         1510,
            "event_status":     "SCHEDULED",
            "lineup_confirmed": True,
            "starter_confirmed": False,
            "player_status":    "ACTIVE",
            "game_log": [{"result": "W"}, {"result": "L"}, {"result": "W"},
                         {"result": "L"}, {"result": "W"}],
            "sp_change_detected": True,
            "sp_change_side":   "home",
            # sp_replacement_plan_era absent → UNRESOLVED
        }
        enr.update(kw)
        return enr

    def test_sc15_unresolved_produces_model_qualified_hold(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "MLB",
            "team": "Tampa Bay Rays",
            "opponent": "Seattle Mariners",
            "slate_date": "2026-08-08",
            "home_away": "AWAY",
            "market_type": "h2h",
        }
        enr = self._make_pipeline_enr()
        result = run_moneyline_pipeline(row, enr)
        assert result.terminal_label == "MODEL_QUALIFIED_HOLD", (
            f"Unresolved replacement plan must cap at MODEL_QUALIFIED_HOLD; "
            f"got {result.terminal_label!r}"
        )
        assert result.can_execute is False

    def test_sc15_hold_blocker_present(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "MLB",
            "team": "Tampa Bay Rays",
            "opponent": "Seattle Mariners",
            "slate_date": "2026-08-08",
            "home_away": "AWAY",
            "market_type": "h2h",
        }
        enr = self._make_pipeline_enr()
        result = run_moneyline_pipeline(row, enr)
        blocker_text = " ".join(result.blockers)
        assert "MLB_SP_SCRATCH" in blocker_text or "UNRESOLVED" in blocker_text


# ---------------------------------------------------------------------------
# SC16: Pipeline integration — quality delta reaches independent_probability
# ---------------------------------------------------------------------------

class TestSC16_PipelineQualityDelta:
    """Same game, same team stats, different pitcher quality → different independent_probability."""

    def _make_row_and_base_enr(self) -> tuple[dict, dict]:
        row = {
            "sport": "MLB",
            "team": "Team A",
            "opponent": "Team B",
            "slate_date": "2026-08-08",
            "home_away": "HOME",
            "market_type": "h2h",
        }
        base_enr = {
            "home_win_pct":     0.52,
            "away_win_pct":     0.48,
            "event_status":     "SCHEDULED",
            "lineup_confirmed": True,
            "starter_confirmed": True,
            "player_status":    "ACTIVE",
            "game_log": [{"result": "W"}, {"result": "W"}, {"result": "L"},
                         {"result": "W"}, {"result": "L"}],
            "sp_change_detected": True,
            "sp_change_side":   "home",   # HOME team's starter scratched
            "sp_original_era":  3.00,
        }
        return row, base_enr

    def test_sc16_inferior_replacement_lowers_probability(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row, base_enr = self._make_row_and_base_enr()

        enr_good = dict(base_enr, sp_replacement_plan_era=3.10)    # nearly same
        enr_bad  = dict(base_enr, sp_replacement_plan_era=5.50)    # clearly worse

        r_good = run_moneyline_pipeline(row, dict(enr_good))
        r_bad  = run_moneyline_pipeline(row, dict(enr_bad))

        assert r_good.outputs.independent_probability is not None
        assert r_bad.outputs.independent_probability is not None
        assert r_good.outputs.independent_probability > r_bad.outputs.independent_probability, (
            "Inferior replacement should lower independent_probability; "
            f"good={r_good.outputs.independent_probability:.4f} "
            f"bad={r_bad.outputs.independent_probability:.4f}"
        )

    def test_sc16_no_change_unaffected(self):
        """Without sp_change_detected, pipeline output should be unaffected by patch."""
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row, base_enr = self._make_row_and_base_enr()
        enr_no_change = {k: v for k, v in base_enr.items()
                         if k not in ("sp_change_detected", "sp_change_side",
                                      "sp_original_era")}
        result = run_moneyline_pipeline(row, enr_no_change)
        # starter_change layer must exist with NO_CHANGE_DETECTED
        sc = result.starter_change
        assert sc.get("classification") == StarterChangeClassification.NO_CHANGE_DETECTED


# ---------------------------------------------------------------------------
# SC17: Pipeline integration — uncertainty expansion reaches calibrated_lower_bound
# ---------------------------------------------------------------------------

class TestSC17_PipelineUncertaintyExpansion:
    """Worse workload (DEPLETED) → lower CLB relative to FRESH workload."""

    def _make_mlb_pipeline_enr(self, workload: str, era_delta: float = 0.5) -> dict:
        return {
            "home_win_pct":     0.50,
            "away_win_pct":     0.50,
            "event_status":     "SCHEDULED",
            "lineup_confirmed": True,
            "starter_confirmed": False,
            "player_status":    "ACTIVE",
            "game_log": [{"result": "W"}, {"result": "L"}, {"result": "W"},
                         {"result": "W"}, {"result": "L"}],
            "sp_change_detected": True,
            "sp_change_side":   "home",
            "sp_original_era":  3.50,
            "sp_replacement_plan_era": 3.50 + era_delta,
            "sp_replacement_architecture": "OPENER_BULK",
            "sp_bullpen_workload": workload,
        }

    def test_sc17_depleted_workload_lowers_clb(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "MLB", "team": "Home Team", "opponent": "Away Team",
            "slate_date": "2026-08-08", "home_away": "HOME", "market_type": "h2h",
        }
        r_fresh    = run_moneyline_pipeline(row, self._make_mlb_pipeline_enr("FRESH"))
        r_depleted = run_moneyline_pipeline(row, self._make_mlb_pipeline_enr("DEPLETED"))

        clb_fresh    = r_fresh.outputs.calibrated_probability_lower_bound
        clb_depleted = r_depleted.outputs.calibrated_probability_lower_bound

        if clb_fresh is None or clb_depleted is None:
            pytest.skip("CLB unavailable — model data insufficient for this test")

        assert clb_depleted <= clb_fresh, (
            f"DEPLETED workload must lower CLB vs FRESH; "
            f"fresh={clb_fresh:.4f} depleted={clb_depleted:.4f}"
        )


# ---------------------------------------------------------------------------
# SC18: Calibration starter_change_uncertainty is a named component
# ---------------------------------------------------------------------------

class TestSC18_CalibrationComponent:

    def test_sc18_starter_change_uncertainty_in_components(self):
        from gate_engine.moneyline.dynamic_calibration import calibrate
        from gate_engine.moneyline.model_disagreement import audit_model_disagreement

        enrichment = {
            "home_win_pct": 0.52,
            "game_log": [{"result": "W"}, {"result": "L"}, {"result": "W"}],
            "starter_change_uncertainty_expansion": 0.025,  # injected by pipeline
        }
        dis_audit = audit_model_disagreement({"h2h_historical": 0.52})
        cal = calibrate(
            independent_prob=0.52,
            model_status="ACTIVE",
            sport="MLB",
            enrichment=enrichment,
            disagreement_audit=dis_audit,
        )
        assert "starter_change_uncertainty" in cal.uncertainty_components, (
            "starter_change_uncertainty must be a named component in calibration output"
        )
        assert cal.uncertainty_components["starter_change_uncertainty"] == pytest.approx(0.025)

    def test_sc18_zero_when_no_patch(self):
        from gate_engine.moneyline.dynamic_calibration import calibrate
        enrichment = {
            "home_win_pct": 0.52,
            "game_log": [{"result": "W"}, {"result": "L"}, {"result": "W"}],
            # No starter_change_uncertainty_expansion key
        }
        cal = calibrate(
            independent_prob=0.52,
            model_status="ACTIVE",
            sport="MLB",
            enrichment=enrichment,
        )
        assert cal.uncertainty_components.get("starter_change_uncertainty", 0.0) == 0.0


# ---------------------------------------------------------------------------
# SC19: can_execute remains False in all outputs
# ---------------------------------------------------------------------------

class TestSC19_CanExecuteFalse:

    def test_sc19_module_level_false(self):
        from gate_engine.moneyline import mlb_starter_change
        assert mlb_starter_change.can_execute is False

    def test_sc19_star_changePlan_always_false(self):
        row = _mlb_row()
        for enr in [
            _enr_scratch(),
            _enr_scratch(sp_replacement_plan_era=None),
            _enr_scratch(sp_replacement_plan_era=2.00),
        ]:
            enr.pop("sp_replacement_plan_era", None) if "sp_replacement_plan_era" not in enr else None
            result = analyze_mlb_starter_change(row, enr)
            assert result.can_execute is False, (
                f"can_execute must be False for classification={result.classification}"
            )

    def test_sc19_pipeline_result_can_execute_false(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "MLB", "team": "Rays", "opponent": "Mariners",
            "slate_date": "2026-08-08", "home_away": "AWAY", "market_type": "h2h",
        }
        enr = {
            "home_win_pct": 0.48, "away_win_pct": 0.52,
            "event_status": "SCHEDULED",
            "lineup_confirmed": True, "player_status": "ACTIVE",
            "game_log": [{"result": "W"}, {"result": "L"}, {"result": "W"}],
            "sp_change_detected": True, "sp_change_side": "away",
            "sp_original_era": 3.45, "sp_replacement_plan_era": 4.10,
            "sp_replacement_architecture": "OPENER_BULK",
        }
        result = run_moneyline_pipeline(row, enr)
        assert result.can_execute is False
        assert result.can_approve_bets is False


# ---------------------------------------------------------------------------
# SC20: Audit trail contains all required fields from the patch spec
# ---------------------------------------------------------------------------

class TestSC20_AuditTrail:

    _REQUIRED_AUDIT_FIELDS = [
        "original_pitching_plan_expectation",
        "replacement_pitching_plan_expectation",
        "point_estimate_delta",
        "uncertainty_calibration_delta",
        "bullpen_workload_status",
        "late_news_trigger",
        "final_research_label",
        "classification",
        "replacement_architecture",
        "patch_id",
        "can_execute",
        "notes",
    ]

    def test_sc20_all_required_fields_present_in_to_dict(self):
        row = _mlb_row()
        enr = _enr_scratch()
        result = analyze_mlb_starter_change(row, enr)
        d = result.to_dict()
        for field in self._REQUIRED_AUDIT_FIELDS:
            assert field in d, f"Required audit field '{field}' missing from to_dict()"

    def test_sc20_patch_id_is_correct(self):
        row = _mlb_row()
        enr = _enr_scratch()
        result = analyze_mlb_starter_change(row, enr)
        assert result.patch_id == PATCH_ID
        assert "MLB-SP-SCRATCH" in result.patch_id

    def test_sc20_final_research_label_always_present(self):
        """All classification paths must produce a final_research_label."""
        scenarios = [
            _enr_scratch(),                                             # normal
            _enr_scratch(sp_replacement_plan_era=None),                 # unresolved
            {**_enr_scratch(), **{"sp_change_detected": False}},        # no change
        ]
        for enr in scenarios:
            enr_copy = dict(enr)
            enr_copy.pop("sp_replacement_plan_era", None)
            row = _mlb_row()
            result = analyze_mlb_starter_change(row, enr_copy)
            assert result.final_research_label in (
                ResearchLabel.PROCEED, ResearchLabel.HOLD, ResearchLabel.UNRESOLVED
            ), f"Invalid final_research_label: {result.final_research_label!r}"

    def test_sc20_notes_non_empty_for_active_change(self):
        row = _mlb_row()
        enr = _enr_scratch()
        result = analyze_mlb_starter_change(row, enr)
        assert len(result.notes) >= 3, (
            "Active starter-change analysis must produce at least 3 audit notes"
        )

    def test_sc20_to_dict_includes_starter_change_in_pipeline_layers(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline
        row = {
            "sport": "MLB", "team": "Rays", "opponent": "Mariners",
            "slate_date": "2026-08-08", "home_away": "AWAY", "market_type": "h2h",
        }
        enr = {
            "home_win_pct": 0.48, "away_win_pct": 0.52, "event_status": "SCHEDULED",
            "lineup_confirmed": True, "player_status": "ACTIVE",
            "game_log": [{"result": "W"}, {"result": "L"}, {"result": "W"}],
            "sp_change_detected": True, "sp_change_side": "away",
            "sp_original_era": 3.45, "sp_replacement_plan_era": 4.10,
        }
        result = run_moneyline_pipeline(row, enr)
        d = result.to_dict()
        assert "starter_change" in d.get("layers", {}), (
            "starter_change must appear in MoneylineResult layers for the GPT schema"
        )
        assert d["layers"]["starter_change"].get("patch_id") == PATCH_ID
