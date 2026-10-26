"""Chronological and adversarial model-improvement evidence tests."""
import pytest

from v17.chronological_model_improvement_evidence import (
    evaluate_chronological_evidence,
)


def sample(i, role="CHAMPION", **overrides):
    result = {
        "prediction_id": f"{role}-pred-{i}",
        "candidate_id": f"game-{i}",
        "event_id": f"event-{i}",
        "market_identity": "NFL:ML:HOME",
        "sport": "NFL",
        "market_family": "MONEYLINE",
        "model_role": role,
        "model_version": "fitted-v1" if role == "CHAMPION" else "shadow-v2",
        "model_artifact_id": "fitted:v1" if role == "CHAMPION" else "shadow:v2",
        "source_snapshot_hash": "genuine-source-hash-reference",
        "settlement_receipt_ref": f"official-result-{i}",
        "model_fit_receipt_ref": f"immutable-fit-{role}",
        "forward_certification_receipt_ref": f"independent-forward-{role}",
        "model_fit_cutoff_at": "2026-10-25T13:00:00Z",
        "out_of_sample_certified": True,
        "calibration_certified": True,
        "lower_bound_certified": True,
        "source_snapshot_at": "2026-10-25T14:00:00Z",
        "issued_at": "2026-10-25T15:00:00Z",
        "event_start_at": "2026-10-25T17:00:00Z",
        "settled_at": "2026-10-25T20:00:00Z",
        "outcome_recorded_at": "2026-10-25T20:05:00Z",
        "calibrated_probability": .55 if role == "CHAMPION" else .75,
        "calibrated_lower_bound": .5,
        "settled_outcome": 1,
        "source_verified": True,
        "outcome_verified": True,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    result.update(overrides)
    return result


def test_champion_challenger_paired_forward_receipts():
    rows = [sample(i, role) for i in range(25)
            for role in ("CHAMPION", "CHALLENGER")]
    result = evaluate_chronological_evidence(rows)
    assert result["audit_complete"]
    assert result["rows_verified"] == 50
    assert result["paired_count"] == 25
    assert result["paired_comparison_ready"]
    assert len(result["cohorts"]) == 2
    assert all(cohort["evidence_sufficient"] for cohort in result["cohorts"])
    assert result["paired_metrics"]["brier_improvement"] > 0
    assert result["paired_metrics"]["log_loss_improvement"] > 0
    assert result["automatic_promotion_allowed"] is False
    assert result["model_release_authorized"] is False
    assert result["can_execute"] is False



def test_many_predictions_one_event_do_not_claim_independent_forward_sample():
    # A burst of 25 different market candidates in one game is not 25
    # independent game outcomes for model-selection confidence.
    rows = [sample(i, role, event_id="shared-forward-game")
            for i in range(25)
            for role in ("CHAMPION", "CHALLENGER")]
    result = evaluate_chronological_evidence(rows)
    assert result["audit_complete"]
    assert result["rows_verified"] == 50
    assert result["paired_count"] == 25
    assert result["paired_distinct_events"] == 1
    assert not result["paired_comparison_ready"]
    assert result["paired_metrics"] is None
    assert all(cohort["distinct_forward_events"] == 1
               and not cohort["evidence_sufficient"]
               and cohort["metrics"] is None
               for cohort in result["cohorts"])


def test_unpaired_models_never_claim_challenger_improvement():
    result = evaluate_chronological_evidence([sample(i) for i in range(25)])
    assert result["audit_complete"]
    assert result["paired_count"] == 0
    assert result["paired_metrics"] is None
    assert not result["paired_comparison_ready"]


@pytest.mark.parametrize("change,code", [
    ({"source_snapshot_at": "2026-10-25T16:00:00Z"}, "CHRONOLOGY"),
    ({"model_fit_cutoff_at": "2026-10-25T14:01:00Z"}, "CHRONOLOGY"),
    ({"model_fit_cutoff_at": "2026-10-25T14:00:00"}, "TIMESTAMP_UNVERIFIED"),
    ({"model_fit_receipt_ref": ""}, "IDENTITY"),
    ({"forward_certification_receipt_ref": ""}, "IDENTITY"),
    ({"out_of_sample_certified": False}, "FORWARD_CERTIFICATION"),
    ({"calibration_certified": False}, "FORWARD_CERTIFICATION"),
    ({"lower_bound_certified": False}, "FORWARD_CERTIFICATION"),
    ({"source_snapshot_at": "2026-10-25T19:00:00Z"}, "CHRONOLOGY"),
    ({"issued_at": "2026-10-25T17:00:00Z"}, "CHRONOLOGY"),
    ({"settled_at": "2026-10-25T16:00:00Z"}, "CHRONOLOGY"),
    ({"outcome_recorded_at": "2026-10-25T19:59:59Z"}, "CHRONOLOGY"),
    ({"issued_at": "2026-10-25T15:00:00"}, "TIMESTAMP_UNVERIFIED"),
    ({"outcome_verified": False}, "EVIDENCE_UNVERIFIED"),
    ({"source_verified": None}, "EVIDENCE_UNVERIFIED"),
    ({"calibrated_probability": float("nan")}, "CALIBRATION"),
    ({"calibrated_lower_bound": .9}, "CALIBRATION"),
    ({"calibrated_probability": True}, "CALIBRATION"),
    ({"settled_outcome": True}, "BINARY_SETTLEMENT"),
    ({"settled_outcome": 2}, "BINARY_SETTLEMENT"),
    ({"model_artifact_id": ""}, "IDENTITY"),
    ({"can_execute": True}, "GOVERNANCE"),
    ({"terminal_authority": "model-agent"}, "GOVERNANCE"),
])
def test_bad_evidence_is_held_and_never_published(change, code):
    result = evaluate_chronological_evidence([sample(1, **change)], min_samples=1)
    assert not result["audit_complete"]
    assert result["rows_verified"] == 0
    assert result["rows_held"] == 1
    assert code in result["findings"][0]["code"]
    assert result["paired_metrics"] is None


def test_small_cohort_is_unknown_not_winner():
    result = evaluate_chronological_evidence(
        [sample(1), sample(1, "CHALLENGER")],
        min_samples=25,
    )
    assert result["audit_complete"]
    assert result["paired_count"] == 1
    assert not result["paired_comparison_ready"]
    assert result["paired_metrics"] is None
    assert all(x["metrics"] is None for x in result["cohorts"])


def test_duplicate_prediction_cannot_inflate_sample_size():
    result = evaluate_chronological_evidence([sample(1), sample(1)], min_samples=1)
    assert not result["audit_complete"]
    assert result["rows_held"] == 1
    assert result["paired_metrics"] is None
    assert any(x["code"] == "DUPLICATE_MODEL_PREDICTION" for x in result["findings"])


def test_cross_model_settlement_conflict_vetoes_comparison():
    rows = [sample(i, role) for i in range(25)
            for role in ("CHAMPION", "CHALLENGER")]
    rows[-1]["settled_outcome"] = 0
    result = evaluate_chronological_evidence(rows)
    assert any(x["code"] == "PAIRED_SETTLEMENT_MISMATCH" for x in result["findings"])
    assert not result["paired_comparison_ready"]
    assert result["paired_metrics"] is None


def test_same_role_multiple_versions_cannot_be_paired_ambiguously():
    rows = [sample(1), sample(1, model_version="other", prediction_id="different")]
    result = evaluate_chronological_evidence(rows, min_samples=1)
    assert not result["audit_complete"]
    assert any(x["code"] == "AMBIGUOUS_PAIRED_MODEL_VERSION" for x in result["findings"])


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "25"])
def test_sample_minimum_must_be_fixed_positive_integer(value):
    with pytest.raises(ValueError, match="MIN_SAMPLES_INVALID"):
        evaluate_chronological_evidence([], min_samples=value)


def test_no_settled_rows_is_unknown_not_a_green_report():
    result = evaluate_chronological_evidence([])
    assert not result["audit_complete"]
    assert result["rows_received"] == 0
    assert result["cohorts"] == []
    assert result["paired_metrics"] is None


def test_same_event_different_source_cutoffs_is_not_a_valid_pair():
    rows = [sample(1), sample(
        1, "CHALLENGER", source_snapshot_at="2026-10-25T14:30:00Z",
    )]
    result = evaluate_chronological_evidence(rows, min_samples=1)
    assert result["rows_held"] == 0  # both real-looking rows; pair itself is false
    assert not result["paired_comparison_ready"]
    assert any(x["code"] == "PAIRED_SETTLEMENT_MISMATCH" for x in result["findings"])



def test_same_prediction_id_across_roles_cannot_count_twice():
    # Roles cannot share a supposedly immutable prediction receipt identifier.
    rows = [sample(1), sample(1, "CHALLENGER", prediction_id="CHAMPION-pred-1")]
    result = evaluate_chronological_evidence(rows, min_samples=1)
    assert result["rows_verified"] == 1
    assert result["rows_held"] == 1
    assert not result["audit_complete"]
    assert result["paired_count"] == 0
    assert any(x["code"] == "DUPLICATE_MODEL_PREDICTION" for x in result["findings"])


def test_same_as_of_timestamp_with_different_snapshot_hash_cannot_pair():
    # Timestamp equality is insufficient proof of identical point-in-time inputs.
    rows = [sample(1), sample(
        1, "CHALLENGER", source_snapshot_hash="different-pregame-inputs",
    )]
    result = evaluate_chronological_evidence(rows, min_samples=1)
    assert result["rows_verified"] == 2
    assert not result["paired_comparison_ready"]
    assert result["paired_metrics"] is None
    assert any(x["code"] == "PAIRED_SETTLEMENT_MISMATCH" for x in result["findings"])


def test_same_candidate_string_from_other_sport_cannot_cross_pair():
    rows = [sample(1), sample(
        1, "CHALLENGER", sport="NBA", market_family="PLAYER_POINTS",
    )]
    result = evaluate_chronological_evidence(rows, min_samples=1)
    assert result["paired_count"] == 0
    assert not result["paired_comparison_ready"]


def test_same_model_artifact_cannot_masquerade_as_independent_challenger():
    rows = [sample(1), sample(1, "CHALLENGER",
                              model_artifact_id="fitted:v1")]
    result = evaluate_chronological_evidence(rows, min_samples=1)
    assert not result["audit_complete"]
    assert result["paired_count"] == 0
    assert not result["paired_comparison_ready"]
    assert any(x["code"] == "PAIRED_MODEL_IDENTITY_COLLISION"
               for x in result["findings"])


def test_same_model_version_cannot_be_counted_as_new_challenger():
    rows = [sample(1), sample(1, "CHALLENGER",
                              model_version="fitted-v1")]
    result = evaluate_chronological_evidence(rows, min_samples=1)
    assert not result["audit_complete"]
    assert result["paired_metrics"] is None
    assert any(x["code"] == "PAIRED_MODEL_IDENTITY_COLLISION"
               for x in result["findings"])


@pytest.mark.parametrize("field,replacement", [
    ("model_artifact_id", "fitted:v1-other"),
    ("model_fit_receipt_ref", "immutable-fit-other"),
    ("forward_certification_receipt_ref", "independent-forward-other"),
])
def test_mixed_champion_artifact_or_certification_cannot_pool_forward_cohort(field, replacement):
    # Same version label, 25 distinct events and authentic-looking matched rows
    # must not manufacture a single certified champion identity.
    rows = [sample(i, role) for i in range(25)
            for role in ("CHAMPION", "CHALLENGER")]
    for i in range(1, 25, 2):
        rows[2 * i][field] = replacement
    result = evaluate_chronological_evidence(rows)
    assert result["rows_verified"] == 50
    assert result["paired_count"] == 25
    assert result["paired_distinct_events"] == 25
    assert not result["audit_complete"]
    assert not result["paired_comparison_ready"]
    assert result["paired_metrics"] is None
    assert any(finding["code"] == "MODEL_COHORT_ARTIFACT_DRIFT"
               and finding["model_role"] == "CHAMPION"
               for finding in result["findings"])
    assert all(cohort["metrics"] is None for cohort in result["cohorts"])
    assert result["automatic_promotion_allowed"] is False
    assert result["model_release_authorized"] is False
    assert result["can_execute"] is False



def test_one_game_with_many_markets_cannot_dominate_forward_calibration():
    # Twenty-five settled games are 25 independent game outcomes, not 125.
    # All additional candidates belong to only the first official event.
    rows = [
        sample(i, role) for i in range(25)
        for role in ("CHAMPION", "CHALLENGER")
    ]
    for candidate_no in range(100):
        for role in ("CHAMPION", "CHALLENGER"):
            rows.append(sample(
                1000 + candidate_no, role,
                event_id="event-0",
                candidate_id=f"player-{candidate_no}",
                market_identity=f"NFL:PLAYER_MARKET:{candidate_no}",
                settlement_receipt_ref=f"official-event0-market-{candidate_no}",
                calibrated_probability=.10 if role == "CHAMPION" else .11,
                calibrated_lower_bound=.05,
            ))
    result = evaluate_chronological_evidence(rows)
    assert result["audit_complete"]
    assert result["metric_weighting"] == "EQUAL_OFFICIAL_EVENT"
    assert result["paired_count"] == 125
    assert result["paired_distinct_events"] == 25
    assert result["paired_comparison_ready"]
    # Event balancing means the one 101-row game carries 1/25 of the mass.
    event_zero_brier = (.45 ** 2 + 100 * .90 ** 2) / 101
    expected = (event_zero_brier + 24 * .45 ** 2) / 25
    assert result["paired_metrics"]["champion"]["brier"] == pytest.approx(
        expected, abs=1e-8,
    )
    assert .22 < result["paired_metrics"]["champion"]["brier"] < .24
    # Calibration error must also use event mass, not raw candidate volume.
    assert result["paired_metrics"]["champion"]["ece_10_bin"] < .5
    assert all(c["distinct_forward_events"] == 25 for c in result["cohorts"])
    assert result["automatic_promotion_allowed"] is False
    assert result["model_release_authorized"] is False
