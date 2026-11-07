"""Fitted NHL specialist: registry provenance, chronological replay, typed holds."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import v17.nhl_fitted_specialist as nhl
from v17.nhl_provenance import SOURCE_PAYLOAD_HASH_SCOPE, provenance_envelope_hash
from team_event_request_runtime import TeamEventRequestRow, _completed_registered

FIXTURE = Path(__file__).parent / "fixtures" / "nhl_fitted_candidate_20261107.json"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
SEASON = "20262027"


def _candidate_row() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _Query:
    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]
        self.filters: list[tuple[str, object]] = []
        self.order_field: str | None = None
        self.desc = False
        self.limit_n: int | None = None
        self.range_: tuple[int, int] | None = None

    def select(self, *args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, field, desc=False):
        self.order_field = field
        self.desc = desc
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def range(self, start, end):
        self.range_ = (start, end)
        return self

    def execute(self):
        rows = [
            row
            for row in self.rows
            if all(str(row.get(key)) == str(value) for key, value in self.filters)
        ]
        if self.order_field:
            rows.sort(key=lambda row: str(row.get(self.order_field)), reverse=self.desc)
        if self.range_:
            rows = rows[self.range_[0] : self.range_[1] + 1]
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        return SimpleNamespace(data=rows)


class _DB:
    def __init__(self, *, candidates=None, events=None):
        self.tables = {
            nhl.CANDIDATE_TABLE: candidates if candidates is not None else [_candidate_row()],
            nhl.SOURCE_TABLE: events if events is not None else _events(),
        }

    def table(self, name):
        return _Query(self.tables.get(name, []))


def _event(index: int, home: str, away: str, home_score: int, away_score: int) -> dict:
    start = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=index)
    event_id = f"202602{index:04d}"
    outcome = {
        "home_score": home_score,
        "away_score": away_score,
        "positive_outcome": home_score > away_score,
        "game_type": 2,
        "neutral_site": False,
        "game_state": "FINAL",
        "provenance_version": nhl.SOURCE_PROVENANCE_VERSION,
        "source_payload_commitment": hashlib.sha256(
            f"nhl-raw-{index}".encode()
        ).hexdigest(),
        "payload_hash_scope": SOURCE_PAYLOAD_HASH_SCOPE,
    }
    envelope = provenance_envelope_hash(
        official_event_id=event_id,
        season=SEASON,
        event_start_time=start.isoformat(),
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        game_type=2,
        game_state="FINAL",
        neutral_site=False,
    )
    return {
        "sport": "NHL",
        "official_event_id": event_id,
        "season": SEASON,
        "event_start_time": start.isoformat(),
        "home_participant": home,
        "away_participant": away,
        "outcome_json": outcome,
        "source_provider": "NHL_PUBLIC_WEB_API",
        "source_uri": f"https://api-web.nhle.com/v1/club-schedule-season/{home}/20262027",
        "source_payload_sha256": envelope,
    }


def _events() -> list[dict]:
    events: list[dict] = []
    for index in range(1, 11):
        if index % 2:
            events.append(_event(index, "BOS", "TOR", 3, 2))
        else:
            events.append(_event(index, "NYR", "MTL", 2, 3))
    return events


def _legacy_event(event: dict) -> dict:
    legacy = dict(event)
    legacy["source_payload_sha256"] = "a" * 64
    legacy["outcome_json"] = {
        "home_score": event["outcome_json"]["home_score"],
        "away_score": event["outcome_json"]["away_score"],
        "positive_outcome": event["outcome_json"]["positive_outcome"],
    }
    return legacy


def _reconstruct(events: list[dict]):
    return nhl.reconstruct_nhl_pregame_features(
        events,
        official_event_id="2026029001",
        event_start_time=START.isoformat(),
        home_team="BOS",
        away_team="NYR",
        season=SEASON,
    )


def _request(**evidence_overrides):
    evidence = {
        "official_event_id": "2026029001",
        "settlement_basis": nhl.SETTLEMENT_CONTRACT,
        "goalie_status": "CONFIRMED",
        "goalie_status_observed_at": (NOW - timedelta(minutes=10)).isoformat(),
        "goalie_identity_snapshot_id": "goalie-snapshot-20261119",
        "injury_report": {"home": [], "away": []},
        "rest_travel": {"home_days": 2, "away_days": 3},
        "source_snapshot_id": "nhl-source-snapshot-20261119",
        "season": SEASON,
        "neutral_site": False,
    }
    evidence.update(evidence_overrides)
    return SimpleNamespace(
        event_key="NHL:2026029001",
        official_event_id="2026029001",
        event_start_time_utc=START.isoformat(),
        home_team="BOS",
        away_team="NYR",
        sport="NHL",
        league="NHL",
        sport_specific_evidence=evidence,
    )


def _reconstruction(db=None):
    return nhl.reconstruct_nhl_pregame_features(
        (db or _DB()).table(nhl.SOURCE_TABLE).execute().data,
        official_event_id="2026029001",
        event_start_time=START.isoformat(),
        home_team="BOS",
        away_team="NYR",
        season=SEASON,
    )


def test_feature_schema_matches_training_pipeline():
    import nhl_candidate_pipeline as pipeline

    assert nhl.FEATURE_NAMES == pipeline.FEATURE_NAMES
    assert nhl.MODEL_FAMILY == pipeline.MODEL_FAMILY
    assert nhl.FEATURE_SCHEMA_VERSION == pipeline.FEATURE_SCHEMA_VERSION
    assert nhl.SOURCE_POLICY_ID == pipeline.SOURCE_ID
    assert nhl.SOURCE_PROVENANCE_VERSION == pipeline.SOURCE_PROVENANCE_VERSION


def test_real_governed_candidate_checksum_is_reproduced():
    row = _candidate_row()
    assert nhl._sha256(row["artifact_payload"]) == row["artifact_checksum"]


def test_registry_loader_accepts_exact_candidate_and_withholds_publication():
    candidate = nhl.load_nhl_fitted_candidate(_DB(), as_of_utc=NOW)
    assert candidate.model_version == "NHL_RS_LOGISTIC_V1-5528ab8891df79fd-372bef19a04a"
    assert candidate.dataset_hash.startswith("5528ab8891df79fd")
    assert candidate.publication_capable is False
    assert candidate.selected_calibrator_method == nhl.IDENTITY_CALIBRATION_METHOD
    assert candidate.has_calibrated_bounds is False


def test_registry_loader_rejects_tampered_artifact():
    row = _candidate_row()
    row["artifact_payload"]["coefficients"][0] = 0.99
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.load_nhl_fitted_candidate(_DB(candidates=[row]), as_of_utc=NOW)
    assert "NHL_FITTED_ARTIFACT_CHECKSUM_MISMATCH" in caught.value.blockers


def test_registry_loader_rejects_promoted_or_wrong_schema_rows():
    promoted = _candidate_row()
    promoted["promoted"] = True
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.load_nhl_fitted_candidate(_DB(candidates=[promoted]), as_of_utc=NOW)
    assert "NHL_FITTED_ARTIFACT_UNAVAILABLE" in caught.value.blockers

    wrong_schema = _candidate_row()
    wrong_schema["feature_schema_version"] = "NHL_DRIFTED_FEATURES_V2"
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.load_nhl_fitted_candidate(_DB(candidates=[wrong_schema]), as_of_utc=NOW)
    assert "NHL_FITTED_ARTIFACT_FEATURE_SCHEMA_MISMATCH" in caught.value.blockers


def test_identity_calibrator_scores_deterministically_and_withholds_bounds():
    candidate = nhl.load_nhl_fitted_candidate(_DB(), as_of_utc=NOW)
    reconstruction = _reconstruction()
    first = nhl.score_nhl_fitted_candidate(
        candidate, reconstruction, event_key="NHL:2026029001", evidence={}
    )
    second = nhl.score_nhl_fitted_candidate(
        candidate, reconstruction, event_key="NHL:2026029001", evidence={}
    )
    assert first["raw_home_probability"] == second["raw_home_probability"]
    assert 0.0 < first["calibrated_probability"] < 1.0
    assert first["selected_participant"] in {"BOS", "NYR"}
    assert first["calibrated_lower_bound"] is None
    assert first["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert "NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE" in first["blockers"]
    assert first["probability_publishable"] is False
    assert first["rank_eligible"] is False
    assert first["can_execute"] is False
    assert first["model_version"] == candidate.model_version
    assert first["feature_reconstruction"]["timeline_hash"] == reconstruction.timeline_hash


def test_empirical_calibrator_bounds_contain_the_selected_probability():
    candidate = nhl.load_nhl_fitted_candidate(_DB(), as_of_utc=NOW)
    calibrator = {
        "method": nhl.EMPIRICAL_CALIBRATION_METHOD,
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
    empirical = replace(candidate, calibrator=calibrator)
    package = nhl.score_nhl_fitted_candidate(
        empirical, _reconstruction(), event_key="NHL:2026029001", evidence={}
    )
    assert package["calibrated_lower_bound"] is not None
    assert package["calibrated_upper_bound"] is not None
    assert package["calibrated_lower_bound"] <= package["calibrated_probability"]
    assert package["calibrated_probability"] <= package["calibrated_upper_bound"]
    assert package["blockers"] == []
    assert package["code"] == "SPORTING_PROBABILITY_SCORED_CANDIDATE"
    assert package["probability_publishable"] is False
    assert package["can_execute"] is False


def test_feature_reconstruction_requires_minimum_prior_games():
    events = [_event(1, "BOS", "TOR", 3, 2), _event(2, "NYR", "MTL", 2, 3)]
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            events,
            official_event_id="2026029001",
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=SEASON,
        )
    assert "NHL_PRIOR_GAMES_INSUFFICIENT" in caught.value.blockers


def test_feature_reconstruction_rejects_already_settled_target():
    events = _events() + [_event(15, "BOS", "NYR", 4, 1)]
    target = events[-1]["official_event_id"]
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            events,
            official_event_id=target,
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=SEASON,
        )
    assert "NHL_EVENT_ALREADY_SETTLED" in caught.value.blockers


def test_feature_reconstruction_rejects_conflicting_duplicate_identity():
    events = _events()
    duplicate = dict(events[0])
    duplicate["outcome_json"] = {
        **events[0]["outcome_json"],
        "home_score": 1,
        "away_score": 5,
        "positive_outcome": False,
    }
    duplicate["source_payload_sha256"] = provenance_envelope_hash(
        official_event_id=duplicate["official_event_id"],
        season=SEASON,
        event_start_time=duplicate["event_start_time"],
        home_team=duplicate["home_participant"],
        away_team=duplicate["away_participant"],
        home_score=1,
        away_score=5,
        game_type=2,
        game_state="FINAL",
        neutral_site=False,
    )
    events.append(duplicate)
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            events,
            official_event_id="2026029001",
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=SEASON,
        )
    assert "NHL_SOURCE_EVENT_IDENTITY_CONFLICT" in caught.value.blockers


def test_legacy_source_rows_without_provenance_cannot_be_replayed():
    events = _events()
    events[0]["outcome_json"].pop("provenance_version")
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            events,
            official_event_id="2026029001",
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=SEASON,
        )
    assert "NHL_SOURCE_PROVENANCE_UNVERIFIED" in caught.value.blockers


def test_replay_prefers_a_v2_sibling_over_a_legacy_duplicate():
    events = _events()
    legacy = dict(events[0])
    legacy["source_payload_sha256"] = hashlib.sha256(b"legacy-sibling").hexdigest()
    legacy["outcome_json"] = {
        "home_score": events[0]["outcome_json"]["home_score"],
        "away_score": events[0]["outcome_json"]["away_score"],
        "positive_outcome": events[0]["outcome_json"]["positive_outcome"],
    }
    events.append(legacy)
    reconstruction = nhl.reconstruct_nhl_pregame_features(
        events,
        official_event_id="2026029001",
        event_start_time=START.isoformat(),
        home_team="BOS",
        away_team="NYR",
        season=SEASON,
    )
    assert reconstruction.home_prior_games == 5
    assert reconstruction.away_prior_games == 5


def test_v2_row_supersedes_a_legacy_sibling_for_the_same_event():
    events = _events()
    events.append(_legacy_event(events[0]))
    reconstruction = _reconstruct(events)
    assert reconstruction.prior_event_count == 10


def test_legacy_only_event_remains_fail_closed():
    events = _events()
    events[0] = _legacy_event(events[0])
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        _reconstruct(events)
    assert "NHL_SOURCE_PROVENANCE_UNVERIFIED" in caught.value.blockers


def test_replay_rejects_a_tampered_event_envelope():
    events = _events()
    events[0]["outcome_json"]["home_score"] += 1
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            events,
            official_event_id="2026029001",
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=SEASON,
        )
    assert "NHL_SOURCE_PAYLOAD_HASH_MISMATCH" in caught.value.blockers


def test_replay_rejects_a_tampered_event_identity():
    events = _events()
    events[0]["home_participant"] = "SEA"
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        _reconstruct(events)
    assert "NHL_SOURCE_PAYLOAD_HASH_MISMATCH" in caught.value.blockers


def test_reconstruction_requires_explicit_neutral_site_boolean():
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            _events(),
            official_event_id="2026029001",
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=SEASON,
            neutral_site="unknown",
        )
    assert "NHL_NEUTRAL_SITE_UNVERIFIED" in caught.value.blockers


def test_current_event_requires_confirmed_fresh_goalie_evidence():
    request = _request(goalie_status="EXPECTED")
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.score_current_nhl_event(request, db=_DB(), now_utc=NOW)
    assert "NHL_STARTING_GOALIE_NOT_CONFIRMED" in caught.value.blockers

    stale = _request(goalie_status_observed_at=(NOW - timedelta(hours=3)).isoformat())
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.score_current_nhl_event(stale, db=_DB(), now_utc=NOW)
    assert "NHL_GOALIE_STATUS_STALE" in caught.value.blockers


def test_current_event_holds_when_registry_provider_is_absent(monkeypatch):
    monkeypatch.setattr(nhl, "_REGISTRY_DB_PROVIDER", None)
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.score_current_nhl_event(_request(), now_utc=NOW)
    assert caught.value.code == "MODEL_UNAVAILABLE"
    assert "NHL_FITTED_REGISTRY_UNAVAILABLE" in caught.value.blockers


def test_current_event_scores_from_registry_and_ignores_caller_calibration():
    caller_artifact = {
        "artifact_type": "BINARY_PLATT_CALIBRATOR",
        "sport": "NHL",
        "platt_a": 9.0,
        "platt_b": 9.0,
        "model_family": "CALLER_SUPPLIED",
    }
    request = _request(calibration_artifact=caller_artifact, neutral_site=False)
    package = nhl.score_current_nhl_event(request, db=_DB(), now_utc=NOW)
    assert package["model_version"] == "NHL_RS_LOGISTIC_V1-5528ab8891df79fd-372bef19a04a"
    assert package["calibration_method"] == nhl.IDENTITY_CALIBRATION_METHOD
    assert package["official_event_id"] == "2026029001"
    assert package["fitted_registry_scoring"] is True
    assert package["probability_publishable"] is False
    assert package["can_execute"] is False
    assert package["feature_reconstruction"]["home_prior_games"] == 5
    assert package["feature_reconstruction"]["away_prior_games"] == 5


def test_batch_boundary_admits_candidate_receipt_without_publication():
    candidate = nhl.load_nhl_fitted_candidate(_DB(), as_of_utc=NOW)
    calibrator = {
        "method": nhl.EMPIRICAL_CALIBRATION_METHOD,
        "training_n": 1226,
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
    package = nhl.score_nhl_fitted_candidate(
        replace(candidate, calibrator=calibrator),
        _reconstruction(),
        event_key="NHL:2026029001",
        evidence={},
    )
    row = TeamEventRequestRow(
        research_run_id="nhl-fitted-boundary",
        objective_lane="OUTRIGHT_WIN_PROBABILITY",
        sport="NHL",
        league="NHL",
        event_key="NHL:2026029001",
        event_state="PREGAME",
        event_date="2026-11-20",
        timezone="America/Chicago",
        price_required_for_objective=False,
        event_start_time_utc=START.isoformat(),
        home_team="BOS",
        away_team="NYR",
        settlement_basis=nhl.SETTLEMENT_CONTRACT,
    )
    outcome = _completed_registered(row, package)
    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["selected_team"] in {"BOS", "NYR"}
    assert outcome["probability_publishable"] is False
    assert outcome["objective_rank_eligible"] is False
    assert outcome["can_execute"] is False


def test_batch_boundary_resolves_full_club_names_to_the_selected_side():
    candidate = nhl.load_nhl_fitted_candidate(_DB(), as_of_utc=NOW)
    calibrator = {
        "method": nhl.EMPIRICAL_CALIBRATION_METHOD,
        "training_n": 1226,
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
    package = nhl.score_nhl_fitted_candidate(
        replace(candidate, calibrator=calibrator),
        _reconstruction(),
        event_key="NHL:2026029001",
        evidence={},
    )
    row = TeamEventRequestRow(
        research_run_id="nhl-fitted-alias",
        objective_lane="OUTRIGHT_WIN_PROBABILITY",
        sport="NHL",
        league="NHL",
        event_key="NHL:2026029001",
        event_state="PREGAME",
        event_date="2026-11-20",
        timezone="America/Chicago",
        price_required_for_objective=False,
        event_start_time_utc=START.isoformat(),
        home_team="Boston Bruins",
        away_team="New York Rangers",
        settlement_basis=nhl.SETTLEMENT_CONTRACT,
    )
    outcome = _completed_registered(row, package)
    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["selected_team"] == package["selected_participant"]
    assert outcome["can_execute"] is False

    package["selected_participant"] = "Montreal Canadiens"
    held = _completed_registered(row, package)
    assert held["terminal_status"] == "HELD"
    assert "TEAM_EVENT_SELECTED_PARTICIPANT_IDENTITY_INVALID" in held["blockers"]


def test_bridge_preserves_fitted_specialist_typed_blockers(monkeypatch):
    from fastapi import HTTPException

    import v17.multisport_team_event_bridges as bridges
    import v17.team_event_request_runtime as host_routing

    monkeypatch.setattr(
        host_routing,
        "resolve_host_route",
        lambda *_args: SimpleNamespace(
            requester_host_identity="test-requester-host",
            controlling_engine_identity="V17_TEAM_EVENT_BRIDGE",
            candidate_family="NHL",
        ),
    )

    def _blocked(_req):
        raise nhl.NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_PRIOR_GAMES_INSUFFICIENT"]
        )

    monkeypatch.setitem(bridges.MODEL_SCORERS, "NHL", _blocked)
    monkeypatch.setattr(bridges, "_route_contract", lambda _req: SimpleNamespace())
    monkeypatch.setattr(
        bridges.base_runtime, "_run_mandatory_scout_research", lambda _req: {}
    )
    request = SimpleNamespace(
        sport="NHL",
        league="NHL",
        event_key="NHL:2026029001",
        requester_host_identity="test-requester-host",
        candidate_family="NHL",
    )
    with pytest.raises(HTTPException) as caught:
        bridges._score_one("NHL", request, event_api=None)
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert "NHL_PRIOR_GAMES_INSUFFICIENT" in caught.value.detail["blockers"]
    assert caught.value.detail["can_execute"] is False


def test_nhl_bridge_registration_uses_the_fitted_server_scorer():
    from v17.multisport_team_event_bridges import (
        BRIDGE_SCORERS,
        SPORT_MODEL_INPUTS,
        score_nhl_fitted_team_event,
    )

    assert BRIDGE_SCORERS["NHL"] is score_nhl_fitted_team_event
    assert "calibration_artifact" not in SPORT_MODEL_INPUTS["NHL"]
    assert "goalie_identity_snapshot_id" in SPORT_MODEL_INPUTS["NHL"]
