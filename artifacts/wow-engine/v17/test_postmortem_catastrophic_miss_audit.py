from __future__ import annotations

from v17.postmortem_catastrophic_miss_audit import audit_high_confidence_catastrophic_miss


def _prediction(**overrides):
    base = {
        "prediction_id": "pred-1",
        "sport": "MLB",
        "stat_type": "PITCHER_STRIKEOUTS",
        "direction": "MORE",
        "line": 2.5,
        "calibrated_probability": 0.69,
        "calibrated_probability_lower_bound": 0.62,
        "confidence_tier": "HIGH",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "failure_cause_tags": [],
    }
    base.update(overrides)
    return base


def _outcome(value=0):
    return {"official_stat_or_settlement_value": value, "official_result": "LOSS"}


def test_newcomb_like_zero_k_miss_promotes_role_mismatch_to_l3_without_hindsight():
    evidence = {
        "game_log": [4, 5, 3, 6, 4, 5, 3, 4, 6, 5],
        "role_status": {
            "recent_role_profile": {
                "status": "STARTER_MODEL_INCOMPATIBLE",
                "start_share": 1 / 6,
                "short_appearance_share": 1.0,
            }
        },
    }
    audit = audit_high_confidence_catastrophic_miss(
        prediction=_prediction(line=1.5),
        evidence=evidence,
        outcome=_outcome(0),
    )
    assert audit.triggered is True
    assert audit.primary_miss_class == "ROLE_OR_WORKLOAD_ERROR"
    assert audit.predictability_class == "PREDICTABLE_BUT_OMITTED"
    assert audit.learning_level == "L3_PATCH_CANDIDATE"
    assert audit.patch_candidate is True
    assert "CURRENT_USAGE_INCOMPATIBLE_WITH_STARTER_MODEL" in audit.contributing_factors


def test_mize_like_zero_k_miss_detects_pregame_low_tail_contradiction_automatically():
    evidence = {
        "game_log": [1, 6, 3, 0, 1, 4, 7],
        "role_status": {
            "recent_role_profile": {
                "status": "STARTER_MODEL_COMPATIBLE",
                "start_share": 1.0,
                "short_appearance_share": 0.0,
            }
        },
    }
    audit = audit_high_confidence_catastrophic_miss(
        prediction=_prediction(line=2.5),
        evidence=evidence,
        outcome=_outcome(0),
    )
    assert audit.triggered is True
    assert audit.primary_miss_class == "TAIL_RISK_UNDERMODELED"
    assert audit.predictability_class == "PREDICTABLE_BUT_UNDERWEIGHTED"
    assert audit.learning_level == "L3_PATCH_CANDIDATE"
    assert "LOW_K_TAIL_CONTRADICTION_NOT_CONSUMED" in audit.contributing_factors
    assert audit.diagnostics["low_k_tail_audit"]["applies"] is True


def test_zero_k_result_does_not_trigger_catastrophic_audit_when_forecast_was_not_high_confidence():
    audit = audit_high_confidence_catastrophic_miss(
        prediction=_prediction(
            calibrated_probability=0.56,
            calibrated_probability_lower_bound=0.50,
            confidence_tier="RESEARCH",
            terminal_label="RESEARCH_INTEREST",
        ),
        evidence={"game_log": [1, 6, 3, 0, 1, 4, 7]},
        outcome=_outcome(0),
    )
    assert audit.triggered is False
    assert audit.patch_candidate is False


def test_market_contradiction_is_diagnostic_only_not_model_probability_replacement():
    evidence = {
        "game_log": [4, 5, 4, 5, 3, 4, 5, 6, 4, 5],
        "role_status": {
            "recent_role_profile": {"status": "STARTER_MODEL_COMPATIBLE"}
        },
    }
    prediction = _prediction(
        market_prior_probability=0.38,
        calibrated_probability=0.70,
        calibrated_probability_lower_bound=0.63,
    )
    audit = audit_high_confidence_catastrophic_miss(
        prediction=prediction,
        evidence=evidence,
        outcome=_outcome(0),
    )
    assert audit.triggered is True
    assert "EXACT_MARKET_CONTRADICTION_PRESENT" in audit.contributing_factors
    assert audit.diagnostics["calibrated_probability"] == 0.70
    assert "DO_NOT_REPLACE_MODEL_PROBABILITY_WITH_MARKET_PROBABILITY" in audit.preserve_constraints
