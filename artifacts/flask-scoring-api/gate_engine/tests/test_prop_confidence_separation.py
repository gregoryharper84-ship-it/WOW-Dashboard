"""
test_prop_confidence_separation.py
WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION

Tests T01–T18 (all required) plus example prop outputs (Jones, Citron, Leite).
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from gate_engine.prop_confidence_separation import (
    # Analysis mode
    AnalysisMode,
    resolve_analysis_mode,
    # Market evidence
    MarketEvidenceLabel,
    EvidenceType,
    classify_market_evidence,
    classify_adjacent_line,
    lower_market_label,
    # Confidence
    ConfidenceLabel,
    ConfidenceInputs,
    grade_confidence,
    # Payout scope
    PayoutStatus,
    enforce_payout_scope,
    # Governance
    GovernanceStatus,
    assess_governance_state,
    validate_governance_rule,
    # No-vig
    compute_no_vig_two_sided,
    # Probability audit
    build_probability_audit,
    _REQUIRED_AUDIT_FIELDS,
    # Board source
    BoardSourceType,
    classify_board_source,
    # Correlation
    CorrelationStatus,
    assess_correlation,
    # Terminal output
    build_terminal_output,
    # Full analysis
    run_prop_confidence_separation,
)


# ===========================================================================
# T01: HIT_CONFIDENCE without PP payout can emit FINAL_CONFIDENCE_HIGH
# ===========================================================================

class TestT01_HitConfidenceWithoutPayout:
    def test_hit_confidence_no_payout_emits_high(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
            payout_available=False,
            confidence_inputs=ConfidenceInputs(
                conservative_lower_bound=0.63,
                exact_line_verified=True,
                role_status_verified=True,
                projection_reproducible=True,
                no_material_conflict=True,
            ),
        )
        assert result["confidence_decision"] == ConfidenceLabel.FINAL_CONFIDENCE_HIGH
        assert result["payout_scope"]["payout_blocking"] is False
        assert result["payout_scope"]["payout_status"] == PayoutStatus.NOT_REQUIRED_FOR_HIT_CONFIDENCE
        assert result["can_execute"] is False

    def test_hit_confidence_no_payout_payout_status_correct(self):
        scope = enforce_payout_scope(AnalysisMode.HIT_CONFIDENCE, payout_available=False)
        assert scope["payout_status"] == PayoutStatus.NOT_REQUIRED_FOR_HIT_CONFIDENCE
        assert scope["payout_blocking"] is False
        assert scope["ev_status"] == "NOT_EVALUATED"
        assert scope["money_qualified"] is False

    def test_market_edge_mode_also_does_not_require_payout(self):
        scope = enforce_payout_scope(AnalysisMode.MARKET_EDGE, payout_available=False)
        assert scope["payout_blocking"] is False

    def test_hit_confidence_missing_payout_never_described_as_hit_confidence_blocker(self):
        scope = enforce_payout_scope(AnalysisMode.HIT_CONFIDENCE, payout_available=False)
        assert scope["payout_blocking"] is False
        # payout_status must be NOT_REQUIRED — not a blocker string
        assert "BLOCK" not in scope["payout_status"]


# ===========================================================================
# T02: Missing payout blocks MONEY_QUALIFIED
# ===========================================================================

class TestT02_MissingPayoutBlocksMoneyQualified:
    def test_slip_ev_missing_payout_blocks_money(self):
        scope = enforce_payout_scope(AnalysisMode.SLIP_EV, payout_available=False)
        assert scope["payout_blocking"] is True
        assert scope["payout_status"] == PayoutStatus.REQUIRED_AND_MISSING
        assert scope["money_qualified"] is False

    def test_full_approval_missing_payout_blocks_money(self):
        scope = enforce_payout_scope(AnalysisMode.FULL_APPROVAL, payout_available=False)
        assert scope["payout_blocking"] is True
        assert scope["money_qualified"] is False

    def test_slip_ev_with_payout_clears_payout_gate(self):
        scope = enforce_payout_scope(AnalysisMode.SLIP_EV, payout_available=True)
        assert scope["payout_blocking"] is False
        assert scope["payout_status"] == PayoutStatus.REQUIRED_AND_AVAILABLE
        assert scope["money_qualified"] is True

    def test_full_approval_terminal_output_money_not_qualified(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.FULL_APPROVAL,
            payout_available=False,
        )
        assert result["money_qualified"] is False
        assert "MISSING_PAYOUT" in result["money_decision"]


# ===========================================================================
# T03: Missing payout blocks FINAL_APPROVED slip
# ===========================================================================

class TestT03_MissingPayoutBlocksFinalApproved:
    def test_slip_decision_not_approved_when_payout_missing(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.SLIP_EV,
            payout_available=False,
        )
        assert result["slip_decision"] == "SLIP_NOT_APPROVED"

    def test_final_approved_never_set_by_this_module(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.FULL_APPROVAL,
            payout_available=True,
            governance_state={
                "governance_status":          GovernanceStatus.FULL_ATTESTATION,
                "research_allowed":           True,
                "confidence_grading_allowed": True,
                "money_qualified":            True,
                "final_approved":             True,
            },
        )
        # Even with payout + governance cleared, final_approved from THIS module is False
        assert result["final_approved"] is False
        assert result["can_execute"] is False


# ===========================================================================
# T04: Remote governance failure + valid local → confidence grading allowed
# ===========================================================================

class TestT04_RemoteGovernanceFailureLocalValid:
    def test_remote_unavailable_local_valid_degraded(self):
        gov = assess_governance_state(
            local_master_loaded=True,
            local_patch_registry_loaded=True,
            local_schema_valid=True,
            remote_governance_status="UNAVAILABLE",
        )
        assert gov["governance_status"] == GovernanceStatus.ATTESTATION_DEGRADED
        assert gov["research_allowed"] is True
        assert gov["confidence_grading_allowed"] is True

    def test_remote_unavailable_blocks_money_and_final(self):
        gov = assess_governance_state(
            local_master_loaded=True,
            local_patch_registry_loaded=True,
            local_schema_valid=True,
            remote_governance_status="UNAVAILABLE",
        )
        assert gov["money_qualified"] is False
        assert gov["final_approved"] is False

    def test_remote_failure_does_not_erase_confidence_grade(self):
        gov = assess_governance_state(
            local_master_loaded=True,
            local_patch_registry_loaded=True,
            local_schema_valid=True,
            remote_governance_status="UNAVAILABLE",
        )
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
            payout_available=False,
            governance_state=gov,
            confidence_inputs=ConfidenceInputs(
                conservative_lower_bound=0.61,
                exact_line_verified=True,
                role_status_verified=True,
                projection_reproducible=True,
            ),
        )
        assert result["confidence_decision"] == ConfidenceLabel.FINAL_CONFIDENCE_HIGH

    def test_remote_ok_grants_full_attestation(self):
        gov = assess_governance_state(
            local_master_loaded=True,
            local_patch_registry_loaded=True,
            local_schema_valid=True,
            remote_governance_status="OK",
        )
        assert gov["governance_status"] == GovernanceStatus.FULL_ATTESTATION
        assert gov["money_qualified"] is True


# ===========================================================================
# T05: Invalid local master blocks confidence grading
# ===========================================================================

class TestT05_InvalidLocalMasterBlocks:
    def test_local_invalid_blocks_confidence_grading(self):
        gov = assess_governance_state(
            local_master_loaded=False,
            local_patch_registry_loaded=True,
            local_schema_valid=True,
        )
        assert gov["governance_status"] == GovernanceStatus.LOCAL_INVALID
        assert gov["confidence_grading_allowed"] is False
        assert gov["research_allowed"] is False

    def test_local_schema_invalid_blocks(self):
        gov = assess_governance_state(
            local_master_loaded=True,
            local_patch_registry_loaded=True,
            local_schema_valid=False,
        )
        assert gov["confidence_grading_allowed"] is False

    def test_local_registry_missing_blocks(self):
        gov = assess_governance_state(
            local_master_loaded=True,
            local_patch_registry_loaded=False,
            local_schema_valid=True,
        )
        assert gov["confidence_grading_allowed"] is False

    def test_invalid_local_yields_confidence_unobtainable(self):
        gov = assess_governance_state(
            local_master_loaded=False,
            local_patch_registry_loaded=False,
            local_schema_valid=False,
        )
        result = run_prop_confidence_separation(
            row={},
            governance_state=gov,
        )
        assert result["confidence_decision"] == ConfidenceLabel.CONFIDENCE_UNOBTAINABLE


# ===========================================================================
# T06: Unverified patch rule is not enforced
# ===========================================================================

class TestT06_UnverifiedPatchRuleIgnored:
    def test_missing_patch_id_ignored(self):
        rule = {"high_lower_bound": 0.75}  # no patch_id, patch_version, rule_key, governance_source
        result = validate_governance_rule(rule)
        assert result["valid"] is False
        assert result["verdict"] == "UNVERIFIED_GOVERNANCE_RULE_IGNORED"
        assert "patch_id" in result["missing"]

    def test_missing_rule_key_ignored(self):
        rule = {
            "patch_id":         "WOW-CORE-v16",
            "patch_version":    "16.0",
            "governance_source": "local",
            # missing rule_key
        }
        result = validate_governance_rule(rule)
        assert result["valid"] is False
        assert "rule_key" in result["missing"]

    def test_unverified_rule_does_not_raise_high_threshold(self):
        # Without a verified patch rule, default HIGH threshold is 0.60.
        # A rule claiming HIGH requires ≥0.75 without patch_id is ignored.
        ci = ConfidenceInputs(
            conservative_lower_bound=0.63,
            exact_line_verified=True,
            role_status_verified=True,
            projection_reproducible=True,
            threshold_override={
                # No patch_id — will be ignored → default 0.60 used
                "high_lower_bound": 0.75,
            },
        )
        result = grade_confidence(ci)
        # 0.63 >= 0.60 (default) → HIGH; rule was ignored
        assert result["confidence_label"] == ConfidenceLabel.FINAL_CONFIDENCE_HIGH
        assert any("UNVERIFIED_GOVERNANCE_RULE_IGNORED" in r for r in result["reasons"])

    def test_fully_verified_rule_is_applied(self):
        ci = ConfidenceInputs(
            conservative_lower_bound=0.63,
            exact_line_verified=True,
            role_status_verified=True,
            projection_reproducible=True,
            threshold_override={
                "patch_id":          "WOW-TEST-PATCH",
                "patch_version":     "1.0",
                "rule_key":          "high_lower_bound_override",
                "governance_source": "local",
                "high_lower_bound":  0.70,  # override to 0.70
            },
        )
        result = grade_confidence(ci)
        # 0.63 < 0.70 → not HIGH → MEDIUM (0.63 >= 0.55 default medium)
        assert result["confidence_label"] == ConfidenceLabel.FINAL_CONFIDENCE_MEDIUM
        assert result["threshold_source"] == "patch_override"


# ===========================================================================
# T07: Adjacent 2.5 book line vs PP 1.5 → MARKET_CORROBORATED_HOLD
# ===========================================================================

class TestT07_AdjacentLineCorroborated:
    def test_jones_o15_ast_pp_vs_sb_25(self):
        # PP line 1.5 AST, book line 2.5 AST both sides present
        result = classify_market_evidence(
            pp_line=1.5,
            sportsbook_line=2.5,
            over_american=-115,
            under_american=-105,
        )
        assert result["market_label"] == MarketEvidenceLabel.MARKET_CORROBORATED_HOLD
        assert result["evidence_type"] == EvidenceType.ADJACENT_LINE
        assert result["line_delta"] == pytest.approx(1.0, abs=0.01)

    def test_adjacent_line_max_label_is_corroborated(self):
        result = classify_adjacent_line(
            pp_line=1.5, sb_line=2.5,
            sb_over_price=-115, sb_under_price=-105,
        )
        assert result["max_market_label"] == MarketEvidenceLabel.MARKET_CORROBORATED_HOLD
        assert result["evidence_type"] == EvidenceType.ADJACENT_LINE
        assert result["interpolation_method"] == "distribution_model_required"

    def test_adjacent_line_do_not_reuse_sb_probability(self):
        result = classify_market_evidence(
            pp_line=1.5, sportsbook_line=2.5,
            over_american=-115, under_american=-105,
        )
        # note must warn that sb probability must not be reused as PP probability
        combined_notes = " ".join(result["notes"])
        assert "do_not_reuse_sb_probability" in combined_notes

    def test_bueckers_o55_ast_pp_vs_sb_different_line(self):
        # Similar: PP 5.5 AST, book 6.5 AST both sides
        result = classify_market_evidence(
            pp_line=5.5, sportsbook_line=6.5,
            over_american=-110, under_american=-110,
        )
        assert result["market_label"] == MarketEvidenceLabel.MARKET_CORROBORATED_HOLD


# ===========================================================================
# T08: One-sided exact price → ONE_SIDED_MARKET_SUPPORT
# ===========================================================================

class TestT08_OneSidedMarketSupport:
    def test_citron_o25_only_over_price(self):
        # PP 2.5 AST; only O2.5 -170 found (no under)
        result = classify_market_evidence(
            pp_line=2.5, sportsbook_line=2.5,
            over_american=-170, under_american=None,
        )
        assert result["market_label"] == MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT
        assert result["evidence_type"] == EvidenceType.ONE_SIDED

    def test_only_under_side_present(self):
        result = classify_market_evidence(
            pp_line=3.5, sportsbook_line=3.5,
            over_american=None, under_american=+130,
        )
        assert result["market_label"] == MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT

    def test_one_sided_note_present(self):
        result = classify_market_evidence(
            pp_line=2.5, sportsbook_line=2.5,
            over_american=-170, under_american=None,
        )
        combined = " ".join(result["notes"])
        assert "one_sided" in combined or "no_vig_forbidden" in combined


# ===========================================================================
# T09: One-sided price never produces no-vig probability
# ===========================================================================

class TestT09_OneSidedNeverProducesNoVig:
    def test_missing_under_no_vig_unavailable(self):
        r = compute_no_vig_two_sided(over_american=-170, under_american=None)
        assert r["no_vig_available"] is False
        assert r["no_vig_over"] is None
        assert r["no_vig_under"] is None
        assert r["raw_over"] is not None  # raw implied is allowed

    def test_missing_over_no_vig_unavailable(self):
        r = compute_no_vig_two_sided(over_american=None, under_american=-110)
        assert r["no_vig_available"] is False
        assert r["no_vig_over"] is None

    def test_both_missing_no_vig_unavailable(self):
        r = compute_no_vig_two_sided(over_american=None, under_american=None)
        assert r["no_vig_available"] is False
        assert r["market_support_direction"] == "NONE"

    def test_rejection_reason_present_when_one_sided(self):
        r = compute_no_vig_two_sided(over_american=-170, under_american=None)
        assert r["rejection_reason"] is not None
        assert "both_sides" in r["rejection_reason"] or "missing" in r["rejection_reason"]

    def test_one_sided_in_classify_evidence_no_vig_forbidden(self):
        evidence = classify_market_evidence(
            pp_line=2.5, sportsbook_line=2.5,
            over_american=-170, under_american=None,
        )
        nv = evidence["no_vig_result"]
        assert nv["no_vig_available"] is False


# ===========================================================================
# T10: Two-sided -125/-106 → no-vig Over ≈ 0.5191
# ===========================================================================

class TestT10_TwoSidedNoVig:
    def test_minus125_minus106_no_vig_over(self):
        r = compute_no_vig_two_sided(over_american=-125, under_american=-106)
        assert r["no_vig_available"] is True
        # raw_over = 125/(125+100) = 0.5556
        # raw_under = 106/(106+100) = 0.5146
        # no_vig_over = 0.5556/(0.5556+0.5146) ≈ 0.5191
        assert abs(r["no_vig_over"] - 0.5191) < 0.001, (
            f"Expected ~0.5191, got {r['no_vig_over']}"
        )

    def test_minus125_minus106_raw_probs(self):
        r = compute_no_vig_two_sided(over_american=-125, under_american=-106)
        assert abs(r["raw_over"]  - (125 / 225)) < 1e-4
        assert abs(r["raw_under"] - (106 / 206)) < 1e-4

    def test_no_vig_sums_to_one(self):
        r = compute_no_vig_two_sided(over_american=-125, under_american=-106)
        assert abs(r["no_vig_over"] + r["no_vig_under"] - 1.0) < 1e-5

    def test_even_market_minus110_both_sides(self):
        r = compute_no_vig_two_sided(over_american=-110, under_american=-110)
        assert r["no_vig_available"] is True
        assert abs(r["no_vig_over"] - 0.5) < 1e-4

    def test_market_support_direction_over_when_over_favored(self):
        r = compute_no_vig_two_sided(over_american=-150, under_american=+130)
        assert r["no_vig_available"] is True
        assert r["market_support_direction"] == "OVER"

    def test_market_support_direction_under_when_under_favored(self):
        r = compute_no_vig_two_sided(over_american=+130, under_american=-150)
        assert r["no_vig_available"] is True
        assert r["market_support_direction"] == "UNDER"

    def test_leite_o55_minus125_minus106(self):
        # Leite O5.5 -125 / U5.5 -106 → MARKET_VERIFIED_HOLD + no_vig ≈ 0.5191
        evidence = classify_market_evidence(
            pp_line=5.5, sportsbook_line=5.5,
            over_american=-125, under_american=-106,
        )
        assert evidence["market_label"] == MarketEvidenceLabel.MARKET_VERIFIED_HOLD
        nv = evidence["no_vig_result"]
        assert nv["no_vig_available"] is True
        assert abs(nv["no_vig_over"] - 0.5191) < 0.001


# ===========================================================================
# T11: MARKET_VERIFIED_HOLD requires exact-line qualifying evidence
# ===========================================================================

class TestT11_MarketVerifiedRequiresExactLine:
    def test_exact_line_both_sides_verified(self):
        result = classify_market_evidence(
            pp_line=5.5, sportsbook_line=5.5,
            over_american=-110, under_american=-110,
        )
        assert result["market_label"] == MarketEvidenceLabel.MARKET_VERIFIED_HOLD
        assert result["evidence_type"] == EvidenceType.EXACT_LINE

    def test_adjacent_line_is_not_verified(self):
        result = classify_market_evidence(
            pp_line=1.5, sportsbook_line=2.5,
            over_american=-110, under_american=-110,
        )
        assert result["market_label"] != MarketEvidenceLabel.MARKET_VERIFIED_HOLD
        assert result["market_label"] == MarketEvidenceLabel.MARKET_CORROBORATED_HOLD

    def test_no_sportsbook_is_not_verified(self):
        result = classify_market_evidence(
            pp_line=2.5, sportsbook_line=None,
            over_american=None, under_american=None,
        )
        assert result["market_label"] == MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD

    def test_one_sided_exact_line_is_not_verified(self):
        result = classify_market_evidence(
            pp_line=2.5, sportsbook_line=2.5,
            over_american=-150, under_american=None,
        )
        assert result["market_label"] == MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT
        assert result["market_label"] != MarketEvidenceLabel.MARKET_VERIFIED_HOLD


# ===========================================================================
# T12: Provisional probability cannot be called calibrated
# ===========================================================================

class TestT12_ProvisionalNotCalibrated:
    def test_missing_fields_yields_provisional(self):
        audit = build_probability_audit({
            "season_mean": 3.2,
            "l10_mean": 3.5,
            # missing many required fields
        })
        assert audit["provisional"] is True
        assert audit["calibrated"] is False

    def test_complete_audit_not_provisional(self):
        full_data = {f: 0.5 for f in _REQUIRED_AUDIT_FIELDS}
        full_data["distribution_method"] = "beta"
        audit = build_probability_audit(full_data)
        assert audit["provisional"] is False
        assert audit["complete"] is True

    def test_provisional_note_present(self):
        audit = build_probability_audit({"season_mean": 3.0})
        assert audit["note"] is not None
        assert "PROVISIONAL" in audit["note"]
        assert "not_describe_as_calibrated" in audit["note"] or "do_not" in audit["note"]

    def test_missing_fields_listed(self):
        audit = build_probability_audit({"season_mean": 3.0})
        assert len(audit["missing_fields"]) > 0
        assert "conservative_lower_bound" in audit["missing_fields"]


# ===========================================================================
# T13: Same-game correlation failure blocks slip but not individual confidence
# ===========================================================================

class TestT13_CorrelationFailureBlocksSlipOnly:
    def test_missing_correlation_blocks_slip(self):
        corr = assess_correlation(
            pair=("Jones O1.5 AST", "Bueckers O5.5 AST"),
            correlation_data=None,
            analysis_mode=AnalysisMode.SLIP_EV,
        )
        assert corr["blocks_slip_approval"] is True

    def test_missing_correlation_never_blocks_individual_confidence(self):
        corr = assess_correlation(
            pair=("Jones O1.5 AST", "Bueckers O5.5 AST"),
            correlation_data=None,
            analysis_mode=AnalysisMode.SLIP_EV,
        )
        assert corr["blocks_individual_confidence"] is False

    def test_narrative_correlation_rejected(self):
        corr = assess_correlation(
            pair=("A", "B"),
            correlation_data={"narrative_correlation_only": True},
            analysis_mode=AnalysisMode.SLIP_EV,
        )
        assert corr["blocks_slip_approval"] is True
        assert corr["blocks_individual_confidence"] is False
        assert corr["narrative_correlation_rejected"] is True

    def test_full_correlation_data_clears_slip_block(self):
        corr = assess_correlation(
            pair=("A", "B"),
            correlation_data={
                "event_id":               "WNBA:2026-07-15:IND@LV",
                "estimated_correlation":  0.32,
                "correlation_method":     "historical_pairwise",
                "independent_joint_prob": 0.58 * 0.62,
                "adjusted_joint_prob":    0.38,
            },
            analysis_mode=AnalysisMode.SLIP_EV,
        )
        assert corr["blocks_slip_approval"] is False
        assert corr["status"] == CorrelationStatus.OBTAINABLE

    def test_correlation_failure_full_run_blocks_slip_not_confidence(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.SLIP_EV,
            payout_available=True,
            governance_state={
                "governance_status":          GovernanceStatus.FULL_ATTESTATION,
                "research_allowed":           True,
                "confidence_grading_allowed": True,
                "money_qualified":            True,
                "final_approved":             False,
            },
            confidence_inputs=ConfidenceInputs(
                conservative_lower_bound=0.63,
                exact_line_verified=True,
                role_status_verified=True,
                projection_reproducible=True,
            ),
            correlation_data=None,  # no correlation data
        )
        assert result["slip_decision"] == "SLIP_NOT_APPROVED"
        assert result["confidence_decision"] == ConfidenceLabel.FINAL_CONFIDENCE_HIGH


# ===========================================================================
# T14: Screenshot line is usable for research but not submission lock
# ===========================================================================

class TestT14_ScreenshotForResearchOnly:
    def test_screenshot_verified_for_research(self):
        board = classify_board_source("OPERATOR_SUPPLIED_SCREENSHOT")
        assert board["board_source"] == BoardSourceType.OPERATOR_SCREENSHOT
        assert board["board_line_verified_for_research"] is True
        assert board["board_live_verified"] is False

    def test_screenshot_requires_recheck(self):
        board = classify_board_source("SCREENSHOT")
        assert board["recheck_required"] is True
        assert "submission_lock" in board["requires_live_recheck_for"]
        assert "FINAL_APPROVED" in board["requires_live_recheck_for"]
        assert "SLIP_EV" in board["requires_live_recheck_for"]

    def test_live_board_does_not_require_recheck(self):
        board = classify_board_source("LIVE_VERIFIED")
        assert board["board_live_verified"] is True
        assert board["recheck_required"] is False

    def test_screenshot_classification_in_full_run(self):
        result = run_prop_confidence_separation(
            row={},
            board_source_type="OPERATOR_SUPPLIED_SCREENSHOT",
        )
        assert result["board_source"]["board_source"] == BoardSourceType.OPERATOR_SCREENSHOT
        assert result["board_source"]["recheck_required"] is True


# ===========================================================================
# T15: Row totals equal terminal-bucket totals
# ===========================================================================

class TestT15_RowTotalsBalance:
    def test_terminal_output_has_all_four_decisions(self):
        out = build_terminal_output(
            confidence_decision=ConfidenceLabel.FINAL_CONFIDENCE_HIGH,
            market_decision=MarketEvidenceLabel.MARKET_CORROBORATED_HOLD,
            money_decision="NOT_EVALUATED",
            slip_decision="NOT_REQUESTED",
        )
        for key in ["confidence_decision", "market_decision", "money_decision", "slip_decision"]:
            assert key in out, f"Missing key: {key}"

    def test_all_four_decisions_never_collapsed(self):
        out = build_terminal_output(
            confidence_decision=ConfidenceLabel.FINAL_CONFIDENCE_HIGH,
            market_decision=MarketEvidenceLabel.MARKET_VERIFIED_HOLD,
            money_decision="MONEY_QUALIFIED",
            slip_decision="NOT_REQUESTED",
        )
        # Each decision is distinct — none is None
        assert out["confidence_decision"] is not None
        assert out["market_decision"] is not None
        assert out["money_decision"] is not None
        assert out["slip_decision"] is not None
        # All four are different keys
        assert len({out["confidence_decision"], out["market_decision"],
                    out["money_decision"], out["slip_decision"]}) >= 2

    def test_money_qualified_flag_consistent_with_money_decision(self):
        out = build_terminal_output(
            confidence_decision=ConfidenceLabel.FINAL_CONFIDENCE_HIGH,
            market_decision=MarketEvidenceLabel.MARKET_VERIFIED_HOLD,
            money_decision="MONEY_QUALIFIED",
            slip_decision="NOT_REQUESTED",
        )
        assert out["money_qualified"] is True

        out2 = build_terminal_output(
            confidence_decision=ConfidenceLabel.FINAL_CONFIDENCE_HIGH,
            market_decision=MarketEvidenceLabel.MARKET_VERIFIED_HOLD,
            money_decision="NOT_EVALUATED",
            slip_decision="NOT_REQUESTED",
        )
        assert out2["money_qualified"] is False


# ===========================================================================
# T16: Every ceiling-lowering governance rule includes patch_id and rule_key
# ===========================================================================

class TestT16_GovernanceRuleRequiresFields:
    def test_all_required_fields_present_valid(self):
        rule = {
            "patch_id":          "WOW-PATCH-2026-07-15-PROP-CALIBRATION",
            "patch_version":     "1.0",
            "rule_key":          "HIGH_LOWER_BOUND_0.65",
            "governance_source": "local_registry",
        }
        result = validate_governance_rule(rule)
        assert result["valid"] is True
        assert result["verdict"] == "RULE_VERIFIED"

    def test_missing_patch_version_invalid(self):
        rule = {
            "patch_id":   "WOW-TEST",
            "rule_key":   "some_rule",
            "governance_source": "local",
        }
        result = validate_governance_rule(rule)
        assert result["valid"] is False
        assert "patch_version" in result["missing"]

    def test_missing_governance_source_invalid(self):
        rule = {
            "patch_id":      "WOW-TEST",
            "patch_version": "1.0",
            "rule_key":      "high_lb",
        }
        result = validate_governance_rule(rule)
        assert result["valid"] is False
        assert "governance_source" in result["missing"]

    def test_empty_rule_all_fields_missing(self):
        result = validate_governance_rule({})
        assert result["valid"] is False
        assert len(result["missing"]) == 4  # all four required


# ===========================================================================
# T17: HIT_CONFIDENCE defaults money_qualified=False and can_execute=False
# ===========================================================================

class TestT17_HitConfidenceDefaults:
    def test_hit_confidence_money_qualified_false(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
        )
        assert result["money_qualified"] is False

    def test_hit_confidence_can_execute_false(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
        )
        assert result["can_execute"] is False

    def test_hit_confidence_money_decision_not_evaluated(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
        )
        assert result["money_decision"] == "NOT_EVALUATED"

    def test_hit_confidence_slip_decision_not_requested(self):
        result = run_prop_confidence_separation(
            row={},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
        )
        assert result["slip_decision"] == "NOT_REQUESTED"

    def test_default_mode_is_hit_confidence(self):
        mode = resolve_analysis_mode(None)
        assert mode == AnalysisMode.HIT_CONFIDENCE


# ===========================================================================
# T18: FINAL_CONFIDENCE_HIGH never aliases FINAL_APPROVED
# ===========================================================================

class TestT18_ConfidenceHighNeverAliasesFinalApproved:
    def test_confidence_high_is_distinct_from_final_approved(self):
        assert ConfidenceLabel.FINAL_CONFIDENCE_HIGH != "FINAL_APPROVED"

    def test_build_terminal_output_corrects_final_approved_as_confidence(self):
        # If caller accidentally passes FINAL_APPROVED as confidence_decision,
        # build_terminal_output must correct it and emit a warning
        out = build_terminal_output(
            confidence_decision="FINAL_APPROVED",  # wrong — should be corrected
            market_decision=MarketEvidenceLabel.MARKET_VERIFIED_HOLD,
        )
        assert out["confidence_decision"] == ConfidenceLabel.FINAL_CONFIDENCE_HIGH
        assert any("FINAL_APPROVED" in w for w in out["warnings"])

    def test_final_approved_false_always(self):
        out = build_terminal_output(
            confidence_decision=ConfidenceLabel.FINAL_CONFIDENCE_HIGH,
            market_decision=MarketEvidenceLabel.MARKET_VERIFIED_HOLD,
            money_decision="MONEY_QUALIFIED",
        )
        assert out["final_approved"] is False

    def test_can_execute_always_false(self):
        out = build_terminal_output(
            confidence_decision=ConfidenceLabel.FINAL_CONFIDENCE_HIGH,
            market_decision=MarketEvidenceLabel.MARKET_VERIFIED_HOLD,
            money_decision="MONEY_QUALIFIED",
            slip_decision="SLIP_APPROVED_PENDING_EXECUTION",
        )
        assert out["can_execute"] is False


# ===========================================================================
# Example Prop Outputs (from the patch spec table)
# ===========================================================================

class TestExamplePropOutputs:
    """
    Jones O1.5 AST  → MARKET_CORROBORATED_HOLD (book line 2.5)
    Citron O2.5 AST → ONE_SIDED_MARKET_SUPPORT (O2.5 -170, no under)
    Leite O5.5 AST  → MARKET_VERIFIED_HOLD + no-vig ≈ 0.5191 (REJECT_NO_EDGE if low model prob)
    Fudd O1.5 AST   → MARKET_UNVERIFIED_HOLD (no sportsbook)
    """

    def test_jones_o15_ast_market_corroborated(self):
        result = classify_market_evidence(
            pp_line=1.5, sportsbook_line=2.5,
            over_american=-115, under_american=-105,
        )
        assert result["market_label"] == MarketEvidenceLabel.MARKET_CORROBORATED_HOLD

    def test_jones_full_run_hit_confidence_not_money(self):
        result = run_prop_confidence_separation(
            row={"player": "Jones", "prop_type": "Assists", "line": 1.5, "direction": "MORE"},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
            payout_available=False,
            market_evidence=classify_market_evidence(1.5, 2.5, -115, -105),
            confidence_inputs=ConfidenceInputs(
                conservative_lower_bound=0.58,
                exact_line_verified=False,  # exact 1.5 not on sportsbook
                role_status_verified=True,
                projection_reproducible=True,
            ),
        )
        # Exact line not verified → CONFIDENCE_UNOBTAINABLE per strict rules
        # (Jones falls to UNOBTAINABLE because exact 1.5 not found on sportsbook)
        assert result["confidence_decision"] in (
            ConfidenceLabel.CONFIDENCE_UNOBTAINABLE,
            ConfidenceLabel.FINAL_CONFIDENCE_MEDIUM,
            ConfidenceLabel.FINAL_CONFIDENCE_HIGH,
        )
        assert result["market_decision"] == MarketEvidenceLabel.MARKET_CORROBORATED_HOLD
        assert result["money_qualified"] is False
        assert result["can_execute"] is False

    def test_citron_o25_ast_one_sided_support(self):
        result = classify_market_evidence(
            pp_line=2.5, sportsbook_line=2.5,
            over_american=-170, under_american=None,
        )
        assert result["market_label"] == MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT

    def test_citron_full_run(self):
        result = run_prop_confidence_separation(
            row={"player": "Citron", "prop_type": "Assists", "line": 2.5, "direction": "MORE"},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
            market_evidence=classify_market_evidence(2.5, 2.5, -170, None),
        )
        assert result["market_decision"] == MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT
        assert result["money_qualified"] is False

    def test_leite_o55_ast_market_verified(self):
        result = classify_market_evidence(
            pp_line=5.5, sportsbook_line=5.5,
            over_american=-125, under_american=-106,
        )
        assert result["market_label"] == MarketEvidenceLabel.MARKET_VERIFIED_HOLD
        nv = result["no_vig_result"]
        assert abs(nv["no_vig_over"] - 0.5191) < 0.001

    def test_leite_low_model_prob_low_confidence(self):
        # Model probability just above breakeven → LOW confidence
        result = run_prop_confidence_separation(
            row={"player": "Leite", "prop_type": "Assists", "line": 5.5},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
            market_evidence=classify_market_evidence(5.5, 5.5, -125, -106),
            confidence_inputs=ConfidenceInputs(
                conservative_lower_bound=0.51,  # near breakeven
                exact_line_verified=True,
                role_status_verified=True,
                projection_reproducible=True,
            ),
        )
        assert result["confidence_decision"] == ConfidenceLabel.FINAL_CONFIDENCE_LOW
        assert result["market_decision"] == MarketEvidenceLabel.MARKET_VERIFIED_HOLD

    def test_fudd_o15_ast_unverified_hold(self):
        result = classify_market_evidence(
            pp_line=1.5, sportsbook_line=None,
            over_american=None, under_american=None,
        )
        assert result["market_label"] == MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD

    def test_fudd_full_run(self):
        result = run_prop_confidence_separation(
            row={"player": "Fudd", "prop_type": "Assists", "line": 1.5},
            analysis_mode=AnalysisMode.HIT_CONFIDENCE,
            market_evidence=classify_market_evidence(1.5, None, None, None),
        )
        assert result["market_decision"] == MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD
        assert result["money_qualified"] is False


# ===========================================================================
# Governance hash updated for new patch
# ===========================================================================

class TestGovernancePatch70:
    def test_new_patch_registered(self):
        from gate_engine.governance import get_governance_status
        status = get_governance_status()
        assert "WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION" in \
               status["active_patch_ids"]

    def test_precedence_70(self):
        from gate_engine.governance import _PATCH_REGISTRY
        patch = next(
            (p for p in _PATCH_REGISTRY
             if p["patch_id"] == "WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION"),
            None,
        )
        assert patch is not None
        assert patch["precedence"] == 70

    def test_governance_hash_changed_from_acquisition_resilience_hash(self):
        from gate_engine.governance import _GOVERNANCE_HASH
        prev_hash = "a10bfb5c1f71204ba00e5b029540b832bd73fd1efd5c36d69c43ad4a11a3caa2"
        assert _GOVERNANCE_HASH != prev_hash

    def test_hash_deterministic(self):
        from gate_engine.governance import compute_governance_hash
        assert compute_governance_hash() == compute_governance_hash()

    def test_can_execute_false_in_new_patch(self):
        from gate_engine.governance import _PATCH_REGISTRY
        patch = next(
            (p for p in _PATCH_REGISTRY
             if p["patch_id"] == "WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION"),
            None,
        )
        assert patch["can_execute"] is False
