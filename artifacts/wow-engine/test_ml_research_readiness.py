from __future__ import annotations

from fastapi.testclient import TestClient

import api
from ml_research_readiness import (
    SUPPORTED_ML_READINESS_SPORTS,
    get_ml_research_readiness,
    validate_registry_invariants,
)


client = TestClient(api.app)


FORBIDDEN_MODEL_OUTPUT_KEYS = {
    "model_probability",
    "raw_probability",
    "unconditional_probability",
    "calibrated_probability",
    "calibrated_probability_lower_bound",
    "calibrated_probability_upper_bound",
    "terminal_label",
    "final_terminal_ceiling",
}


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_registry_invariants_validate():
    validate_registry_invariants()


def test_registry_contains_exactly_six_supported_ml_sports():
    payload = get_ml_research_readiness()
    sports = [row["sport"] for row in payload["sports"]]
    assert tuple(sports) == SUPPORTED_ML_READINESS_SPORTS
    assert tuple(sports) == ("MLB", "NFL", "NBA", "WNBA", "TENNIS", "NCAAF")


def test_mlb_baseline_reflects_prospectively_certified_v16_event_model():
    mlb = get_ml_research_readiness("MLB")

    assert mlb["research_readiness"] == "STRONG"
    assert mlb["model_capability"] == "AVAILABLE"
    assert mlb["components"]["fitted_event_model"] == "READY"
    assert mlb["components"]["event_calibrator"] == "READY"
    assert mlb["components"]["calibrated_lower_bound"] == "READY"
    assert mlb["capability_evidence"]["provider_identity"] == "WOW_MLB_EVENT_FITTED_MODEL_V1"
    assert mlb["capability_evidence"]["model_artifact_version"] == "MLB_V16_V2D_CONTEXT_SHARED_SIM_R1"
    assert mlb["capability_evidence"]["lifecycle_state"] == "PROSPECTIVE_CERTIFIED"
    assert mlb["capability_evidence"]["calibration_health_requirement"] == "PASS"
    assert mlb["capability_evidence"]["minimum_simulations"] == 50000
    assert mlb["blockers"] == []
    assert mlb["main_research_gap"] == "NONE_AT_MODEL_RESEARCH_LAYER"


def test_nfl_and_nba_are_explicit_and_fail_closed_on_missing_models():
    nfl = get_ml_research_readiness("nfl")
    nba = get_ml_research_readiness("NBA")

    assert nfl["priority"] == "P0"
    assert nfl["research_readiness"] == "INCOMPLETE"
    assert nfl["model_capability"] == "MODEL_UNAVAILABLE"
    assert "NFL_FITTED_EVENT_MODEL_UNAVAILABLE" in nfl["blockers"]
    assert "QB-adjusted EPA/play" in nfl["research_requirements"]

    assert nba["priority"] == "P1"
    assert nba["research_readiness"] == "INCOMPLETE"
    assert nba["model_capability"] == "MODEL_UNAVAILABLE"
    assert "NBA_FITTED_EVENT_MODEL_UNAVAILABLE" in nba["blockers"]
    assert "adjusted net rating" in nba["research_requirements"]


def test_readiness_is_baseline_non_terminal_non_executable_and_non_publishable():
    payload = get_ml_research_readiness()
    assert payload["runtime"] == "WOW_v16_CLEAN_CORE"
    assert payload["status_basis"] == "CODEBASE_CAPABILITY_BASELINE"
    assert payload["requires_event_specific_refresh"] is True
    assert payload["readiness_is_terminal_gate"] is False
    assert payload["probability_publishable_from_readiness"] is False
    assert payload["terminal_ceiling_effect"] == "NONE"
    assert payload["can_execute"] is False

    for row in payload["sports"]:
        assert row["status_basis"] == "CODEBASE_CAPABILITY_BASELINE"
        assert row["requires_event_specific_refresh"] is True
        assert row["readiness_is_terminal_gate"] is False
        assert row["probability_publishable_from_readiness"] is False
        assert row["terminal_ceiling_effect"] == "NONE"
        assert row["can_execute"] is False


def test_ncaaf_profile_points_to_existing_dynamic_readiness_source():
    ncaaf = get_ml_research_readiness("NCAAF")
    assert ncaaf["live_readiness_source"] == "/internal/ncaaf/readiness"
    assert ncaaf["requires_event_specific_refresh"] is True


def test_readiness_never_contains_model_probability_or_terminal_output():
    payload = get_ml_research_readiness()
    keys = set(_walk_keys(payload))
    assert not FORBIDDEN_MODEL_OUTPUT_KEYS.intersection(keys)


def test_public_readiness_endpoint_returns_all_six_sports():
    response = client.get("/ml-research-readiness")
    assert response.status_code == 200
    payload = response.json()
    assert [row["sport"] for row in payload["sports"]] == list(SUPPORTED_ML_READINESS_SPORTS)
    assert payload["status_basis"] == "CODEBASE_CAPABILITY_BASELINE"
    assert payload["requires_event_specific_refresh"] is True
    assert payload["readiness_is_terminal_gate"] is False
    assert payload["can_execute"] is False


def test_public_readiness_endpoint_returns_case_insensitive_sport():
    response = client.get("/ml-research-readiness/nfl")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sport"] == "NFL"
    assert payload["model_capability"] == "MODEL_UNAVAILABLE"
    assert payload["probability_publishable_from_readiness"] is False


def test_unsupported_sport_returns_fail_closed_404():
    response = client.get("/ml-research-readiness/cricket")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "ML_RESEARCH_READINESS_SPORT_UNSUPPORTED"
    assert detail["readiness_is_terminal_gate"] is False
    assert detail["probability_publishable_from_readiness"] is False
    assert detail["can_execute"] is False


def test_existing_score_event_route_remains_registered_separately():
    paths = {route.path for route in api.app.routes}
    assert "/score-event" in paths
    assert "/ml-research-readiness" in paths
    assert "/ml-research-readiness/{sport}" in paths
