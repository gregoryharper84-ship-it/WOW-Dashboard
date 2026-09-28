from __future__ import annotations

from v17.spread_certification_decision import (
    READY,
    REMAIN_SHADOW,
    build_spread_certification_decision_packet,
)


def _historical(**overrides):
    row = {
        "evidence_class": "NFLVERSE_HISTORICAL_CLOSE_PROXY",
        "exact_line_metrics": {
            "evaluation_mode": "NFLVERSE_HISTORICAL_CLOSE_PROXY",
            "evidence_row_n": 240,
            "exact_line_coverage": 0.92,
            "cover_brier": 0.235,
            "cover_log_loss": 0.662,
            "cover_ece": 0.045,
            "three_way_brier": 0.47,
            "market_features_used": False,
            "spread_line_used_as_feature": False,
            "market_probability_substitution_used": False,
            "moneyline_probability_used": False,
        },
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
    }
    row.update(overrides)
    return row


def _forward(**overrides):
    row = {
        "status": "EXPERIMENT_CREATED",
        "p_cover": 0.54,
        "p_push": 0.02,
        "p_not_cover": 0.44,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _review(**overrides):
    row = {
        "leakage_source_manifest_audit_passed": True,
        "counterexample_review_complete": True,
        "season_regime_review_complete": True,
        "calibration_reliability_review_complete": True,
        "tail_review_complete": True,
        "push_review_complete": True,
        "holdout_validation_complete": True,
        "regression_complete": True,
    }
    row.update(overrides)
    return row


def _policy(**overrides):
    row = {
        "policy_id": "NFL-SPREAD-CERT-POLICY-TEST",
        "status": "RATIFIED",
        "sport": "NFL",
        "min_exact_line_n": 200,
        "min_exact_line_coverage": 0.80,
        "max_cover_brier": 0.25,
        "max_cover_log_loss": 0.70,
        "max_cover_ece": 0.08,
        "max_three_way_brier": 0.55,
    }
    row.update(overrides)
    return row


def test_unratified_policy_remains_shadow():
    result = build_spread_certification_decision_packet(
        sport="NFL",
        historical_receipt=_historical(),
        forward_receipt=_forward(),
        review_evidence=_review(),
        policy={"policy_id": "DRAFT", "status": "DRAFT", "sport": "NFL"},
    )
    assert result["status"] == REMAIN_SHADOW
    assert "SPREAD_CERTIFICATION:POLICY_UNRATIFIED" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_structurally_complete_passing_packet_is_ready_for_independent_review_only():
    result = build_spread_certification_decision_packet(
        sport="NFL",
        historical_receipt=_historical(),
        forward_receipt=_forward(),
        review_evidence=_review(),
        policy=_policy(),
    )
    assert result["status"] == READY
    assert result["blockers"] == []
    assert result["publication_decision"] == "UNDECIDED_REQUIRES_INDEPENDENT_REVIEW"
    assert result["independent_review_required"] is True
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_failed_ratified_metric_gate_remains_shadow():
    historical = _historical()
    historical["exact_line_metrics"] = dict(historical["exact_line_metrics"], cover_brier=0.31)
    result = build_spread_certification_decision_packet(
        sport="NFL",
        historical_receipt=historical,
        forward_receipt=_forward(),
        review_evidence=_review(),
        policy=_policy(),
    )
    assert result["status"] == REMAIN_SHADOW
    assert "SPREAD_CERTIFICATION:POLICY_GATE_FAILED:cover_brier" in result["blockers"]


def test_invalid_forward_probability_triplet_fails_closed():
    result = build_spread_certification_decision_packet(
        sport="NFL",
        historical_receipt=_historical(),
        forward_receipt=_forward(p_cover=0.60, p_push=0.10, p_not_cover=0.40),
        review_evidence=_review(),
        policy=_policy(),
    )
    assert result["status"] == REMAIN_SHADOW
    assert "SPREAD_CERTIFICATION:FORWARD_PROBABILITY_TRIPLET_INVALID" in result["blockers"]


def test_market_or_moneyline_probability_substitution_invariant_violation_blocks():
    historical = _historical(market_probability_substitution_used=True)
    result = build_spread_certification_decision_packet(
        sport="NFL",
        historical_receipt=historical,
        forward_receipt=_forward(),
        review_evidence=_review(),
        policy=_policy(),
    )
    assert result["status"] == REMAIN_SHADOW
    assert "SPREAD_CERTIFICATION:INVARIANT_VIOLATION:market_probability_substitution_used" in result["blockers"]


def test_incomplete_review_gate_blocks_even_when_metrics_pass():
    result = build_spread_certification_decision_packet(
        sport="NFL",
        historical_receipt=_historical(),
        forward_receipt=_forward(),
        review_evidence=_review(tail_review_complete=False),
        policy=_policy(),
    )
    assert result["status"] == REMAIN_SHADOW
    assert "SPREAD_CERTIFICATION:REVIEW_GATE_INCOMPLETE:tail_review_complete" in result["blockers"]


def test_espn_historical_proxy_class_is_evidence_only_never_publishable():
    historical = _historical(evidence_class="ESPN_HISTORICAL_CLOSE_PROXY")
    historical["exact_line_metrics"] = dict(
        historical["exact_line_metrics"],
        evaluation_mode="ESPN_HISTORICAL_CLOSE_PROXY",
    )
    result = build_spread_certification_decision_packet(
        sport="WNBA",
        historical_receipt=historical,
        forward_receipt=_forward(),
        review_evidence=_review(),
        policy=_policy(policy_id="WNBA-SPREAD-CERT-POLICY-TEST", sport="WNBA"),
    )
    assert result["status"] == READY
    assert result["historical_evidence_class"] == "ESPN_HISTORICAL_CLOSE_PROXY"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
