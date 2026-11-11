"""Fail-closed production-model readiness register: negative-first tests."""
from __future__ import annotations

import pytest

from v17.sport_model_production_readiness import (
    AUDITED_SPORTS,
    BLOCKED,
    PRODUCTION_READY,
    REGISTER_VERSION,
    assess_sport,
    build_readiness_register,
)

ALL_OBSERVATIONS = {
    "starting_goalie_confirmation_status": "CONFIRMED",
    "pregame_inputs_status": "COMPLETE",
    "source_to_scorer_integration_status": "VERIFIED",
    "certified_calibration_status": "PASS",
    "forward_validation_status": "PASS",
    "publication_provenance_status": "VERIFIED",
    "certification_requirement_status": "PASS",
    "certified_live_serving_status": "PASS",
    "pregame_model_integration_status": "VERIFIED",
    "independent_validation_status": "PASS",
    "production_specialist_status": "DEMONSTRATED",
}


def _all_observations() -> dict[str, dict[str, str]]:
    return {sport: dict(ALL_OBSERVATIONS) for sport in AUDITED_SPORTS}


def _certified_health(sport: str, *, certification: str = "CERTIFIED") -> dict:
    return {
        sport: {
            "registered": True,
            "scorer_resolvable": True,
            "model_artifact_present": True,
            "certification_status": certification,
        }
    }


def test_every_audited_sport_is_present_once_and_blocked_without_evidence():
    register = build_readiness_register()

    assert register["register_version"] == REGISTER_VERSION
    assert [row["sport"] for row in register["sports"]] == list(AUDITED_SPORTS)
    assert len({row["sport"] for row in register["sports"]}) == len(AUDITED_SPORTS)
    assert register["all_audited_sports_blocked"] is True
    assert register["production_ready_sports"] == []
    assert register["blocked_sports"] == list(AUDITED_SPORTS)
    assert register["probability_behavior_changed"] is False
    assert register["can_execute"] is False
    for row in register["sports"]:
        assert row["production_model_ready"] is False
        assert row["readiness_state"] == BLOCKED
        assert row["can_execute"] is False


def test_audit_named_blockers_surface_as_typed_codes():
    rows = {row["sport"]: row for row in build_readiness_register()["sports"]}

    assert "NHL_STARTING_GOALIE_CONFIRMATION_NOT_SUPPLIED" in rows["NHL"]["blockers"]
    assert "NHL_PREGAME_INPUTS_INCOMPLETE" in rows["NHL"]["blockers"]
    assert "NBA_SOURCE_TO_SCORER_INTEGRATION_UNVERIFIED" in rows["NBA"]["blockers"]
    assert "NBA_CERTIFIED_CALIBRATION_UNAVAILABLE" in rows["NBA"]["blockers"]
    assert "NFL_FORWARD_VALIDATION_UNPROVEN" in rows["NFL"]["blockers"]
    assert "NFL_PUBLICATION_PROVENANCE_UNVERIFIED" in rows["NFL"]["blockers"]
    assert "NCAAF_CERTIFIED_LIVE_SERVING_INCOMPLETE" in rows["NCAAF"]["blockers"]
    assert "NCAAB_PREGAME_MODEL_INTEGRATION_INCOMPLETE" in rows["NCAAB"]["blockers"]
    assert "NCAAB_INDEPENDENT_VALIDATION_UNPROVEN" in rows["NCAAB"]["blockers"]
    assert "TENNIS_PRODUCTION_SPECIALIST_NOT_DEMONSTRATED" in rows["TENNIS"]["blockers"]
    assert "SOCCER_PRODUCTION_SPECIALIST_NOT_DEMONSTRATED" in rows["SOCCER"]["blockers"]


def test_observations_alone_cannot_certify_a_sport():
    register = build_readiness_register(observations=_all_observations())

    assert register["all_audited_sports_blocked"] is True
    for row in register["sports"]:
        assert row["production_model_ready"] is False
        assert "TEAM_EVENT_BRIDGE_NOT_REGISTERED" in row["blockers"]
        assert "TEAM_EVENT_FITTED_MODEL_CERTIFICATION_NOT_ACTIVE" in row["blockers"]
        certification_gate = next(
            gate for gate in row["gates"] if gate["gate_id"] == "FITTED_MODEL_CERTIFIED"
        )
        assert certification_gate["satisfied"] is False


def test_registration_and_evidence_without_certification_stays_blocked():
    observations = {"NBA": dict(ALL_OBSERVATIONS)}
    row = assess_sport(
        "NBA",
        health=_certified_health("NBA", certification="CANDIDATE_REGISTERED_UNCERTIFIED"),
        observations=observations,
    )

    assert row["production_model_ready"] is False
    assert row["readiness_state"] == BLOCKED
    assert "TEAM_EVENT_FITTED_MODEL_CERTIFICATION_NOT_ACTIVE" in row["blockers"]


def test_runtime_pass_without_named_artifact_version_cannot_certify():
    for version, calibration in (("", "NFL_PLATT_TIME_SPLIT_V1_TEST"), (None, None), ("   ", "")):
        health = {
            "NFL": {
                "registered": True,
                "scorer_resolvable": True,
                "model_artifact_present": True,
                "certification_status": "CANDIDATE_REGISTERED_UNCERTIFIED",
                "runtime_artifact_certification_status": "PASS",
                "runtime_model_artifact_version": version,
                "runtime_calibration_version": calibration,
            }
        }
        row = assess_sport("NFL", health=health, observations={"NFL": dict(ALL_OBSERVATIONS)})

        assert row["production_model_ready"] is False
        assert row["readiness_state"] == BLOCKED
        assert row["certification_identity"] is None
        assert "TEAM_EVENT_FITTED_MODEL_CERTIFICATION_NOT_ACTIVE" in row["blockers"]
        assert "SERVER_CALIBRATION_ARTIFACT_NOT_BOUND" in row["blockers"]
        assert "BACKEND_SPORT_EVIDENCE_HYDRATOR_NOT_BOUND" in row["blockers"]


def test_full_authoritative_evidence_and_receipts_only_then_read_ready():
    health = {
        "NFL": {
            "registered": True,
            "scorer_resolvable": True,
            "model_artifact_present": True,
            "certification_status": "CANDIDATE_REGISTERED_UNCERTIFIED",
            "runtime_artifact_certification_status": "PASS",
            "runtime_artifact_certification_code": "NFL_ACTIVE_PROMOTED_CHAMPION_PROVEN",
            "runtime_model_artifact_version": "NFL_EVENT_LOGREG_PLATT_V1_TEST",
            "runtime_calibration_version": "NFL_PLATT_TIME_SPLIT_V1_TEST",
        }
    }
    row = assess_sport(
        "NFL", health=health, observations={"NFL": dict(ALL_OBSERVATIONS)}
    )

    assert row["production_model_ready"] is True
    assert row["readiness_state"] == PRODUCTION_READY
    assert row["blockers"] == []
    assert row["certification_identity"] == "NFL_EVENT_LOGREG_PLATT_V1_TEST"
    assert row["certification_identity"] != "RUNTIME_CHAMPION"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


@pytest.mark.parametrize(
    "sport,certification,expected_blocker",
    [
        ("NHL", "CERTIFIED", "SERVER_CALIBRATION_ARTIFACT_NOT_BOUND"),
        ("NCAAF", "CERTIFIED", "SERVER_CALIBRATION_ARTIFACT_NOT_BOUND"),
        ("TENNIS", "CERTIFIED", "SERVER_CALIBRATION_ARTIFACT_NOT_BOUND"),
    ],
)
def test_certification_alone_never_binds_calibration_or_hydration(
    sport, certification, expected_blocker
):
    row = assess_sport(
        sport,
        health=_certified_health(sport, certification=certification),
        observations={sport: dict(ALL_OBSERVATIONS)},
    )

    assert row["production_model_ready"] is False
    assert expected_blocker in row["blockers"]
    assert "BACKEND_SPORT_EVIDENCE_HYDRATOR_NOT_BOUND" in row["blockers"]


def test_observation_values_must_match_exact_receipt_status():
    for bad in ("PROJECTED", "CONFIRMED_BY_CALLER", "", 1, True, None, ["CONFIRMED"]):
        row = assess_sport(
            "NHL",
            health=_certified_health("NHL"),
            observations={
                "NHL": {
                    **ALL_OBSERVATIONS,
                    "starting_goalie_confirmation_status": bad,
                }
            },
        )
        assert row["production_model_ready"] is False
        assert "NHL_STARTING_GOALIE_CONFIRMATION_NOT_SUPPLIED" in row["blockers"]


def test_register_is_observability_only_and_never_executes():
    register = build_readiness_register(observations=_all_observations())
    forbidden = {
        "raw_probability",
        "calibrated_probability",
        "calibrated_lower_bound",
        "lower_bound",
        "upper_bound",
        "edge",
        "stake",
    }
    for row in register["sports"]:
        assert row["can_execute"] is False
        assert row["probability_publishable"] is False
        assert row["market_probability_substitution_allowed"] is False
        assert row["generic_reasoning_substitution_allowed"] is False
        assert row["legacy_fallback_allowed"] is False
        assert row["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert forbidden.isdisjoint(row)


def test_unsupported_sport_is_rejected_rather_than_guessed():
    with pytest.raises(ValueError):
        assess_sport("BASEBALL_X")


def test_register_output_is_deterministic():
    assert build_readiness_register() == build_readiness_register()
