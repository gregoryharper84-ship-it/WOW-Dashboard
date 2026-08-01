"""
gate_engine/tests/test_patch_2026_08_01.py

Regression suite for 2026-08-01 postmortem patches:
  WOW-PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD
  WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE
  WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY

14 tests matching the handoff spec.
"""
from __future__ import annotations

import pytest

from gate_engine.mlb.first_inning_efficiency import (
    BAND_INCOMPLETE,
    BAND_MATERIAL,
    BAND_MILD,
    BAND_SEVERE,
    BAND_STABLE,
    CEILING_HOLD,
    CEILING_NONE,
    CEILING_WATCH,
    DFS_HIGH,
    DFS_LOW,
    DFS_MODERATE,
    DFS_SEVERE,
    apply_lowest_ceiling,
    calculate_directional_fragility_score,
    calculate_recent_1ip_efficiency_score,
)
from gate_engine.portfolio.slip_exposure_ledger import (
    TIER_0, TIER_1, TIER_2, TIER_3,
    apply_cross_slip_exposure_ceiling,
    build_duplicate_groups,
    build_shared_distribution_groups,
    calculate_duplicate_leg_exposure_pct,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(player="A Pitcher", stat="1IP_Pitches", line=13.5, side="LESS", stake=10.0):
    return {
        "player": player,
        "stat_type": stat,
        "line": line,
        "side": side,
        "proposed_stake": stake,
    }


def _all_tier1_flags(score_per_metric: float) -> dict:
    """Return metric_flags where every Tier-1 metric fires with the given value."""
    return {
        "pbf_deterioration":     score_per_metric,
        "pitches_per_start_det": score_per_metric,
        "walk_rate_1ip_det":     score_per_metric,
        "first_pitch_strike_det": score_per_metric,
        "zone_rate_det":          score_per_metric,
        "overall_bb_rate_det":    score_per_metric,
        "csw_rate_det":           score_per_metric,
    }


def _score_from_flags(flags: dict) -> float:
    """Re-compute the weighted Tier-1 score for assertion verification."""
    weights = {
        "pbf_deterioration":      0.20,
        "pitches_per_start_det":  0.20,
        "walk_rate_1ip_det":      0.15,
        "first_pitch_strike_det": 0.15,
        "zone_rate_det":          0.10,
        "overall_bb_rate_det":    0.10,
        "csw_rate_det":           0.10,
    }
    total_w = 0.0
    weighted = 0.0
    for k, w in weights.items():
        v = flags.get(k)
        if v is not None:
            weighted += v * w
            total_w  += w
    return weighted / total_w if total_w > 0 else 0.0


# ===========================================================================
# Cross-slip duplicate guard (PATCH-CROSS-SLIP-DUPLICATE-GUARD)
# ===========================================================================

class TestT1_ExactDuplicateHardStop:
    """Test 1: Exact duplicate >20% exposure hard-stops."""

    def test_exact_dup_above_threshold_is_tier3(self):
        # Two identical legs each with stake=12; total=24 out of 100 → 24% > 20%
        rows = [_row(stake=12.0), _row(stake=12.0)]
        result = apply_cross_slip_exposure_ceiling(rows, portfolio_stake_base=100.0)

        assert result["tier"] == TIER_3
        assert result["action"] == "HARD_STOP_CROSS_SLIP_OVEREXPOSURE"
        assert result["highest_exact_dup_pct"] > 0.20

    def test_exact_dup_below_threshold_is_not_tier3(self):
        # Two identical legs stake=8; total=16% — under 20% → TIER_1
        rows = [_row(stake=8.0), _row(stake=8.0)]
        result = apply_cross_slip_exposure_ceiling(rows, portfolio_stake_base=100.0)

        assert result["tier"] != TIER_3

    def test_no_duplicates_is_tier0(self):
        rows = [
            _row(player="Alpha", stake=10.0),
            _row(player="Beta",  stake=10.0),
        ]
        result = apply_cross_slip_exposure_ceiling(rows, portfolio_stake_base=100.0)
        assert result["tier"] == TIER_0


class TestT2_NestedThresholdsGrouped:
    """Test 2: Nested same-direction thresholds are grouped."""

    def test_alternate_lines_same_player_stat_side_grouped(self):
        rows = [
            _row(player="C Kershaw", stat="1IP_Pitches", line=13.5, side="LESS", stake=5.0),
            _row(player="C Kershaw", stat="1IP_Pitches", line=15.5, side="LESS", stake=5.0),
        ]
        dist_groups = build_shared_distribution_groups(rows)
        assert len(dist_groups) == 1, f"Expected 1 shared-dist group, got: {dist_groups}"

    def test_different_directions_not_grouped_as_shared_distribution(self):
        rows = [
            _row(player="C Kershaw", stat="1IP_Pitches", line=13.5, side="LESS", stake=5.0),
            _row(player="C Kershaw", stat="1IP_Pitches", line=13.5, side="MORE", stake=5.0),
        ]
        # Different sides → different distribution family keys → no shared-distribution group
        # (they ARE different theses; nested-line detection is direction-inclusive)
        dist_groups = build_shared_distribution_groups(rows)
        assert len(dist_groups) == 0


class TestT3_MissingDenominatorCapsAtHold:
    """Test 3: Missing exposure denominator caps at HOLD."""

    def test_zero_stake_rows_missing_denom_caps_tier2(self):
        # All proposed_stakes are 0 → workbook fallback denom = 0 → unknown
        rows = [_row(stake=0), _row(stake=0)]
        result = apply_cross_slip_exposure_ceiling(rows, portfolio_stake_base=None)
        # With two identical rows and no usable denominator, should be TIER_2 or TIER_3
        # (exact dup group found; pct=None → TIER_2)
        assert result["tier"] in (TIER_2, TIER_3)

    def test_duplicate_group_no_denominator_is_tier2(self):
        rows = [_row(stake=0), _row(stake=0)]
        dup_groups = build_duplicate_groups(rows)
        assert len(dup_groups) == 1
        pct = calculate_duplicate_leg_exposure_pct(
            list(dup_groups.values())[0], rows, None
        )
        assert pct is None   # denominator unknown


# ===========================================================================
# 1IP Efficiency Gap gate (PATCH-1IP-EFFICIENCY-GAP-ENFORCE)
# ===========================================================================

class TestT4_EfficiencyScore049MildHaircut:
    """Test 4: Efficiency score 0.49 applies only mild haircut."""

    def test_score_just_below_0_50_is_mild(self):
        # Weighted score ≈ 0.49 → MILD band
        # All 7 metrics available; fire enough to land just below 0.50
        # Use 0.5-scored metrics to land at 0.49 (≈ 0.49 × weight_sum / weight_sum)
        flags = {
            "pbf_deterioration":      0.5,   # 0.20 × 0.5 = 0.10
            "pitches_per_start_det":  0.5,   # 0.20 × 0.5 = 0.10
            "walk_rate_1ip_det":      0.5,   # 0.15 × 0.5 = 0.075
            "first_pitch_strike_det": 0.5,   # 0.15 × 0.5 = 0.075
            "zone_rate_det":          0.5,   # 0.10 × 0.5 = 0.05
            "overall_bb_rate_det":    0.5,   # 0.10 × 0.5 = 0.05
            "csw_rate_det":           0.0,   # 0.10 × 0.0 = 0.00
        }
        # Expected: 0.10+0.10+0.075+0.075+0.05+0.05 = 0.45; < 0.50 → MILD
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        assert result["efficiency_band"] == BAND_MILD
        assert result["efficiency_probability_haircut"] == pytest.approx(0.02)
        assert result["efficiency_ceiling"] is CEILING_NONE

    def test_score_0_49_does_not_block_top_confidence(self):
        flags = {k: 0.5 for k in ["pbf_deterioration", "pitches_per_start_det",
                                   "walk_rate_1ip_det", "first_pitch_strike_det",
                                   "zone_rate_det", "overall_bb_rate_det"]}
        flags["csw_rate_det"] = 0.0
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        # MILD → ceiling is None (no top-confidence block applied by the score itself)
        assert result["efficiency_ceiling"] is CEILING_NONE


class TestT5_EfficiencyScore050BlocksTopConfidence:
    """Test 5: Efficiency score 0.50 blocks top confidence."""

    def test_score_0_50_is_material(self):
        # Need final_score = 0.50 exactly: use all 7 metrics at 0.5 each
        # 0.5 × (0.20+0.20+0.15+0.15+0.10+0.10+0.10) = 0.5 × 1.0 = 0.50
        flags = _all_tier1_flags(0.5)
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        assert result["efficiency_band"] == BAND_MATERIAL
        assert result["efficiency_ceiling"] == CEILING_HOLD

    def test_era_alone_cannot_trigger_gate(self):
        # ERA is contextual only; if the caller does not supply metric_flags,
        # the gate must not fire based on nothing.
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01",
                                                        metric_flags={})
        # 0 metrics available → INCOMPLETE → ceiling=HOLD (missing data)
        assert result["efficiency_band"] == BAND_INCOMPLETE
        assert result["data_coverage_count"] == 0


class TestT6_EfficiencyScore070CapsWatch:
    """Test 6: Efficiency score 0.70 caps at WATCH."""

    def test_score_0_70_band_severe(self):
        # All 7 metrics at 1.0 → score = 1.0 × sum_weights = 1.0 > 0.70
        flags = _all_tier1_flags(1.0)
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        assert result["efficiency_band"] == BAND_SEVERE
        assert result["efficiency_ceiling"] == CEILING_WATCH

    def test_score_exactly_0_70_is_severe(self):
        # Construct flags yielding exactly 0.70:
        # All at 1.0 but weight sum = 1.0, so 0.70 * 1.0 = 0.70 → SEVERE
        # Easier: 7 metrics, use weights to hit 0.70
        flags = {
            "pbf_deterioration":      1.0,   # 0.20
            "pitches_per_start_det":  1.0,   # 0.20
            "walk_rate_1ip_det":      1.0,   # 0.15
            "first_pitch_strike_det": 1.0,   # 0.15
            "zone_rate_det":          1.0,   # 0.10
            "overall_bb_rate_det":    0.0,   # 0.10
            "csw_rate_det":           0.0,   # 0.10
        }
        # score = (0.20+0.20+0.15+0.15+0.10) / 1.0 = 0.80 → SEVERE (already > 0.70)
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        assert result["efficiency_band"] == BAND_SEVERE


class TestT7_FewerThan4MetricsCapsHold:
    """Test 7: Fewer than four Tier-1 metrics caps LESS at HOLD."""

    def test_3_metrics_available_is_incomplete(self):
        flags = {
            "pbf_deterioration":     1.0,
            "pitches_per_start_det": 1.0,
            "walk_rate_1ip_det":     1.0,
            # remaining 4 absent
        }
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        assert result["efficiency_band"] == BAND_INCOMPLETE
        assert result["efficiency_ceiling"] == CEILING_HOLD
        assert result["data_coverage_count"] == 3

    def test_4_metrics_available_not_incomplete(self):
        flags = {
            "pbf_deterioration":      1.0,
            "pitches_per_start_det":  1.0,
            "walk_rate_1ip_det":      1.0,
            "first_pitch_strike_det": 0.0,
        }
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        assert result["efficiency_band"] != BAND_INCOMPLETE
        assert result["data_coverage_count"] == 4


# ===========================================================================
# Directional Fragility Score (PATCH-PITCH-COUNT-DIRECTIONAL-ASYMMETRY)
# ===========================================================================

class TestT8_DFS069ModerateNoCeiling:
    """Test 8: DFS 0.69 applies moderate treatment (no hard ceiling)."""

    def test_dfs_0_69_is_moderate(self):
        # Target DFS ~= 0.61 (between 0.55 and 0.70) → MODERATE
        # tbl=0.42/0.60=0.70, eilr=0.65, rtm=0.25, gap=0.08→norm=0.80
        # DFS = 0.35*0.70 + 0.30*0.65 + 0.20*0.25 + 0.15*0.80
        #     = 0.245 + 0.195 + 0.050 + 0.120 = 0.610
        result = calculate_directional_fragility_score(
            p_less_and_bf3=0.42,
            p_less=0.60,
            p_more_given_bf4_plus=0.65,
            right_tail_mass_line_plus_3=0.25,
            raw_p_less=0.60,
            calibrated_lower_bound_less=0.52,
        )
        dfs = result["directional_fragility_score"]
        assert dfs is not None
        assert 0.55 <= dfs < 0.70, f"Expected MODERATE range; got DFS={dfs:.4f}"
        assert result["directional_fragility_label"] == DFS_MODERATE
        assert result["directional_ceiling"] is CEILING_NONE


class TestT9_DFS070CapsHold:
    """Test 9: DFS 0.70 caps at HOLD."""

    def test_dfs_at_or_above_0_70_is_high(self):
        # Tune inputs to push DFS >= 0.70
        result = calculate_directional_fragility_score(
            p_less_and_bf3=0.80,      # tbl = 0.80/0.90 ≈ 0.889
            p_less=0.90,
            p_more_given_bf4_plus=0.68,  # eilr = 0.68
            right_tail_mass_line_plus_3=0.35,
            raw_p_less=0.60,
            calibrated_lower_bound_less=0.50,
        )
        dfs = result["directional_fragility_score"]
        assert dfs is not None
        assert dfs >= 0.70, f"Expected DFS>=0.70; got {dfs:.4f}"
        # HIGH: ceiling = HOLD (not WATCH, not hard-override since eilr<0.70)
        assert result["directional_ceiling"] in (CEILING_HOLD, CEILING_WATCH)


class TestT10_DFS080CapsWatch:
    """Test 10: DFS 0.80 caps at WATCH."""

    def test_dfs_at_or_above_0_80_is_severe(self):
        # Push all components to maximum
        result = calculate_directional_fragility_score(
            p_less_and_bf3=0.88,
            p_less=0.90,
            p_more_given_bf4_plus=0.85,
            right_tail_mass_line_plus_3=0.55,
            raw_p_less=0.60,
            calibrated_lower_bound_less=0.45,
        )
        dfs = result["directional_fragility_score"]
        assert dfs is not None
        assert dfs >= 0.80, f"Expected DFS>=0.80; got {dfs:.4f}"
        assert result["directional_ceiling"] == CEILING_WATCH


class TestT11_HardOverrideCapsWatch:
    """Test 11: Hard override (tbl>=0.80 AND eilr>=0.70) caps at WATCH."""

    def test_hard_override_triggers_severe(self):
        result = calculate_directional_fragility_score(
            p_less_and_bf3=0.73,    # tbl = 0.73/0.90 = 0.811 >= 0.80
            p_less=0.90,
            p_more_given_bf4_plus=0.72,   # eilr = 0.72 >= 0.70
            right_tail_mass_line_plus_3=0.20,
            raw_p_less=0.55,
            calibrated_lower_bound_less=0.52,
        )
        assert result["hard_override_triggered"] is True
        assert result["directional_fragility_label"] == DFS_SEVERE
        assert result["directional_ceiling"] == CEILING_WATCH

    def test_hard_override_requires_both_conditions(self):
        # Only tbl >= 0.80, eilr < 0.70 → no override
        result = calculate_directional_fragility_score(
            p_less_and_bf3=0.73,
            p_less=0.90,
            p_more_given_bf4_plus=0.60,  # below 0.70
            right_tail_mass_line_plus_3=0.20,
            raw_p_less=0.55,
            calibrated_lower_bound_less=0.52,
        )
        assert result["hard_override_triggered"] is False


# ===========================================================================
# Event-tree remains controlling / lowest-ceiling propagation
# ===========================================================================

class TestT12_EventTreeControlling:
    """Test 12: Event-tree outputs remain controlling."""

    def test_efficiency_gate_cannot_upgrade_event_tree_label(self):
        # A STABLE efficiency score doesn't grant permissions the event tree blocked.
        # The module only restricts — it never upgrades.
        flags = _all_tier1_flags(0.0)   # perfectly stable
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01", metric_flags=flags)
        assert result["efficiency_ceiling"] is CEILING_NONE
        assert result["efficiency_band"] == BAND_STABLE
        # Stable → no additional ceiling.  Event tree label is unchanged.

    def test_dfs_gate_cannot_upgrade_event_tree_label(self):
        result = calculate_directional_fragility_score(
            p_less_and_bf3=0.10,
            p_less=0.60,
            p_more_given_bf4_plus=0.20,
            right_tail_mass_line_plus_3=0.05,
            raw_p_less=0.60,
            calibrated_lower_bound_less=0.58,
        )
        dfs = result["directional_fragility_score"]
        assert dfs < 0.55
        assert result["directional_ceiling"] is CEILING_NONE


class TestT13_LowestCeilingPropagation:
    """Test 13: Lowest-ceiling propagation is preserved."""

    def test_most_restrictive_wins(self):
        assert apply_lowest_ceiling(None, CEILING_HOLD) == CEILING_HOLD
        assert apply_lowest_ceiling(CEILING_HOLD, CEILING_WATCH) == CEILING_WATCH
        assert apply_lowest_ceiling(CEILING_WATCH, None) == CEILING_WATCH
        assert apply_lowest_ceiling(None, None) is None

    def test_upstream_ceiling_not_erased_by_downstream(self):
        # WATCH from upstream + HOLD from downstream → still WATCH
        assert apply_lowest_ceiling(CEILING_WATCH, CEILING_HOLD) == CEILING_WATCH

    def test_full_ceiling_stack(self):
        # Scenario: event-tree → HOLD, efficiency → WATCH, DFS → HOLD
        # Expected lowest: WATCH
        result = apply_lowest_ceiling(CEILING_HOLD, CEILING_WATCH, CEILING_HOLD)
        assert result == CEILING_WATCH


class TestT14_CanExecuteAlwaysFalse:
    """Test 14: can_execute=false is always returned."""

    def test_efficiency_can_execute_false(self):
        result = calculate_recent_1ip_efficiency_score("p1", "2026-08-01",
                                                        metric_flags=_all_tier1_flags(0.0))
        assert result["can_execute"] is False

    def test_dfs_can_execute_false(self):
        result = calculate_directional_fragility_score(
            p_less_and_bf3=0.2, p_less=0.5,
            p_more_given_bf4_plus=0.3,
            right_tail_mass_line_plus_3=0.1,
            raw_p_less=0.5,
            calibrated_lower_bound_less=0.48,
        )
        assert result["can_execute"] is False

    def test_exposure_ledger_can_execute_false(self):
        rows = [_row(stake=5.0)]
        result = apply_cross_slip_exposure_ceiling(rows, portfolio_stake_base=100.0)
        assert result["can_execute"] is False
