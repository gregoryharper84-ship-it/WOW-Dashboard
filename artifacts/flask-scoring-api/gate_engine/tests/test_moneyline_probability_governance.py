from gate_engine.moneyline.probability_claim_auditor import audit_probability_claim
from gate_engine.moneyline.event_decision_governor import decide_event

def _candidate(role, participant, p, lb):
    return {
        "event_key": "MLB:game-1",
        "participant": participant,
        "market_role": role,
        "calibrated_probability": p,
        "calibrated_probability_lower_bound": lb,
        "probability_audit_status": "PASS_PROBABILITY_AUDIT",
        "model_valid_after_latest_material_update": True,
    }

def test_probability_claim_passes_complete_record():
    result = audit_probability_claim(
        raw_probability=.62,
        independent_probability=.62,
        market_prior_probability=.60,
        market_prior_weight=.20,
        calibrated_probability=.616,
        lower_bound=.57,
        upper_bound=.66,
        model_status="ACTIVE",
        model_timestamp="2026-08-25T20:00:00+00:00",
        latest_material_update_timestamp="2026-08-25T19:00:00+00:00",
        source_snapshot_id="snapshot-1",
        outcome_probabilities=[.616, .384],
    )
    assert result.audit_result == "PASS_PROBABILITY_AUDIT"
    assert result.rank_eligible is True
    assert result.can_execute is False

def test_probability_claim_rejects_stale_model():
    result = audit_probability_claim(
        raw_probability=.62,
        independent_probability=.62,
        market_prior_probability=.60,
        market_prior_weight=.20,
        calibrated_probability=.616,
        lower_bound=.57,
        upper_bound=.66,
        model_status="ACTIVE",
        model_timestamp="2026-08-25T18:00:00+00:00",
        latest_material_update_timestamp="2026-08-25T19:00:00+00:00",
        source_snapshot_id="snapshot-1",
        outcome_probabilities=[.616, .384],
    )
    assert result.audit_result == "STALE_MODEL_INVALIDATED"
    assert result.rank_eligible is False

def test_probability_claim_caps_market_dependent_model():
    result = audit_probability_claim(
        raw_probability=.62,
        independent_probability=.62,
        market_prior_probability=.60,
        market_prior_weight=.60,
        calibrated_probability=.608,
        lower_bound=.56,
        upper_bound=.65,
        model_status="ACTIVE",
        source_snapshot_id="snapshot-1",
        outcome_probabilities=[.608, .392],
    )
    assert result.audit_result == "PASS_WITH_CONFIDENCE_CEILING"
    assert result.confidence_ceiling == "MODEL_QUALIFIED_HOLD"

def test_event_governor_selects_exactly_one_favorite():
    result = decide_event(
        _candidate("FAVORITE", "A", .64, .59),
        _candidate("UNDERDOG", "B", .36, .31),
    )
    assert result.event_decision == "PICK_FAVORITE"
    assert result.selected_participant == "A"
    assert result.selected_participant_count == 1

def test_event_governor_does_not_force_close_game():
    result = decide_event(
        _candidate("FAVORITE", "A", .52, .47),
        _candidate("UNDERDOG", "B", .48, .44),
    )
    assert result.event_decision == "NO_PICK_CLOSE_GAME"
    assert result.selected_participant_count == 0

def test_event_governor_requires_opposing_outcome():
    result = decide_event(_candidate("FAVORITE", "A", .70, .65), None)
    assert result.event_decision == "NO_PICK_UNCALIBRATED"
    assert result.selected_participant_count == 0
