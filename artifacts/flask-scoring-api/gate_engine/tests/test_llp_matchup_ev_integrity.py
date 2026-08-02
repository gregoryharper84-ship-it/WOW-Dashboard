"""
Tests for gate_engine/llp_matchup_ev_integrity.py
WOW-PATCH-2026-08-02-LLP-MATCHUP-EV-INTEGRITY
"""
import pytest
from gate_engine.llp_matchup_ev_integrity import (
    MATCHUP_PA_FLOOR,
    REQUIRED_EV_FIELDS,
    check_matchup_sample_floor,
    check_absence_of_data_neutrality,
    check_ev_claim_audit,
    check_variance_vs_safety,
    check_upstream_dependency_lock,
    run_matchup_ev_integrity,
)


# ─────────────────────────────────────────────────────────────
# can_execute guard
# ─────────────────────────────────────────────────────────────

def test_can_execute_is_false():
    import gate_engine.llp_matchup_ev_integrity as m
    assert m.can_execute is False


# ─────────────────────────────────────────────────────────────
# Rule 1 — Small-sample matchup floor
# ─────────────────────────────────────────────────────────────

class TestMatchupSampleFloor:
    def test_above_floor_primary_driver_passes(self):
        r = check_matchup_sample_floor(55, is_primary_driver=True)
        assert r.passed
        assert not r.blockers
        assert r.label_ceiling is None

    def test_below_floor_not_primary_driver_warns_not_blocks(self):
        r = check_matchup_sample_floor(14, is_primary_driver=False)
        assert r.passed                     # no block
        assert not r.blockers
        assert any("context only" in w for w in r.warnings)

    def test_below_floor_primary_driver_fails(self):
        r = check_matchup_sample_floor(14, is_primary_driver=True)
        assert not r.passed
        assert any("l5-l10-overtrusted" in b for b in r.blockers)
        assert r.label_ceiling == "WATCH"

    def test_exactly_floor_primary_driver_passes(self):
        r = check_matchup_sample_floor(MATCHUP_PA_FLOOR, is_primary_driver=True)
        assert r.passed

    def test_zero_pa_primary_driver_fails(self):
        r = check_matchup_sample_floor(0, is_primary_driver=True)
        assert not r.passed
        assert r.label_ceiling == "WATCH"

    def test_none_pa_returns_warning_not_blocker(self):
        r = check_matchup_sample_floor(None, is_primary_driver=True)
        assert r.passed
        assert not r.blockers
        assert r.warnings

    def test_detail_populated_on_failure(self):
        r = check_matchup_sample_floor(9, is_primary_driver=True)
        assert r.detail["sample_pa"] == 9
        assert r.detail["floor"] == MATCHUP_PA_FLOOR


# ─────────────────────────────────────────────────────────────
# Rule 2 — Absence-of-data neutrality
# ─────────────────────────────────────────────────────────────

class TestAbsenceOfDataNeutrality:
    def test_clean_note_passes(self):
        r = check_absence_of_data_neutrality("Burns has 42% K rate in 31 PA vs current roster")
        assert r.passed

    def test_zero_career_phrase_fails(self):
        r = check_absence_of_data_neutrality("Bennett has 0 career PA vs Dodgers")
        assert not r.passed
        assert any("reasoned-not-modeled" in b for b in r.blockers)

    def test_no_career_phrase_fails(self):
        r = check_absence_of_data_neutrality("No career matchup history here")
        assert not r.passed

    def test_never_faced_fails(self):
        r = check_absence_of_data_neutrality("Pitcher has never faced this lineup")
        assert not r.passed

    def test_no_history_phrase_fails(self):
        r = check_absence_of_data_neutrality("no matchup history available")
        assert not r.passed

    def test_none_note_passes(self):
        r = check_absence_of_data_neutrality(None)
        assert r.passed

    def test_empty_note_passes(self):
        r = check_absence_of_data_neutrality("")
        assert r.passed

    def test_detected_phrase_in_detail(self):
        r = check_absence_of_data_neutrality("zero career at-bats against this starter")
        assert "detected_phrase" in r.detail


# ─────────────────────────────────────────────────────────────
# Rule 3 — EV-claim audit gate
# ─────────────────────────────────────────────────────────────

class TestEvClaimAudit:
    FULL_CLAIM = {
        "model_prob": 0.64,
        "fair_odds": -178,
        "book": "DraftKings",
        "timestamp": "2026-08-02T13:40Z",
    }

    def test_no_ev_claim_passes(self):
        r = check_ev_claim_audit(None)
        assert r.passed

    def test_full_ev_claim_passes(self):
        r = check_ev_claim_audit(self.FULL_CLAIM)
        assert r.passed

    def test_missing_model_prob_fails(self):
        claim = {**self.FULL_CLAIM, "model_prob": None}
        r = check_ev_claim_audit(claim)
        assert not r.passed
        assert "model_prob" in r.detail["ev_claim_missing_fields"]
        assert any("missing-projection-support" in b for b in r.blockers)

    def test_missing_timestamp_fails(self):
        claim = {k: v for k, v in self.FULL_CLAIM.items() if k != "timestamp"}
        r = check_ev_claim_audit(claim)
        assert not r.passed
        assert "timestamp" in r.detail["ev_claim_missing_fields"]

    def test_missing_book_fails(self):
        claim = {**self.FULL_CLAIM, "book": ""}
        r = check_ev_claim_audit(claim)
        assert not r.passed

    def test_all_fields_missing_fails(self):
        r = check_ev_claim_audit({})
        assert not r.passed
        assert len(r.detail["ev_claim_missing_fields"]) == len(REQUIRED_EV_FIELDS)

    def test_ev_rejected_language_in_blocker(self):
        claim = {**self.FULL_CLAIM, "fair_odds": None}
        r = check_ev_claim_audit(claim)
        assert any("REJECTED" in b for b in r.blockers)


# ─────────────────────────────────────────────────────────────
# Rule 4 — Variance-vs-safety separation
# ─────────────────────────────────────────────────────────────

class TestVarianceVsSafety:
    def test_not_claimed_safer_always_passes(self):
        r = check_variance_vs_safety(0.65, 0.40, is_claimed_safer=False)
        assert r.passed

    def test_claimed_safer_higher_prob_passes(self):
        r = check_variance_vs_safety(0.55, 0.72, is_claimed_safer=True)
        assert r.passed

    def test_claimed_safer_equal_prob_passes(self):
        r = check_variance_vs_safety(0.60, 0.60, is_claimed_safer=True)
        assert r.passed

    def test_claimed_safer_lower_prob_fails(self):
        r = check_variance_vs_safety(0.65, 0.30, is_claimed_safer=True)
        assert not r.passed
        assert any("VARIANCE_INCREASE" in b for b in r.blockers)

    def test_none_prob_warns_not_blocks(self):
        r = check_variance_vs_safety(None, 0.40, is_claimed_safer=True)
        assert r.passed
        assert r.warnings

    def test_detail_populated_on_failure(self):
        r = check_variance_vs_safety(0.65, 0.30, is_claimed_safer=True)
        assert r.detail["original_hit_prob_lb"] == 0.65
        assert r.detail["replacement_hit_prob_lb"] == 0.30

    def test_longshot_prop_replacing_moneyline_fails(self):
        # +326 odds → ~23% implied hit prob; original ML was ~60%
        r = check_variance_vs_safety(0.60, 0.23, is_claimed_safer=True)
        assert not r.passed


# ─────────────────────────────────────────────────────────────
# Rule 5 — Upstream dependency lock
# ─────────────────────────────────────────────────────────────

class TestUpstreamDependencyLock:
    def test_none_status_passes(self):
        r = check_upstream_dependency_lock("Analyzing MLB props", None)
        assert r.passed

    def test_complete_status_passes(self):
        r = check_upstream_dependency_lock("Analyzing MLB props", "complete")
        assert r.passed

    def test_incomplete_status_fails_and_drops(self):
        r = check_upstream_dependency_lock("Analyzing MLB props", "incomplete")
        assert not r.passed
        assert r.dropped
        assert any("PIPELINE_INTEGRITY_FAILURE" in b for b in r.blockers)

    def test_running_status_fails(self):
        r = check_upstream_dependency_lock("Finding props", "running")
        assert not r.passed

    def test_timeout_status_fails(self):
        r = check_upstream_dependency_lock("Finding props", "timed_out")
        assert not r.passed

    def test_error_status_fails(self):
        r = check_upstream_dependency_lock("Finding props", "error")
        assert not r.passed

    def test_unchecked_status_fails(self):
        r = check_upstream_dependency_lock("Analyzing MLB props", "unchecked")
        assert not r.passed
        assert r.dropped

    def test_detail_records_step_name(self):
        r = check_upstream_dependency_lock("Analyzing MLB props", "running")
        assert r.detail["upstream_step_name"] == "Analyzing MLB props"


# ─────────────────────────────────────────────────────────────
# run_matchup_ev_integrity — integration
# ─────────────────────────────────────────────────────────────

class TestRunMatchupEvIntegrity:
    def test_fully_clean_row_passes(self):
        row = {
            "matchup_sample_pa": 55,
            "matchup_is_primary_driver": False,
            "matchup_note": "Burns has 42% K rate in 55 PA vs current roster",
            "ev_claim": {
                "model_prob": 0.64,
                "fair_odds": -178,
                "book": "DraftKings",
                "timestamp": "2026-08-02T13:40Z",
            },
            "original_hit_prob_lb": 0.60,
            "replacement_hit_prob_lb": 0.72,
            "is_claimed_safer": True,
            "upstream_step_name": "Analyzing MLB props",
            "upstream_step_status": "complete",
        }
        r = run_matchup_ev_integrity(row)
        assert r.passed
        assert not r.blockers

    def test_spec_positive_test_case(self):
        """Exact positive test from patch spec — Cincinnati Reds ML."""
        row = {
            "matchup_sample_pa": 55,
            "matchup_is_primary_driver": False,
            "matchup_note": "Burns has .212 avg / 38.2% K rate vs Pirates in 55 PA (context only)",
            "ev_claim": {
                "model_prob": 0.64,
                "fair_odds": -178,
                "book": "DraftKings",
                "timestamp": "2026-08-02T13:40Z",
            },
            "upstream_step_name": "Analyzing MLB props",
            "upstream_step_status": "complete",
        }
        r = run_matchup_ev_integrity(row)
        assert r.passed

    def test_spec_negative_test_case(self):
        """Exact negative test from patch spec — Boston Red Sox ML."""
        row = {
            "matchup_sample_pa": 14,
            "matchup_is_primary_driver": True,
            "matchup_note": "Bennett has 0 career PA vs Dodgers",
            "ev_claim": None,
            "upstream_step_name": "Analyzing MLB props",
            "upstream_step_status": "incomplete",
        }
        r = run_matchup_ev_integrity(row)
        assert not r.passed
        assert r.label_ceiling == "WATCH"
        assert r.dropped
        # Should have blockers from sample floor + absence-of-data + pipeline lock
        blocker_text = " ".join(r.blockers)
        assert "l5-l10-overtrusted" in blocker_text
        assert "DATA_UNAVAILABLE" in blocker_text or "reasoned-not-modeled" in blocker_text
        assert "PIPELINE_INTEGRITY_FAILURE" in blocker_text

    def test_multiple_violations_all_collected(self):
        row = {
            "matchup_sample_pa": 9,
            "matchup_is_primary_driver": True,
            "ev_claim": {"model_prob": 0.55},  # missing 3 fields
            "is_claimed_safer": True,
            "original_hit_prob_lb": 0.65,
            "replacement_hit_prob_lb": 0.25,
            "upstream_step_status": "running",
        }
        r = run_matchup_ev_integrity(row)
        assert not r.passed
        assert len(r.blockers) >= 3

    def test_empty_row_passes(self):
        r = run_matchup_ev_integrity({})
        assert r.passed
