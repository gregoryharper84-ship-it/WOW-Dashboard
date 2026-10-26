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
        "requester_host_identity": "WOW_BETTING_ENGINE",
        "candidate_family": "TEAM_EVENT",
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


def _register(sport: str, scorer, **kwargs):
    return register_team_event_bridge(
        sport,
        adapter_name=f"TEST_{sport}_ADAPTER",
        controlling_specialist=f"TEST_{sport}_SPECIALIST",
        scorer=scorer,
        required_inputs=kwargs.pop("required_inputs", TEAM_EVENT_INPUT_CONTRACTS[sport]),
        standard_package_validation=kwargs.pop("standard_package_validation", True),
        **kwargs,
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
    assert health["MLB"]["feature_consumption_receipt_enabled"] is True
    assert health["MLB"]["feature_schema_version"] is None
    for sport in ("NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"):
        assert health[sport]["status"] == "MODEL_UNAVAILABLE"
        assert health[sport]["registered"] is False
        assert health[sport]["feature_consumption_receipt_enabled"] is False
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
    assert caught.value.detail["controlling_engine_identity"] == "LLP_TEAM_BETTING_ENGINE"
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
    assert result["feature_consumption_receipt_id"]
    assert result["feature_consumption_receipt"]["consumption_verification_status"] == "UNVERIFIED"
    assert result["feature_consumption_receipt"]["receipt_complete"] is False
    assert result["can_execute"] is False


def test_declared_feature_receipt_does_not_change_probability_or_publication():
    request = SimpleNamespace(
        requester_host_identity="WOW_BETTING_ENGINE",
        candidate_family="TEAM_EVENT",
        sport="WNBA",
        league="WNBA",
        official_event_id="event-feature-1",
        home_team="Alpha",
        away_team="Beta",
        sport_specific_evidence={
            "home_win_pct": 0.62,
            "away_win_pct": 0.48,
            "calibration_artifact": {"artifact_id": "cal-1"},
            "feature_provenance": {
                "home_win_pct": {"source": "strength-store", "provenance_id": "h"},
                "away_win_pct": {"source": "strength-store", "provenance_id": "a"},
                "calibration_artifact": {"source": "calibration-registry", "provenance_id": "c"},
            },
        },
    )

    def scorer(*args, **kwargs):
        package = _valid_package("feature-declared")
        package["consumed_feature_ids"] = ["home_win_pct", "away_win_pct"]
        return package

    _register(
        "WNBA",
        scorer,
        required_inputs=(
            "official_event_id",
            "home_win_pct",
            "away_win_pct",
            "calibration_artifact",
        ),
        feature_schema_version="WNBA_TEST_FEATURES_V1",
        feature_roles={
            "home_win_pct": "MODEL_PRIMARY",
            "away_win_pct": "MODEL_PRIMARY",
        },
        critical_features=("home_win_pct", "away_win_pct"),
    )
    result = _score(request)
    receipt = result["feature_consumption_receipt"]

    assert result["calibrated_probability"] == 0.72
    assert result["calibrated_lower_bound"] == 0.66
    assert result["calibrated_upper_bound"] == 0.78
    assert result["rank_eligible"] is True
    assert result["probability_publishable"] is True
    assert receipt["feature_schema_version"] == "WNBA_TEST_FEATURES_V1"
    assert receipt["features_consumed"] == 2
    assert receipt["consumption_verification_status"] == "VERIFIED"
    assert receipt["receipt_complete"] is True
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
    assert result["feature_consumption_receipt_id"]
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


def test_cross_sport_enforcement_registration_requires_backend_verifier():
    with pytest.raises(ValueError, match="certified cross-sport evidence verifier required"):
        _register(
            "NBA",
            lambda *args, **kwargs: _valid_package("nba-evidence"),
            evidence_integrity_required=True,
        )
    assert "NBA" not in TEAM_EVENT_BRIDGES


def test_model_scorer_cannot_self_attest_evidence_status():
    def scorer(*args, **kwargs):
        result = _valid_package("nba-evidence")
        result["cross_sport_evidence"] = {"claimed": "VERIFIED"}
        result["cross_sport_evidence_attested"] = True
        result["cross_sport_evidence_required"] = False
        return result

    _register(
        "NBA",
        scorer,
        evidence_integrity_required=True,
        evidence_attestation_verifier=lambda envelope, model_result: False,
    )
    result = _score(_request("NBA"))
    assert result["cross_sport_evidence_required"] is True
    assert result["cross_sport_evidence_attested"] is False
    assert result["cross_sport_evidence_producer_status"] == "UNVERIFIED"
    assert result["calibrated_probability"] == 0.72
    assert result["can_execute"] is False


def test_registered_backend_verifier_can_attest_model_result_only_when_enrolled():
    def scorer(*args, **kwargs):
        result = _valid_package("nba-evidence")
        result["cross_sport_evidence"] = {"trusted_snapshot_id": "verified-source-1"}
        return result

    verifier = lambda envelope, model_result: (
        envelope.get("trusted_snapshot_id") == "verified-source-1"
        and model_result.get("candidate_id") == "nba-evidence"
    )
    _register(
        "NBA",
        scorer,
        evidence_integrity_required=True,
        evidence_attestation_verifier=verifier,
    )
    result = _score(_request("NBA"))
    # This simple scoring fixture has no complete numerical-consumption receipt:
    # a registered verifier must not transform that deficit into a PASS.
    assert result["cross_sport_evidence_attested"] is False
    assert result["cross_sport_evidence_producer_status"] == "UNVERIFIED"
    assert result["cross_sport_evidence"]["feature_consumption_receipt_id"] == result["feature_consumption_receipt_id"]
    assert team_event_bridge_health()["NBA"]["cross_sport_evidence_verifier_registered"] is True


def test_legacy_scorer_evidence_claim_never_activates_enrollment():
    def scorer(*args, **kwargs):
        result = _valid_package("nba-unenrolled")
        result["cross_sport_evidence"] = {"claimed": "VERIFIED"}
        result["cross_sport_evidence_attested"] = True
        result["cross_sport_evidence_required"] = True
        return result

    _register("NBA", scorer)
    result = _score(_request("NBA"))
    assert result["cross_sport_evidence_required"] is False
    assert result["cross_sport_evidence_attested"] is False
    assert result["cross_sport_evidence_producer_status"] == "NOT_ENROLLED"
    assert team_event_bridge_health()["NBA"]["cross_sport_evidence_enforced"] is False
