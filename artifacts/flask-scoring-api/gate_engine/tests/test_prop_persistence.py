"""
test_prop_persistence.py
Tests for gate_engine/prop_persistence.py
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT
"""
import pytest
from gate_engine.prop_persistence import (
    classify_window_agreement,
    compute_persistence_score,
    compute_threshold_cushion,
    run_inflation_audit,
    run_prop_persistence,
    FULL_ALIGNMENT,
    PARTIAL_ALIGNMENT,
    RECENT_ONLY,
    SEASON_ONLY,
    CONFLICTING_WINDOWS,
    INSUFFICIENT_DATA,
    WINDOW_WEIGHTS,
)


# ---------------------------------------------------------------------------
# classify_window_agreement
# ---------------------------------------------------------------------------

class TestClassifyWindowAgreement:

    def test_full_alignment_when_all_within_10pp(self):
        w = {"l5": 0.70, "l10": 0.72, "l20": 0.74, "season": 0.71, "role_matched": 0.73}
        r = classify_window_agreement(w)
        assert r["agreement"] == FULL_ALIGNMENT
        assert r["recent_form_divergence"] is False

    def test_conflicting_when_spread_exceeds_30pp(self):
        w = {"l5": 0.90, "l10": 0.90, "season": 0.55}
        r = classify_window_agreement(w)
        assert r["agreement"] == CONFLICTING_WINDOWS

    def test_recent_form_divergence_fires_at_20pp_gap(self):
        w = {"l10": 0.90, "season": 0.567}   # gap = 0.333 >= 0.20
        r = classify_window_agreement(w)
        assert r["recent_form_divergence"] is True
        assert r["divergence_detail"] is not None
        assert "RECENT_FORM_DIVERGENCE" in r["divergence_detail"]

    def test_no_divergence_below_20pp_gap(self):
        w = {"l10": 0.70, "season": 0.65}   # gap = 0.05
        r = classify_window_agreement(w)
        assert r["recent_form_divergence"] is False

    def test_recent_only_when_season_absent(self):
        w = {"l5": 0.75, "l10": 0.72, "l20": None}
        r = classify_window_agreement(w)
        assert r["agreement"] == RECENT_ONLY

    def test_season_only_when_recent_absent(self):
        w = {"season": 0.60, "l5": None, "l10": None}
        r = classify_window_agreement(w)
        assert r["agreement"] == SEASON_ONLY

    def test_insufficient_data_when_single_window(self):
        w = {"season": 0.60}
        r = classify_window_agreement(w)
        assert r["agreement"] == SEASON_ONLY

    def test_all_none_is_insufficient(self):
        w = {"l5": None, "l10": None, "season": None}
        r = classify_window_agreement(w)
        assert r["agreement"] == INSUFFICIENT_DATA

    def test_partial_alignment_between_10_and_20pp(self):
        w = {"l10": 0.80, "season": 0.64}   # spread = 0.16, no divergence (0.16 < 0.20)
        r = classify_window_agreement(w)
        assert r["agreement"] == PARTIAL_ALIGNMENT
        assert r["recent_form_divergence"] is False

    def test_regression_kayla_mcbride_pattern(self):
        """L10=90%, season=56.7% from Linemaker analysis should trigger RECENT_FORM_DIVERGENCE."""
        w = {"l10": 0.90, "season": 0.567}
        r = classify_window_agreement(w)
        assert r["recent_form_divergence"] is True
        assert r["window_spread"] is not None
        assert r["window_spread"] >= 0.30
        assert r["agreement"] == CONFLICTING_WINDOWS


# ---------------------------------------------------------------------------
# compute_persistence_score
# ---------------------------------------------------------------------------

class TestComputePersistenceScore:

    def test_weights_sum_correct(self):
        assert abs(sum(WINDOW_WEIGHTS.values()) - 1.0) < 1e-9

    def test_full_windows_weighted_correctly(self):
        # All windows identical = score equals value
        val = 0.70
        w   = {k: val for k in WINDOW_WEIGHTS}
        r   = compute_persistence_score(w)
        assert abs(r["persistence_score"] - val) < 0.001

    def test_partial_windows_redistributes_weight(self):
        # Only role_matched (35%) and season (25%) present → should sum to 1.0 after redistribution
        w = {"role_matched": 0.80, "season": 0.60}
        r = compute_persistence_score(w)
        assert r["persistence_score"] is not None
        wts = r["weight_applied"]
        assert abs(sum(wts.values()) - 1.0) < 1e-3

    def test_no_windows_returns_none(self):
        r = compute_persistence_score({})
        assert r["persistence_score"] is None

    def test_discovery_only_always_true(self):
        r = compute_persistence_score({"l10": 0.75})
        assert r["discovery_only"] is True

    def test_low_persistence_on_conflicting_windows(self):
        w = {"l5": 0.90, "l10": 0.90, "season": 0.40, "role_matched": 0.40}
        r = compute_persistence_score(w)
        # l5+l10 at 0.90, season+role at 0.40 — score should be between 0.40 and 0.90
        assert 0.40 < r["persistence_score"] < 0.90

    def test_l5_weight_smallest(self):
        assert WINDOW_WEIGHTS["l5"] < WINDOW_WEIGHTS["l10"]
        assert WINDOW_WEIGHTS["l5"] < WINDOW_WEIGHTS["season"]
        assert WINDOW_WEIGHTS["l5"] < WINDOW_WEIGHTS["role_matched"]

    def test_role_matched_weight_largest(self):
        assert WINDOW_WEIGHTS["role_matched"] == max(WINDOW_WEIGHTS.values())


# ---------------------------------------------------------------------------
# compute_threshold_cushion
# ---------------------------------------------------------------------------

class TestComputeThresholdCushion:

    def test_positive_cushion_on_overs(self):
        # All games scored 5, line = 3 → cushion = 2.0 for all
        r = compute_threshold_cushion([5, 5, 5, 5, 5], line=3.0, direction="MORE")
        assert abs(r["mean_cushion"] - 2.0) < 0.001
        assert abs(r["median_cushion"] - 2.0) < 0.001
        assert r["p25_cushion"] == r["mean_cushion"]  # all identical

    def test_negative_cushion_on_miss(self):
        # All games scored 2, line = 3 → cushion = -1.0
        r = compute_threshold_cushion([2, 2, 2, 2], line=3.0, direction="MORE")
        assert r["mean_cushion"] < 0

    def test_hit_rate_correct(self):
        # 8 of 10 games above line
        vals = [5, 5, 5, 5, 5, 5, 5, 5, 2, 1]
        r    = compute_threshold_cushion(vals, line=3.0, direction="MORE")
        assert abs(r["hit_rate"] - 0.80) < 0.01

    def test_p25_is_more_conservative_than_mean_for_high_variance(self):
        vals = [10, 9, 8, 0, 0, 0, 12, 11]   # mixed
        r    = compute_threshold_cushion(vals, line=5.0, direction="MORE")
        # p25 should be <= mean for high-variance data
        assert r["p25_cushion"] <= r["mean_cushion"]

    def test_empty_values_returns_none_fields(self):
        r = compute_threshold_cushion([], line=2.5)
        assert r["mean_cushion"] is None
        assert r["n_games"] == 0


# ---------------------------------------------------------------------------
# run_inflation_audit
# ---------------------------------------------------------------------------

class TestRunInflationAudit:

    def test_no_flags_low_risk(self):
        r = run_inflation_audit({}, n_games=10)
        assert r["inflation_risk"] == "LOW"
        assert r["inflation_flags"] == []
        assert r["outlier_or_role_audit_required"] is False

    def test_small_sample_fires_at_7(self):
        r = run_inflation_audit({}, n_games=7)
        assert "small_sample" in r["inflation_flags"]

    def test_small_sample_does_not_fire_at_8(self):
        r = run_inflation_audit({}, n_games=8)
        assert "small_sample" not in r["inflation_flags"]

    def test_two_flags_trigger_audit_required(self):
        row = {"role_change_detected": True, "schedule_strength_flag": True}
        r   = run_inflation_audit(row, n_games=10)
        assert r["outlier_or_role_audit_required"] is True
        assert r["inflation_risk"] in ("MODERATE", "HIGH")

    def test_three_flags_high_risk(self):
        row = {
            "role_change_detected":    True,
            "schedule_strength_flag":  True,
            "outlier_games_detected":  True,
        }
        r = run_inflation_audit(row, n_games=10)
        assert r["inflation_risk"] == "HIGH"


# ---------------------------------------------------------------------------
# run_prop_persistence (integration)
# ---------------------------------------------------------------------------

class TestRunPropPersistence:

    def test_returns_required_top_level_keys(self):
        r = run_prop_persistence({"l10": 0.75, "season": 0.60})
        for key in ("persistence_score", "window_agreement", "research_priority_boost",
                    "discovery_notes", "can_execute"):
            assert key in r, f"Missing key: {key}"

    def test_can_execute_always_false(self):
        r = run_prop_persistence({"l10": 0.80})
        assert r["can_execute"] is False

    def test_high_boost_on_full_alignment_high_score(self):
        w = {k: 0.78 for k in WINDOW_WEIGHTS}
        r = run_prop_persistence(w)
        assert r["research_priority_boost"] == "HIGH"

    def test_boost_cancelled_on_conflicting_windows(self):
        w = {"l5": 0.95, "l10": 0.95, "season": 0.45, "role_matched": 0.45}
        r = run_prop_persistence(w)
        # Even if persistence score is high, conflicting windows cancel boost
        assert r["research_priority_boost"] == "NONE"

    def test_divergence_in_notes_when_l10_season_gap_large(self):
        w = {"l10": 0.90, "season": 0.55}
        r = run_prop_persistence(w)
        notes = " ".join(r["discovery_notes"])
        assert "RECENT_FORM_DIVERGENCE" in notes

    def test_threshold_cushion_computed_when_data_provided(self):
        w    = {"l10": 0.75, "season": 0.70}
        vals = [6.0, 5.5, 7.0, 4.0, 8.0]
        r    = run_prop_persistence(w, stat_values=vals, line=4.5, direction="MORE")
        assert r["threshold_cushion"] is not None
        assert r["threshold_cushion"]["n_games"] == 5

    def test_reminder_note_always_present(self):
        r = run_prop_persistence({"l10": 0.72})
        reminder = next(
            (n for n in r["discovery_notes"] if "REMINDER" in n or "calibrated_lower_bound" in n),
            None,
        )
        assert reminder is not None, "Reminder about calibrated_lower_bound must always appear"
