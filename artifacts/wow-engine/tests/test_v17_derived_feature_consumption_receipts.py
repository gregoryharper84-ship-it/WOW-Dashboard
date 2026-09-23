from __future__ import annotations

from types import SimpleNamespace

from nfl_event_features_p2 import FEATURE_ORDER as NFL_FEATURE_ORDER, FEATURE_SCHEMA_VERSION as NFL_SCHEMA
from v17.nfl_team_event_publication import _attach_nfl_feature_consumption_metadata
from v17.team_event_capability_manifest import TEAM_EVENT_INPUT_CONTRACTS
from v17.team_event_feature_consumption import build_feature_consumption_receipt


def _nfl_request():
    return SimpleNamespace(
        official_event_id="2026_03_DAL_PHI",
        home_team="PHI",
        away_team="DAL",
        quarterback_status="CANONICAL_CONTRACT_FIELD",
        injury_report="CANONICAL_CONTRACT_FIELD",
        weather_or_roof="CANONICAL_CONTRACT_FIELD",
        rest_travel="CANONICAL_CONTRACT_FIELD",
        settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        sport_specific_evidence={},
    )


def _base_nfl_result(**extra):
    result = {
        "prediction_id": "nfl-prediction-1",
        "official_event_id": "2026_03_DAL_PHI",
        "feature_schema_version": NFL_SCHEMA,
        "feature_row_hash": "feature-row-sha256",
        "model_artifact_version": "nfl-champion-test",
        "immutable_model_timestamp": "2026-09-23T21:00:00+00:00",
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.55,
        "calibrated_upper_bound": 0.67,
        "can_execute": False,
    }
    result.update(extra)
    return result


def _nfl_receipt(result):
    return build_feature_consumption_receipt(
        req=_nfl_request(),
        sport="NFL",
        controlling_specialist="wow.nfl-game-win-probability-expert",
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["NFL"],
        result=result,
    )


def test_nfl_publication_metadata_reports_exact_certified_vector_without_probability_change():
    original = _base_nfl_result()
    augmented = _attach_nfl_feature_consumption_metadata(original)

    assert augmented["consumed_feature_ids"] == list(NFL_FEATURE_ORDER)
    assert set(augmented["feature_observations"]) == set(NFL_FEATURE_ORDER)
    assert all(
        observation == {
            "value_status": "AVAILABLE",
            "freshness_status": "UNKNOWN",
            "source": "NFL_CANONICAL_PREGAME_FEATURE_ROW",
            "provenance_id": "feature-row-sha256",
        }
        for observation in augmented["feature_observations"].values()
    )
    assert augmented["calibrated_probability"] == original["calibrated_probability"]
    assert augmented["calibrated_lower_bound"] == original["calibrated_lower_bound"]
    assert augmented["calibrated_upper_bound"] == original["calibrated_upper_bound"]
    assert augmented["can_execute"] is False


def test_nfl_publication_metadata_fails_closed_when_schema_or_provenance_is_not_proven():
    wrong_schema = _attach_nfl_feature_consumption_metadata(
        _base_nfl_result(feature_schema_version="OTHER_SCHEMA")
    )
    missing_hash = _attach_nfl_feature_consumption_metadata(
        _base_nfl_result(feature_row_hash="")
    )

    assert "consumed_feature_ids" not in wrong_schema
    assert "feature_observations" not in wrong_schema
    assert "consumed_feature_ids" not in missing_hash
    assert "feature_observations" not in missing_hash


def test_nfl_receipt_expected_universe_includes_internal_model_features_and_request_contract():
    result = _attach_nfl_feature_consumption_metadata(_base_nfl_result())
    receipt = _nfl_receipt(result)

    expected_count = len(set(TEAM_EVENT_INPUT_CONTRACTS["NFL"]) | set(NFL_FEATURE_ORDER))
    assert receipt["features_expected"] == expected_count
    assert receipt["request_contract_features_expected"] == len(TEAM_EVENT_INPUT_CONTRACTS["NFL"])
    assert receipt["model_features_expected"] == len(NFL_FEATURE_ORDER)
    assert receipt["features_consumed"] == len(NFL_FEATURE_ORDER)
    assert receipt["unexpected_consumed_feature_ids"] == []
    assert receipt["consumed_but_unavailable_feature_ids"] == []
    assert receipt["consumption_verification_status"] == "VERIFIED"
    assert receipt["role_contract_complete"] is True
    assert receipt["provenance_complete"] is True
    assert receipt["critical_consumption_complete"] is True
    assert receipt["receipt_complete"] is True

    details = {row["feature_id"]: row for row in receipt["feature_details"]}
    assert details["off_epa_edge"]["role"] == "MODEL_PRIMARY"
    assert details["off_epa_edge"]["expected_source"] == "MODEL_FEATURE_CONTRACT"
    assert details["off_epa_edge"]["value_status"] == "AVAILABLE"
    assert details["off_epa_edge"]["consumption_status"] == "CONSUMED"
    assert details["off_epa_edge"]["provenance_id"] == "feature-row-sha256"
    assert details["quarterback_status"]["role"] == "CONTRACT_ONLY"
    assert details["quarterback_status"]["consumption_status"] == "NOT_VERIFIED"
    assert receipt["can_execute"] is False


def test_consumed_internal_feature_without_observation_is_visible_and_incomplete():
    result = _base_nfl_result(consumed_feature_ids=list(NFL_FEATURE_ORDER))
    receipt = _nfl_receipt(result)

    assert receipt["consumption_verification_status"] == "VERIFIED"
    assert set(receipt["consumed_but_unavailable_feature_ids"]) == set(NFL_FEATURE_ORDER)
    assert receipt["provenance_complete"] is False
    assert receipt["receipt_complete"] is False


def test_development_only_feature_contract_does_not_self_promote_live_receipt_expectations():
    req = SimpleNamespace(
        official_event_id="wnba-event",
        home_team="A",
        away_team="B",
        injury_report="ok",
        expected_starters_rotation="ok",
        rest_back_to_back="ok",
        settlement_basis="full_game",
        sport_specific_evidence={},
    )
    result = {
        "prediction_id": "wnba-test",
        "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2",
        "consumed_feature_ids": ["home_win_rate_prior"],
        "can_execute": False,
    }
    receipt = build_feature_consumption_receipt(
        req=req,
        sport="WNBA",
        controlling_specialist="WNBA_TEST",
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["WNBA"],
        result=result,
    )

    assert receipt["model_features_expected"] == 0
    assert receipt["unexpected_consumed_feature_ids"] == ["home_win_rate_prior"]
    assert receipt["receipt_complete"] is False
