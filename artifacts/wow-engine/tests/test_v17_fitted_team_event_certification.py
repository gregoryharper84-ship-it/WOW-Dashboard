from __future__ import annotations

from copy import deepcopy

import pytest

from v17.fitted_team_event_certification import (
    TeamEventCertificationError,
    parse_certification,
    resolve_certification,
    validate_prediction_package,
)


CERTIFICATION = {
    "certification_id": "cert-wnba-1",
    "sport": "WNBA",
    "league_scope": "WNBA",
    "controlling_specialist": "WNBA_GAME_WIN_V17",
    "model_family": "BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
    "model_version": "wnba-fitted-v17.1",
    "artifact_id": "artifact-wnba-1",
    "artifact_sha256": "a" * 64,
    "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2",
    "input_contract_version": "WNBA_GAME_WIN_INPUTS_V1",
    "calibration_artifact_id": "cal-wnba-1",
    "calibration_sha256": "b" * 64,
    "calibration_method": "PLATT_TIME_SPLIT_V1",
    "independent_verification_status": "PASS",
    "certification_status": "CERTIFIED",
    "certified_at": "2026-09-21T18:00:00Z",
    "probability_publishable": True,
    "can_execute": False,
}


def _package() -> dict:
    return {
        "sport": "WNBA",
        "controlling_specialist": "WNBA_GAME_WIN_V17",
        "model_version": "wnba-fitted-v17.1",
        "artifact_id": "artifact-wnba-1",
        "source_snapshot_id": "snapshot-1",
        "model_timestamp": "2026-09-21T19:00:00Z",
        "raw_probability": 0.67,
        "calibrated_probability": 0.64,
        "lower_bound": 0.59,
        "upper_bound": 0.69,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "independent_verification_status": "PASS",
        "latest_material_update_timestamp": "2026-09-21T18:30:00Z",
        "probability_package_valid": True,
        "rank_eligible": True,
        "status": "PASS",
        "can_execute": False,
    }


def test_valid_certification_parses_with_can_execute_false():
    certification = parse_certification(CERTIFICATION)
    assert certification.sport == "WNBA"
    assert certification.model_version == "wnba-fitted-v17.1"
    assert certification.independent_verification_status == "PASS"
    assert certification.can_execute is False


def test_registration_shaped_record_cannot_fake_certification():
    fake = {
        "sport": "WNBA",
        "controlling_specialist": "WNBA_GAME_WIN_V17",
        "can_execute": False,
    }
    with pytest.raises(TeamEventCertificationError) as exc:
        parse_certification(fake)
    assert exc.value.code == "TEAM_EVENT_CERTIFICATION_FIELD_MISSING"


def test_independent_verification_must_pass():
    proof = deepcopy(CERTIFICATION)
    proof["independent_verification_status"] = "FAIL"
    with pytest.raises(TeamEventCertificationError) as exc:
        parse_certification(proof)
    assert exc.value.code == "TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS"


def test_certification_never_allows_execution():
    proof = deepcopy(CERTIFICATION)
    proof["can_execute"] = True
    with pytest.raises(TeamEventCertificationError) as exc:
        parse_certification(proof)
    assert exc.value.code == "TEAM_EVENT_CERTIFICATION_CAN_EXECUTE_FORBIDDEN"


def test_common_probability_package_matches_certified_artifact_and_bounds():
    certification = parse_certification(CERTIFICATION)
    ok, blockers = validate_prediction_package(_package(), certification)
    assert ok is True
    assert blockers == ()


def test_stale_probability_package_is_not_valid_for_publication():
    certification = parse_certification(CERTIFICATION)
    package = _package()
    package["model_timestamp"] = "2026-09-21T18:00:00Z"
    ok, blockers = validate_prediction_package(package, certification)
    assert ok is False
    assert "MODEL_STALE_AFTER_MATERIAL_UPDATE" in blockers


def test_artifact_mismatch_is_fail_closed():
    certification = parse_certification(CERTIFICATION)
    package = _package()
    package["artifact_id"] = "artifact-other"
    ok, blockers = validate_prediction_package(package, certification)
    assert ok is False
    assert "TEAM_EVENT_CERTIFICATION_ARTIFACT_MISMATCH" in blockers


def test_bound_order_is_validated():
    certification = parse_certification(CERTIFICATION)
    package = _package()
    package["lower_bound"] = 0.66
    package["calibrated_probability"] = 0.64
    ok, blockers = validate_prediction_package(package, certification)
    assert ok is False
    assert "CALIBRATED_BOUNDS_ORDER_INVALID" in blockers


class _Response:
    def __init__(self, data):
        self.data = data


class _RPC:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return _Response(self.data)


class _Client:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RPC(self.data)


def test_registry_resolution_requires_service_receipt_payload():
    client = _Client({"ok": True, **CERTIFICATION})
    certification = resolve_certification(client, sport="WNBA", league_scope="WNBA")
    assert certification.certification_id == "cert-wnba-1"
    assert client.calls == [
        (
            "wow_v17_active_team_event_certification",
            {"p_sport": "WNBA", "p_league_scope": "WNBA"},
        )
    ]


def test_registry_unavailable_is_not_model_unavailable():
    client = _Client({
        "ok": False,
        "code": "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED",
        "can_execute": False,
    })
    with pytest.raises(TeamEventCertificationError) as exc:
        resolve_certification(client, sport="WNBA", league_scope="WNBA")
    assert exc.value.code == "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"
    assert exc.value.code != "MODEL_UNAVAILABLE"
