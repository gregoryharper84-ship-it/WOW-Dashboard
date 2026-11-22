"""Registry-backed NHL certified scoring adapter — Fix D contract tests."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from v17.candidate_certification_evidence import _hash
from v17.fitted_team_event_certification import (
    TeamEventCertificationError,
    parse_certification,
    validate_prediction_package,
)
from v17 import nhl_certified_scoring_adapter as adapter
import v17.nhl_fitted_specialist as nhl


NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "nhl_fitted_candidate_20261107.json"


def _empirical_calibrator() -> dict:
    return {
        "method": "EMPIRICAL_WILSON_BINS_V1",
        "training_n": 1226,
        "selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
        "bins": [
            {
                "raw_mean": value,
                "calibrated_probability": min(max(value, 0.02), 0.98),
                "wilson_lower": max(0.001, value - 0.05),
                "wilson_upper": min(0.999, value + 0.05),
            }
            for value in (0.05, 0.25, 0.45, 0.65, 0.85, 0.95)
        ],
    }


def _receipt_payload(**overrides) -> dict:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calibrator = _empirical_calibrator()
    row = {
        "certification_id": "cert-nhl-1",
        "sport": "NHL",
        "league_scope": "NHL",
        "controlling_specialist": "NHL_GAME_WIN_PROBABILITY_EXPERT_V1",
        "model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1",
        "model_version": fixture["model_artifact_version"],
        "artifact_id": "artifact-nhl-1",
        "artifact_sha256": fixture["artifact_checksum"],
        "feature_schema_version": "NHL_REGULAR_SEASON_FEATURES_V1",
        "input_contract_version": "NHL_GAME_WIN_INPUTS_V1",
        "calibration_artifact_id": "cal-nhl-1",
        "calibration_sha256": _hash(calibrator),
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
        "season": "20262027",
        "neutral_site": False,
        "injury_report": {"status": "NONE"},
        "rest_travel": {"home_rest_days": 2, "away_rest_days": 1},
    }
    row.update(overrides)
    return row


class _Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self._limit = None

    def select(self, *_):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, *_a, **_k):
        return self

    def execute(self):
        rows = [dict(r) for r in self.db.tables.get(self.name, [])]
        for key, value in self.filters:
            rows = [r for r in rows if r.get(key) == value]
        if self._limit is not None:
            rows = rows[: self._limit]
        return type("R", (), {"data": rows})()


class _Database:
    def __init__(self, candidates):
        self.tables = {adapter.CANDIDATE_TABLE: list(candidates)}
        self.writes: list[tuple] = []

    def table(self, name):
        if name == adapter.CANDIDATE_TABLE:
            return _Query(self, name)
        self.writes.append(("table", name))
        raise AssertionError(f"unexpected table access: {name}")


def _candidate_row(**overrides) -> dict:
    row = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row["calibrator_payload"] = _empirical_calibrator()
    row["research_screen_pass"] = True
    row["source_review_status"] = "PASS"
    row["lifecycle_state"] = "CANDIDATE"
    row["promoted"] = False
    row["active"] = False
    row["probability_publishable"] = False
    row["can_execute"] = False
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


def test_adapter_rejects_incompatible_settlement_basis():
    receipt = parse_certification(_receipt_payload())
    with pytest.raises(adapter.NHLCertifiedScoringError) as exc:
        adapter.build_nhl_certified_envelope(
            receipt=receipt,
            raw_home=0.58,
            calibrated_home=0.56,
            lower=0.51,
            upper=0.61,
            evidence=_evidence(settlement_basis="REGULATION_ONLY"),
            model_timestamp="2026-11-22T12:00:00+00:00",
            latest_material_update_timestamp="2026-11-22T11:00:00+00:00",
            home_team="BOS",
            away_team="NYR",
            official_event_id="2026020001",
            now_utc=NOW,
        )
    assert "NHL_SETTLEMENT_BASIS_UNVERIFIED" in exc.value.blockers


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
    assert package["calibration_artifact_fingerprint"] == _hash(_empirical_calibrator())
    assert package["calibration_artifact_fingerprint"] != package.get("model_artifact_checksum")
    assert package["settlement_basis"] == "HOME_AWAY_INCLUDING_OT_SO"
    assert package["outcome_space"] == "HOME_AWAY_INCLUDING_OT_SO"
    assert package["calibrated_away_lower_bound"] == pytest.approx(0.39)
    assert package["calibrated_away_upper_bound"] == pytest.approx(0.49)
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


def test_load_candidate_matching_receipt_binds_exact_artifact_and_calibrator():
    receipt = parse_certification(_receipt_payload())
    db = _Database([_candidate_row()])
    row = adapter.load_candidate_matching_receipt(db, receipt)
    assert row["artifact_checksum"] == receipt.artifact_sha256
    assert row["model_artifact_version"] == receipt.model_version
    assert _hash(row["calibrator_payload"]) == receipt.calibration_sha256

    bad = _Database([_candidate_row(artifact_checksum="0" * 64)])
    with pytest.raises(adapter.NHLCertifiedScoringError) as exc:
        adapter.load_candidate_matching_receipt(bad, receipt)
    assert "NHL_CERTIFIED_CANDIDATE_NOT_FOUND" in exc.value.blockers


def test_score_matching_uses_receipt_bound_loader_without_replacing_helper(monkeypatch):
    """Exercise the real helper; only stub the heavy current-event scorer."""
    receipt = parse_certification(_receipt_payload())
    candidate = _candidate_row()
    db = _Database([candidate])
    seen: dict[str, object] = {}

    def fake_score(req, *, db, now_utc=None):
        loaded = nhl.load_nhl_fitted_candidate(db, as_of_utc=now_utc)
        seen["model_version"] = loaded.model_version
        seen["artifact_checksum"] = loaded.artifact_checksum
        return {
            "raw_home_probability": 0.58,
            "calibrated_home_probability": 0.56,
            "calibrated_home_lower_bound": 0.51,
            "calibrated_home_upper_bound": 0.61,
            "immutable_model_timestamp": "2026-11-22T12:00:00+00:00",
            "home_team": "BOS",
            "away_team": "NYR",
            "official_event_id": "2026020001",
            "model_artifact_checksum": receipt.artifact_sha256,
        }

    monkeypatch.setattr(nhl, "score_current_nhl_event", fake_score)
    req = SimpleNamespace(
        sport_specific_evidence=_evidence(),
        home_team="BOS",
        away_team="NYR",
        event_start_time_utc="2026-11-22T20:00:00+00:00",
        settlement_basis="HOME_AWAY_INCLUDING_OT_SO",
    )
    scored = adapter._score_matching_certified_artifacts(
        db=db,
        req=req,
        receipt=receipt,
        evidence=_evidence(),
        now_utc=NOW,
    )
    assert seen["model_version"] == receipt.model_version
    assert seen["artifact_checksum"] == receipt.artifact_sha256
    assert scored["raw_home"] == pytest.approx(0.58)
    assert scored["lower"] == pytest.approx(0.51)


def test_adapter_end_to_end_does_not_mutate_candidate_rows(monkeypatch):
    receipt = parse_certification(_receipt_payload())
    candidate = _candidate_row()
    db = _Database([candidate])
    before = deepcopy(candidate)

    monkeypatch.setattr(adapter, "resolve_certification", lambda *_a, **_k: receipt)

    def fake_score(req, *, db, now_utc=None):
        loaded = nhl.load_nhl_fitted_candidate(db, as_of_utc=now_utc)
        assert loaded.artifact_checksum == receipt.artifact_sha256
        return {
            "raw_home_probability": 0.58,
            "calibrated_home_probability": 0.56,
            "calibrated_home_lower_bound": 0.51,
            "calibrated_home_upper_bound": 0.61,
            "immutable_model_timestamp": "2026-11-22T12:00:00+00:00",
            "home_team": "BOS",
            "away_team": "NYR",
            "official_event_id": "2026020001",
            "model_artifact_checksum": receipt.artifact_sha256,
        }

    monkeypatch.setattr(nhl, "score_current_nhl_event", fake_score)
    req = SimpleNamespace(
        sport_specific_evidence=_evidence(),
        home_team="BOS",
        away_team="NYR",
        event_start_time_utc="2026-11-22T20:00:00+00:00",
        event_key="NHL:2026020001",
        settlement_basis="HOME_AWAY_INCLUDING_OT_SO",
    )
    package = adapter.score_nhl_certified_event(req, db=db, now_utc=NOW)
    assert package["probability_publishable"] is True
    assert package["can_execute"] is False
    assert package["settlement_basis"] == "HOME_AWAY_INCLUDING_OT_SO"
    assert db.tables[adapter.CANDIDATE_TABLE][0] == before
    assert db.tables[adapter.CANDIDATE_TABLE][0]["probability_publishable"] is False
