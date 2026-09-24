from __future__ import annotations

from types import SimpleNamespace

import pytest

from basketball_team_event_specialist import (
    FEATURE_NAMES as BASKETBALL_FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION as BASKETBALL_SCHEMA,
    MODEL_FAMILY as BASKETBALL_MODEL_FAMILY,
)
from ncaaf_trainer import FEATURES as NCAAF_FEATURES, MODEL_FAMILY as NCAAF_MODEL_FAMILY
from nfl_event_features_p2 import FEATURE_ORDER as NFL_FEATURE_ORDER, FEATURE_SCHEMA_VERSION as NFL_SCHEMA
from nfl_event_model_v17 import MODEL_FAMILY as NFL_MODEL_FAMILY
from v17.mlb_event_feature_contract import (
    FEATURE_ORDER as MLB_FEATURE_ORDER,
    FEATURE_ORDER_SHA256 as MLB_FEATURE_ORDER_SHA256,
    FEATURE_SCHEMA_VERSION as MLB_SCHEMA,
    MODEL_ARTIFACT_VERSION as MLB_MODEL_ARTIFACT_VERSION,
    MODEL_FAMILY as MLB_MODEL_FAMILY,
    feature_order_sha256 as mlb_feature_order_sha256,
    observed_feature_order_matches as mlb_observed_feature_order_matches,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS, TEAM_EVENT_INPUT_CONTRACTS
from v17.team_event_feature_consumption import build_feature_consumption_receipt
from v17.team_event_feature_contract_registry import (
    DEVELOPMENT_ONLY,
    RUNTIME_CONTRACT,
    UNDECLARED,
    SportFeatureContract,
    feature_contract_for_sport,
    feature_contract_registry,
    feature_contract_registry_health,
)


def _feature_ids(row):
    return tuple(feature["feature_id"] for feature in row["features"])


def test_registry_accounts_for_every_cataloged_sport_without_implying_consumption():
    rows = feature_contract_registry()

    assert len(rows) == len(EXPECTED_TEAM_EVENT_SPORTS)
    assert {row["sport"] for row in rows} == set(EXPECTED_TEAM_EVENT_SPORTS)
    for row in rows:
        assert row["consumption_proof_required"] is True
        assert row["consumption_currently_verified"] is False
        assert row["market_probability_feature_allowed"] is False
        assert row["generic_probability_substitution_allowed"] is False
        assert row["terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert row["can_execute"] is False


def test_mlb_runtime_contract_matches_certified_persisted_fitted_vector_without_claiming_live_consumption():
    row = feature_contract_for_sport("MLB").as_dict()

    assert row["schema_state"] == RUNTIME_CONTRACT
    assert row["schema_version"] == MLB_SCHEMA == "MLB_V2D_CONTEXT_V1"
    assert row["model_family"] == MLB_MODEL_FAMILY == "V2D_SHARED_NB_PLUS_CONTEXT_REGIMES"
    assert MLB_MODEL_ARTIFACT_VERSION == "MLB_V16_V2D_CONTEXT_SHARED_SIM_R1"
    assert len(MLB_FEATURE_ORDER) == 38
    assert _feature_ids(row) == tuple(MLB_FEATURE_ORDER)
    assert mlb_feature_order_sha256() == MLB_FEATURE_ORDER_SHA256 == "eda4c6a20efade791dfaf7df4759a344c22ab9872765c4ca3c8cb27d82fd0a52"
    assert mlb_observed_feature_order_matches(MLB_FEATURE_ORDER) is True
    assert MLB_MODEL_ARTIFACT_VERSION in str(row["model_consumption_source"])
    assert all(feature["role"] == "MODEL_PRIMARY" for feature in row["features"])
    assert all(feature["required"] is True for feature in row["features"])
    assert all(feature["critical"] is True for feature in row["features"])
    assert row["consumption_currently_verified"] is False
    assert row["can_execute"] is False

    forbidden_probability_inputs = {
        "sportsbook_implied_probability",
        "no_vig_probability",
        "market_probability",
        "market_prior",
        "moneyline_odds",
    }
    assert forbidden_probability_inputs.isdisjoint(_feature_ids(row))


def test_mlb_runtime_contract_expands_receipt_expectations_but_does_not_fabricate_consumption():
    req = SimpleNamespace(
        official_event_id="mlb-event",
        home_team="HOME",
        away_team="AWAY",
        home_starter="HOME-SP",
        away_starter="AWAY-SP",
        home_lineup="HOME-LINEUP",
        away_lineup="AWAY-LINEUP",
        injury_report="CANONICAL",
        bullpen_status="CANONICAL",
        weather_or_roof="CANONICAL",
        settlement_basis="FULL_GAME_INCLUDING_EXTRAS",
        sport_specific_evidence={},
    )
    receipt = build_feature_consumption_receipt(
        req=req,
        sport="MLB",
        controlling_specialist="MLB_GAME_WIN_PROBABILITY_EXPERT",
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["MLB"],
        result={
            "prediction_id": "mlb-prediction-1",
            "feature_schema_version": MLB_SCHEMA,
            "model_artifact_version": MLB_MODEL_ARTIFACT_VERSION,
            "can_execute": False,
        },
    )

    assert receipt["model_features_expected"] == len(MLB_FEATURE_ORDER) == 38
    assert receipt["consumption_verification_status"] == "UNVERIFIED"
    assert receipt["features_consumed"] == 0
    assert receipt["receipt_complete"] is False
    assert receipt["unexpected_consumed_feature_ids"] == []
    assert receipt["can_execute"] is False

    details = {row["feature_id"]: row for row in receipt["feature_details"]}
    assert details["opp_starter_era"]["role"] == "MODEL_PRIMARY"
    assert details["opp_starter_era"]["expected_source"] == "MODEL_FEATURE_CONTRACT"
    assert details["opp_starter_era"]["consumption_status"] == "NOT_VERIFIED"


def test_nfl_runtime_contract_is_exactly_the_certified_fitted_vector():
    row = feature_contract_for_sport("NFL").as_dict()

    assert row["schema_state"] == RUNTIME_CONTRACT
    assert row["schema_version"] == NFL_SCHEMA == "NFL_EVENT_PREGAME_PRIOR_V1"
    assert row["model_family"] == NFL_MODEL_FAMILY == "NFL_OUTRIGHT_WIN_LOGREG_V1"
    assert _feature_ids(row) == tuple(NFL_FEATURE_ORDER)
    assert all(feature["role"] == "MODEL_PRIMARY" for feature in row["features"])
    assert all(feature["required"] is True for feature in row["features"])
    assert all(feature["critical"] is True for feature in row["features"])


def test_basketball_contract_matches_fitted_development_artifact_but_does_not_promote_it():
    for sport in ("NBA", "WNBA"):
        row = feature_contract_for_sport(sport).as_dict()

        assert row["schema_state"] == DEVELOPMENT_ONLY
        assert row["schema_version"] == BASKETBALL_SCHEMA == "BASKETBALL_TEAM_EVENT_FEATURES_V2"
        assert row["model_family"] == BASKETBALL_MODEL_FAMILY == "BASKETBALL_TEAM_EVENT_LOGISTIC_V1"
        assert _feature_ids(row) == tuple(BASKETBALL_FEATURE_NAMES)
        assert all(feature["role"] == "MODEL_PRIMARY" for feature in row["features"])
        assert row["consumption_currently_verified"] is False


def test_ncaaf_contract_matches_candidate_fitted_vector_and_stays_development_only():
    row = feature_contract_for_sport("NCAAF").as_dict()

    assert row["schema_state"] == DEVELOPMENT_ONLY
    assert row["schema_version"] == "NCAAF_FEATURES_V1"
    assert row["model_family"] == NCAAF_MODEL_FAMILY == "NCAAF_LOGISTIC_V1"
    assert _feature_ids(row) == tuple(NCAAF_FEATURES)
    assert all(feature["role"] == "MODEL_PRIMARY" for feature in row["features"])
    assert row["consumption_currently_verified"] is False


def test_schema_or_certification_existence_does_not_fabricate_a_feature_contract():
    # Several sports have implementations or activatable registrations, but no
    # exact audited numerical feature schema has been declared in this registry
    # slice. They must stay UNDECLARED.
    for sport in ("NCAAB", "NHL", "SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET"):
        row = feature_contract_for_sport(sport).as_dict()

        assert row["schema_state"] == UNDECLARED
        assert row["schema_version"] is None
        assert row["model_family"] is None
        assert row["features"] == []
        assert row["feature_count"] == 0
        assert row["consumption_currently_verified"] is False


def test_contract_object_rejects_authority_escalation_flags():
    common = dict(
        sport="NFL",
        schema_state=UNDECLARED,
        schema_version=None,
        model_family=None,
        schema_source=None,
        model_consumption_source=None,
    )

    with pytest.raises(ValueError, match="live consumption verification"):
        SportFeatureContract(**common, consumption_currently_verified=True)

    with pytest.raises(ValueError, match="market sporting-probability"):
        SportFeatureContract(**common, market_probability_feature_allowed=True)

    with pytest.raises(ValueError, match="generic probability substitution"):
        SportFeatureContract(**common, generic_probability_substitution_allowed=True)

    with pytest.raises(ValueError, match="can_execute"):
        SportFeatureContract(**common, can_execute=True)


def test_registry_health_is_observability_only_and_reports_unverified_live_consumption():
    health = feature_contract_registry_health()

    assert health["cataloged_sport_count"] == len(EXPECTED_TEAM_EVENT_SPORTS)
    assert health["runtime_contract_count"] == 2
    assert health["development_only_contract_count"] == 3
    assert health["declared_contract_count"] == 5
    assert health["undeclared_contract_count"] == len(EXPECTED_TEAM_EVENT_SPORTS) - 5
    assert health["all_live_consumption_unverified"] is True
    assert health["market_probability_feature_allowed"] is False
    assert health["generic_probability_substitution_allowed"] is False
    assert health["probability_behavior_changed"] is False
    assert health["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert health["can_execute"] is False
