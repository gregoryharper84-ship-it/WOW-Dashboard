from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17.team_event_bridge_runtime import (
    DISCOVERY_ONLY_LABEL,
    NO_VERIFIED_THREE_TEAM_PARLAY,
    TEAM_EVENT_BRIDGES,
    discovery_only_candidate,
    register_team_event_bridge,
    score_registered_team_event_request,
    team_event_bridge_health,
    verified_three_team_parlay_status,
)
from v17.team_event_capability_manifest import TEAM_EVENT_INPUT_CONTRACTS


@pytest.fixture(autouse=True)
def isolated_bridge_registry():
    original = dict(TEAM_EVENT_BRIDGES)
    TEAM_EVENT_BRIDGES.clear()
    try:
        yield
    finally:
        TEAM_EVENT_BRIDGES.clear()
        TEAM_EVENT_BRIDGES.update(original)


def _request(sport: str, *, complete: bool = True):
    required = TEAM_EVENT_INPUT_CONTRACTS[sport]
    evidence = {field: f"verified:{field}" for field in required}
    core = {
        "sport": sport,
        "league": sport,
        "official_event_id": "event-1",
        "home_team": "Alpha",
        "away_team": "Beta",
        "settlement_basis": "FULL_GAME",
        "sport_specific_evidence": evidence,
    }
    if not complete:
        evidence.pop(required[-1], None)
        core[required[-1]] = None
    return SimpleNamespace(**core)


def _valid_package(candidate_id: str = "candidate-1"):
    return {
        "candidate_id": candidate_id,
        "calibrated_probability": 0.72,
        "calibrated_lower_bound": 0.66,
        "calibrated_upper_bound": 0.78,
        "immutable_model_timestamp": "2026-09-09T17:00:00+00:00",
        "calibration_method": "ISOTONIC",
        "calibration_version": "v1",
        "source_snapshot_id": "snapshot-1",
        "source_snapshot_timestamp": "2026-09-09T16:55:00+00:00",
        "outcome_space": "TWO_WAY_WINNER",
        "model_version": "fake-certified-v1",
        "event_status": "PREGAME",
        "probability_publishable": True,
        "rank_eligible": True,
        "blockers": [],
        "can_execute": False,
    }


def _register(sport: str, scorer):
    return register_team_event_bridge(
        sport,
        adapter_name=f"TEST_{sport}_ADAPTER",
        controlling_specialist=f"TEST_{sport}_SPECIALIST",
        scorer=scorer,
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS[sport],
        standard_package_validation=True,
    )


def _score(req):
    return score_registered_team_event_request(
        req,
        event_api=object(),
        canonical_hydration_required=False,
    )


def test_health_separates_catalog_support_from_registered_model_support():
    _register("MLB", lambda *args, **kwargs: _valid_package("mlb"))
    health = team_event_bridge_health()

    assert health["MLB"]["status"] == "UP"
    for sport in ("NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"):
        assert health[sport]["status"] == "MODEL_UNAVAILABLE"
        assert health[sport]["registered"] is False
        assert health[sport]["discovery_supported"] is True
        assert health[sport]["can_execute"] is False


def test_discovery_only_candidate_can_never_rank_or_publish():
    row = discovery_only_candidate("TENNIS", candidate_id="tennis-1")
    assert row["status"] == DISCOVERY_ONLY_LABEL
    assert row["model_status"] == "MODEL_UNAVAILABLE"
    assert row["rank_eligible"] is False
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


@pytest.mark.parametrize("sport", ["NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"])
def test_no_registered_model_is_model_unavailable(sport):
    with pytest.raises(HTTPException) as caught:
        _score(_request(sport))
    assert caught.value.detail["code"] == "MODEL_UNAVAILABLE"
    assert caught.value.detail["rank_eligible"] is False
    assert caught.value.detail["can_execute"] is False


@pytest.mark.parametrize("sport", ["NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"])
def test_registered_model_with_missing_inputs_is_inputs_insufficient(sport):
    _register(sport, lambda *args, **kwargs: _valid_package())
    with pytest.raises(HTTPException) as caught:
        _score(_request(sport, complete=False))
    assert caught.value.detail["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert caught.value.detail["missing_fields"]
    assert caught.value.detail["rank_eligible"] is False
    assert caught.value.detail["can_execute"] is False


@pytest.mark.parametrize("sport", ["NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"])
def test_registered_scorer_timeout_is_scorer_failed_not_model_unavailable(sport):
    def timeout(*args, **kwargs):
        raise TimeoutError("synthetic timeout")

    _register(sport, timeout)
    with pytest.raises(HTTPException) as caught:
        _score(_request(sport))
    assert caught.value.detail["code"] == "MODEL_SCORER_FAILED"
    assert caught.value.detail["model_invoked"] is True
    assert caught.value.detail["can_execute"] is False


@pytest.mark.parametrize("sport", ["NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"])
def test_malformed_probability_package_is_output_invalid(sport):
    _register(sport, lambda *args, **kwargs: {"calibrated_probability": 0.7})
    with pytest.raises(HTTPException) as caught:
        _score(_request(sport))
    assert caught.value.detail["code"] == "MODEL_OUTPUT_INVALID"
    assert caught.value.detail["rank_eligible"] is False
    assert caught.value.detail["can_execute"] is False


@pytest.mark.parametrize("sport", ["NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"])
def test_complete_registered_model_package_is_rank_eligible(sport):
    _register(sport, lambda *args, **kwargs: _valid_package(sport.lower()))
    result = _score(_request(sport))
    assert result["calibrated_probability"] == 0.72
    assert result["calibrated_lower_bound"] == 0.66
    assert result["rank_eligible"] is True
    assert result["probability_publishable"] is True
    assert result["can_execute"] is False


@pytest.mark.parametrize("sport", ["NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"])
def test_market_failure_does_not_erase_valid_sporting_probability(sport):
    def scorer(*args, **kwargs):
        package = _valid_package(sport.lower())
        package.update(
            {
                "market_gate": "DATA_UNOBTAINABLE",
                "edge_publication_status": "BLOCKED",
            }
        )
        return package

    _register(sport, scorer)
    result = _score(_request(sport))
    assert result["calibrated_probability"] == 0.72
    assert result["calibrated_lower_bound"] == 0.66
    assert result["probability_publishable"] is True
    assert result["market_gate"] == "DATA_UNOBTAINABLE"
    assert result["edge_publication_status"] == "BLOCKED"
    assert result["can_execute"] is False


def test_soccer_contract_preserves_draw_as_third_outcome():
    assert "home_draw_away_outcome_space" in TEAM_EVENT_INPUT_CONTRACTS["SOCCER"]
    assert "competition_rules" in TEAM_EVENT_INPUT_CONTRACTS["SOCCER"]


def test_three_team_parlay_rejects_discovery_or_unverified_rows():
    rows = [_valid_package(f"leg-{index}") for index in range(1, 4)]
    rows[1] = discovery_only_candidate("SOCCER", candidate_id="leg-2")
    result = verified_three_team_parlay_status(rows)
    assert result["status"] == "NO_VERIFIED_3_TEAM_PARLAY"
    assert result["message"] == NO_VERIFIED_THREE_TEAM_PARLAY
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_three_team_research_card_requires_three_valid_fresh_packages():
    rows = [_valid_package(f"leg-{index}") for index in range(1, 4)]
    result = verified_three_team_parlay_status(rows)
    assert result["status"] == "VERIFIED_3_TEAM_RESEARCH_CARD"
    assert result["rank_eligible"] is True
    assert result["blockers"] == []
    assert result["can_execute"] is False
