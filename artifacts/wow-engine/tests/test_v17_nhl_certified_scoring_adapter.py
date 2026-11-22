"""Registry-backed NHL certified scoring adapter — Fix D contract tests."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v17.fitted_team_event_certification import (
    TeamEventCertificationError,
    parse_certification,
    validate_prediction_package,
)
from v17 import nhl_certified_scoring_adapter as adapter


NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)


def _receipt_payload(**overrides) -> dict:
    row = {
        "certification_id": "cert-nhl-1",
        "sport": "NHL",
        "league_scope": "NHL",
        "controlling_specialist": "NHL_GAME_WIN_PROBABILITY_EXPERT_V1",
        "model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1",
        "model_version": "NHL_RS_LOGISTIC_V1-abcd1234efgh5678-xyz987654321",
        "artifact_id": "artifact-nhl-1",
        "artifact_sha256": "a" * 64,
        "feature_schema_version": "NHL_REGULAR_SEASON_FEATURES_V1",
        "input_contract_version": "NHL_GAME_WIN_INPUTS_V1",
        "calibration_artifact_id": "cal-nhl-1",
        "calibration_sha256": "b" * 64,
        "calibration_method": "EMPIRICAL_WILSON_BINS_V1",
        "independent_verification_status": "PASS",
        "certification_status": "CERTIFIED",
        "certified_at": "2026-11-22T12:00:00Z",
        "probability_publishable": True,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _evidence(**overrides) -> dict:
    row = {
        "official_event_id": "2026020001",
        "goalie_status": "CONFIRMED",
        "goalie_status_observed_at": "2026-11-22T17:30:00+00:00",
        "goalie_identity_snapshot_id": "goalie-snap-1",
        "settlement_basis": "HOME_AWAY_INCLUDING_OT_SO",
        "source_snapshot_id": "snap-1",
        "source_snapshot_timestamp": "2026-11-22T17:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_adapter_never_enables_execution():
    assert adapter.CAN_EXECUTE is False


def test_adapter_fails_closed_without_registry_receipt(monkeypatch):
    def _boom(*_a, **_k):
        raise TeamEventCertificationError(
            "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED", "NHL"
        )

    monkeypatch.setattr(adapter, "resolve_certification", _boom)
    req = SimpleNamespace(sport_specific_evidence=_evidence())
    with pytest.raises(adapter.NHLCertifiedScoringError) as exc:
        adapter.score_nhl_certified_event(req, db=object(), now_utc=NOW)
    assert exc.value.code == "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"
    assert exc.value.can_execute is False


def test_adapter_rejects_request_asserted_certification(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_certification",
        lambda *_a, **_k: (_ for _ in ()).throw(
            TeamEventCertificationError("TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED", "NHL")
        ),
    )
    req = SimpleNamespace(
        sport_specific_evidence={
            **_evidence(),
            "certification_status": "CERTIFIED",
            "independent_verification_status": "PASS",
        }
    )
    with pytest.raises(adapter.NHLCertifiedScoringError) as exc:
        adapter.score_nhl_certified_event(req, db=object(), now_utc=NOW)
    assert exc.value.code == "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"


def test_adapter_rejects_identity_calibrator_even_if_receipt_parses():
    receipt = parse_certification(
        _receipt_payload(calibration_method="IDENTITY_RAW_PROBABILITY_V1")
    )
    with pytest.raises(adapter.NHLCertifiedScoringError) as exc:
        adapter.build_nhl_certified_envelope(
            receipt=receipt,
            raw_home=0.55,
            calibrated_home=0.55,
            lower=None,
            upper=None,
            evidence=_evidence(),
            model_timestamp="2026-11-22T12:00:00+00:00",
            latest_material_update_timestamp="2026-11-22T11:00:00+00:00",
            home_team="BOS",
            away_team="NYR",
            official_event_id="2026020001",
            now_utc=NOW,
        )
    assert "NHL_IDENTITY_CALIBRATOR_FORBIDDEN" in exc.value.blockers


def test_adapter_emits_home_and_away_bounds_matching_validate_prediction_package():
    receipt = parse_certification(_receipt_payload())
    package = adapter.build_nhl_certified_envelope(
        receipt=receipt,
        raw_home=0.58,
        calibrated_home=0.56,
        lower=0.51,
        upper=0.61,
        evidence=_evidence(),
        model_timestamp="2026-11-22T12:00:00+00:00",
        latest_material_update_timestamp="2026-11-22T11:00:00+00:00",
        home_team="BOS",
        away_team="NYR",
        official_event_id="2026020001",
        now_utc=NOW,
    )
    assert package["can_execute"] is False
    assert package["calibration_artifact_fingerprint"] == "b" * 64
    assert package["calibration_artifact_fingerprint"] != package.get("model_artifact_checksum")
    assert package["calibrated_home_probability"] == pytest.approx(0.56)
    assert package["calibrated_away_probability"] == pytest.approx(0.44)
    assert package["calibrated_away_lower_bound"] == pytest.approx(0.39)
    assert package["calibrated_away_upper_bound"] == pytest.approx(0.49)
    assert package["lower_bound"] == pytest.approx(0.51)
    assert package["upper_bound"] == pytest.approx(0.61)
    assert package["probability_package_valid"] is True
    assert package["independent_verification_status"] == "PASS"
    assert package["calibration_artifact_certified"] is True
    assert package["outcome_space"] == "HOME_AWAY_INCLUDING_OT_SO"
    ok, blockers = validate_prediction_package(package, receipt)
    assert ok is True
    assert blockers == ()


def test_goalie_hold_still_blocks_with_valid_receipt():
    receipt = parse_certification(_receipt_payload())
    with pytest.raises(adapter.NHLCertifiedScoringError) as exc:
        adapter.build_nhl_certified_envelope(
            receipt=receipt,
            raw_home=0.58,
            calibrated_home=0.56,
            lower=0.51,
            upper=0.61,
            evidence=_evidence(goalie_status="PROJECTED"),
            model_timestamp="2026-11-22T12:00:00+00:00",
            latest_material_update_timestamp="2026-11-22T11:00:00+00:00",
            home_team="BOS",
            away_team="NYR",
            official_event_id="2026020001",
            now_utc=NOW,
        )
    assert "NHL_STARTING_GOALIE_NOT_CONFIRMED" in exc.value.blockers
    assert exc.value.can_execute is False


def test_adapter_does_not_mutate_candidate_rows(monkeypatch):
    writes: list[tuple] = []

    class _DB:
        def table(self, name):
            writes.append(("table", name))
            raise AssertionError("certified adapter must not mutate candidate tables")

        def rpc(self, *_a, **_k):
            return type(
                "R",
                (),
                {"data": {**_receipt_payload(), "ok": True, "code": "TEAM_EVENT_SPECIALIST_CERTIFICATION_READY"}},
            )()

    monkeypatch.setattr(
        adapter,
        "resolve_certification",
        lambda client, *, sport, league_scope=None: parse_certification(
            {**_receipt_payload(), "ok": True}
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_score_matching_certified_artifacts",
        lambda **kwargs: {
            "raw_home": 0.58,
            "calibrated_home": 0.56,
            "lower": 0.51,
            "upper": 0.61,
            "model_timestamp": "2026-11-22T12:00:00+00:00",
            "latest_material_update_timestamp": "2026-11-22T11:00:00+00:00",
            "home_team": "BOS",
            "away_team": "NYR",
            "official_event_id": "2026020001",
            "model_artifact_checksum": "a" * 64,
        },
    )
    req = SimpleNamespace(
        sport_specific_evidence=_evidence(),
        home_team="BOS",
        away_team="NYR",
        event_start_time_utc="2026-11-22T20:00:00+00:00",
        event_key="NHL:2026020001",
        settlement_basis="HOME_AWAY_INCLUDING_OT_SO",
    )
    package = adapter.score_nhl_certified_event(req, db=_DB(), now_utc=NOW)
    assert package["probability_publishable"] is True
    assert package["can_execute"] is False
    assert writes == []
