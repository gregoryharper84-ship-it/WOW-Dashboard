from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17.team_event_feature_consumption import (
    FeatureContractEntry,
    build_feature_consumption_receipt,
)


def _request(*, include_away: bool = True, stale_home: bool = False):
    evidence = {
        "home_win_pct": 0.63,
        "calibration_artifact": {"artifact_id": "cal-1"},
        "feature_metadata": {
            "home_win_pct": {
                "source": "internal_team_strength_snapshot",
                "source_timestamp": "2026-09-23T18:00:00+00:00",
                "observed_at": "2026-09-23T18:01:00+00:00",
                "provenance_id": "prov-home",
                "freshness_status": "STALE" if stale_home else "FRESH",
            },
            "away_win_pct": {
                "source": "internal_team_strength_snapshot",
                "source_timestamp": "2026-09-23T18:00:00+00:00",
                "observed_at": "2026-09-23T18:01:00+00:00",
                "provenance_id": "prov-away",
                "freshness_status": "FRESH",
            },
            "calibration_artifact": {
                "source": "governed_calibration_registry",
                "source_timestamp": "2026-09-22T00:00:00+00:00",
                "provenance_id": "prov-cal",
                "freshness_status": "FRESH",
            },
        },
    }
    if include_away:
        evidence["away_win_pct"] = 0.52
    return SimpleNamespace(
        official_event_id="event-123",
        sport_specific_evidence=evidence,
    )


def _result(**extra):
    payload = {
        "candidate_id": "candidate-123",
        "prediction_id": "prediction-123",
        "official_event_id": "event-123",
        "model_artifact_version": "wnba-test-v1",
        "immutable_model_timestamp": "2026-09-23T18:02:00+00:00",
        "calibrated_probability": 0.64,
        "calibrated_lower_bound": 0.57,
        "calibrated_upper_bound": 0.71,
        "can_execute": False,
    }
    payload.update(extra)
    return payload


def _receipt(req, result, **kwargs):
    return build_feature_consumption_receipt(
        req=req,
        sport="WNBA",
        controlling_specialist="WNBA_TEST_SPECIALIST",
        required_inputs=(
            "official_event_id",
            "home_win_pct",
            "away_win_pct",
            "calibration_artifact",
        ),
        result=result,
        feature_schema_version=kwargs.pop("feature_schema_version", "WNBA_TEST_FEATURES_V1"),
        declared_feature_roles=kwargs.pop(
            "declared_feature_roles",
            {
                "home_win_pct": "MODEL_PRIMARY",
                "away_win_pct": "MODEL_PRIMARY",
            },
        ),
        critical_features=kwargs.pop(
            "critical_features", ("home_win_pct", "away_win_pct")
        ),
        **kwargs,
    )


def test_feature_contract_entry_rejects_unknown_role():
    with pytest.raises(ValueError, match="unsupported feature role"):
        FeatureContractEntry("home_win_pct", role="MAGIC_FEATURE")


def test_receipt_distinguishes_available_from_explicitly_consumed():
    receipt = _receipt(
        _request(),
        _result(consumed_feature_ids=["home_win_pct", "away_win_pct"]),
    )

    assert receipt["features_expected"] == 4
    assert receipt["features_available"] == 4
    assert receipt["features_consumed"] == 2
    assert receipt["features_missing"] == 0
    assert receipt["consumption_verification_status"] == "VERIFIED"
    assert receipt["role_contract_complete"] is True
    assert receipt["receipt_complete"] is True
    assert receipt["critical_features"] == {
        "home_win_pct": "CONSUMED",
        "away_win_pct": "CONSUMED",
    }
    details = {row["feature_id"]: row for row in receipt["feature_details"]}
    assert details["official_event_id"]["role"] == "CONTRACT_ONLY"
    assert details["calibration_artifact"]["role"] == "CALIBRATION_CONTEXT"
    assert details["home_win_pct"]["role"] == "MODEL_PRIMARY"
    assert details["home_win_pct"]["provenance_id"] == "prov-home"
    assert receipt["can_execute"] is False


def test_absent_consumption_declaration_is_unverified_not_inferred():
    receipt = _receipt(_request(), _result())

    assert receipt["features_available"] == 4
    assert receipt["features_consumed"] == 0
    assert receipt["consumption_verification_status"] == "UNVERIFIED"
    assert receipt["receipt_complete"] is False
    assert all(
        detail["consumption_status"] == "NOT_VERIFIED"
        for detail in receipt["feature_details"]
    )


def test_missing_and_stale_inputs_are_reported_without_imputation():
    receipt = _receipt(
        _request(include_away=False, stale_home=True),
        _result(consumed_feature_ids=["home_win_pct"]),
    )

    assert receipt["features_missing"] == 1
    assert receipt["features_stale"] == 1
    details = {row["feature_id"]: row for row in receipt["feature_details"]}
    assert details["away_win_pct"]["value_status"] == "MISSING"
    assert details["home_win_pct"]["value_status"] == "STALE"
    assert details["away_win_pct"]["consumption_status"] == "NOT_VERIFIED"


def test_rejected_and_unexpected_consumed_feature_ids_are_auditable():
    receipt = _receipt(
        _request(),
        _result(
            consumed_feature_ids=["home_win_pct", "not_in_contract"],
            rejected_feature_ids=["away_win_pct", "also_not_in_contract"],
        ),
    )

    assert receipt["features_consumed"] == 1
    assert receipt["features_rejected"] == 1
    assert receipt["unexpected_consumed_feature_ids"] == ["not_in_contract"]
    assert receipt["unexpected_rejected_feature_ids"] == ["also_not_in_contract"]


def test_receipt_id_is_deterministic_for_same_score_evidence():
    req = _request()
    result = _result(consumed_feature_ids=["home_win_pct", "away_win_pct"])
    first = _receipt(req, result)
    second = _receipt(req, result)

    assert first["feature_consumption_receipt_id"] == second["feature_consumption_receipt_id"]


def test_undeclared_feature_role_keeps_receipt_incomplete():
    receipt = _receipt(
        _request(),
        _result(consumed_feature_ids=["home_win_pct", "away_win_pct"]),
        declared_feature_roles={"home_win_pct": "MODEL_PRIMARY"},
    )

    assert receipt["role_contract_complete"] is False
    assert receipt["receipt_complete"] is False
    details = {row["feature_id"]: row for row in receipt["feature_details"]}
    assert details["away_win_pct"]["role"] == "UNDECLARED"
