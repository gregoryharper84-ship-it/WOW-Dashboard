"""Fitted NHL specialist: registry provenance, chronological replay, typed holds."""
# Replacement PR #2569 unique tip for Actions enqueue.
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


def _timeline_receipt(
    events: list[dict],
    *,
    home: str = "BOS",
    away: str = "NYR",
    target_start: datetime = START,
    season: str = SEASON,
) -> dict:
    """Test-authority full-season prior receipt for a synthetic timeline."""
    del home, away  # retained for call-site compatibility
    prior_ids: set[str] = set()
    for event in events:
        start = datetime.fromisoformat(str(event["event_start_time"]).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start >= target_start:
            continue
        prior_ids.add(str(event["official_event_id"]))
    return {
        "provenance_complete": True,
        "season": str(season),
        "settled_prior_event_ids": sorted(prior_ids),
    }


def _reconstruct(events: list[dict], **kwargs):
    params = {
        "official_event_id": "2026029001",
        "event_start_time": START.isoformat(),
        "home_team": "BOS",
        "away_team": "NYR",
        "season": SEASON,
        "source_timeline_receipt": kwargs.get(
            "source_timeline_receipt", _timeline_receipt(events)
        ),
    }
    params.update(kwargs)
    return nhl.reconstruct_nhl_pregame_features(events, **params)


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
    events = (db or _DB()).table(nhl.SOURCE_TABLE).execute().data
    return nhl.reconstruct_nhl_pregame_features(
        events,
        official_event_id="2026029001",
        event_start_time=START.isoformat(),
        home_team="BOS",
        away_team="NYR",
        season=SEASON,
        source_timeline_receipt=_timeline_receipt(events),
    )



@pytest.fixture(autouse=True)
def _install_test_timeline_receipt_provider():
    """Score path must not trust caller evidence; use a synthetic server seam."""
    def _provider(*, season, event_start_time, home_team="", away_team="", session=None):
        return _timeline_receipt(
            _events(),
            home=str(home_team or "BOS"),
            away=str(away_team or "NYR"),
            target_start=event_start_time if isinstance(event_start_time, datetime) else START,
            season=str(season),
        )
    nhl.install_nhl_timeline_receipt_provider(_provider)
    yield
    nhl.reset_nhl_timeline_receipt_provider()


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


def test_registry_loader_skips_malformed_newest_candidate_and_falls_back():
    malformed = _candidate_row()
    malformed["training_rows"] = "3676"
    malformed["created_at"] = "2026-11-18T00:00:00+00:00"
    valid = _candidate_row()
    valid["created_at"] = "2026-11-17T00:00:00+00:00"

    candidate = nhl.load_nhl_fitted_candidate(
        _DB(candidates=[malformed, valid]), as_of_utc=NOW
    )

    assert candidate.model_version == "NHL_RS_LOGISTIC_V1-5528ab8891df79fd-372bef19a04a"


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
    # Generic calibrated_* stay home-oriented; selected_* track the winner side.
    assert package["calibrated_probability"] == package["calibrated_home_probability"]
    assert package["calibrated_lower_bound"] == package["calibrated_home_lower_bound"]
    assert package["calibrated_upper_bound"] == package["calibrated_home_upper_bound"]
    assert (
        package["selected_calibrated_lower_bound"]
        <= package["selected_calibrated_probability"]
        <= package["selected_calibrated_upper_bound"]
    )
    assert package["blockers"] == []
    assert package["code"] == "SPORTING_PROBABILITY_SCORED_CANDIDATE"
    assert package["probability_publishable"] is False
    assert package["can_execute"] is False


def test_generic_calibrated_fields_remain_home_oriented_for_away_selection():
    candidate = nhl.load_nhl_fitted_candidate(_DB(), as_of_utc=NOW)
    calibrator = {
        "method": nhl.EMPIRICAL_CALIBRATION_METHOD,
        "training_n": 1226,
        "selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
        "bins": [
            {
                "raw_mean": 0.20,
                "calibrated_probability": 0.20,
                "wilson_lower": 0.10,
                "wilson_upper": 0.30,
            }
        ],
    }
    # Force a low home lower-bound so the selected participant is away.
    empirical = replace(candidate, calibrator=calibrator)
    package = nhl.score_nhl_fitted_candidate(
        empirical, _reconstruction(), event_key="NHL:2026029001", evidence={}
    )
    assert package["selected_participant"] == "NYR"
    assert package["calibrated_probability"] == package["calibrated_home_probability"]
    assert package["selected_calibrated_probability"] == package["calibrated_away_probability"]
    outcome = _completed_registered(
        TeamEventRequestRow(
            research_run_id="run",
            objective_lane="OUTRIGHT_WIN_PROBABILITY",
            sport="NHL",
            league="NHL",
            event_key="NHL:2026029001",
            event_state="PREGAME",
            event_date="2026-11-19",
            timezone="America/Chicago",
            price_required_for_objective=False,
            home_team="BOS",
            away_team="NYR",
        ),
        package,
    )
    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["calibrated_probability"] == package["calibrated_away_probability"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda receipt: None,
        lambda receipt: {**receipt, "provenance_complete": False},
        lambda receipt: {**receipt, "season": "20252026"},
        lambda receipt: {k: v for k, v in receipt.items() if k != "settled_prior_event_ids"},
        lambda receipt: {**receipt, "settled_prior_event_ids": "not-a-list"},
        lambda receipt: {
            **receipt,
            "settled_prior_event_ids": list(receipt["settled_prior_event_ids"]) + ["2026029999"],
        },
        lambda receipt: {
            **receipt,
            "settled_prior_event_ids": list(receipt["settled_prior_event_ids"])[:-1],
        },
    ],
)
def test_missing_or_mismatched_timeline_receipt_holds_incomplete(mutate):
    events = _events()
    baseline = _timeline_receipt(events)
    receipt = mutate(baseline)
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            events,
            official_event_id="2026029001",
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=SEASON,
            **({"source_timeline_receipt": receipt} if receipt is not None else {}),
        )
    assert "NHL_SOURCE_TIMELINE_INCOMPLETE" in caught.value.blockers


def test_build_official_season_timeline_receipt_uses_default_session_and_filters_by_start(monkeypatch):
    from types import SimpleNamespace

    captured: dict[str, object] = {}

    def _fake_fetch(start_years, *, teams=None, session=None, strict=False):
        captured["session"] = session
        captured["teams"] = teams
        captured["strict"] = strict
        return [
            SimpleNamespace(
                game_id="2026020001",
                event_start_time=(START - timedelta(days=2)).isoformat(),
                game_type=2,
                home_team="BOS",
                away_team="TOR",
            ),
            SimpleNamespace(
                game_id="2026020002",
                event_start_time=START.isoformat(),
                game_type=2,
                home_team="NYR",
                away_team="MTL",
            ),
            SimpleNamespace(
                game_id="2026020003",
                event_start_time=(START + timedelta(days=1)).isoformat(),
                game_type=2,
                home_team="BOS",
                away_team="NYR",
            ),
        ]

    monkeypatch.setattr("nhl_candidate_pipeline.fetch_historical_games", _fake_fetch)
    receipt = nhl.build_official_season_timeline_receipt(
        season=int(SEASON),
        event_start_time=START,
    )
    assert captured["session"] is None  # default session path (kwargs omitted)
    assert captured["teams"] is None  # full season
    assert captured["strict"] is True
    assert receipt["settled_prior_event_ids"] == ["2026020001"]
    assert receipt["provenance_complete"] is True

    def _boom(*_args, **_kwargs):
        from nhl_candidate_pipeline import NHLCandidateError
        raise NHLCandidateError("NHL_SOURCE_TRANSPORT_FAILED", "boom")

    monkeypatch.setattr("nhl_candidate_pipeline.fetch_historical_games", _boom)
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.build_official_season_timeline_receipt(season=int(SEASON), event_start_time=START)
    assert "NHL_SOURCE_TIMELINE_INCOMPLETE" in caught.value.blockers


def test_score_current_ignores_caller_supplied_timeline_receipt(monkeypatch):
    # Poisoned caller evidence must not authorize an incomplete stored timeline.
    poisoned = {
        "provenance_complete": True,
        "season": SEASON,
        "settled_prior_event_ids": [],
    }
    request = _request(source_timeline_receipt=poisoned)
    package = nhl.score_current_nhl_event(request, db=_DB(), now_utc=NOW)
    assert package["can_execute"] is False
    assert "NHL_SOURCE_TIMELINE_INCOMPLETE" not in package.get("blockers", [])


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
            source_timeline_receipt=_timeline_receipt(events),
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
            source_timeline_receipt=_timeline_receipt(events),
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
            source_timeline_receipt=_timeline_receipt(events),
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
            source_timeline_receipt=_timeline_receipt(events),
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
        source_timeline_receipt=_timeline_receipt(events),
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
            source_timeline_receipt=_timeline_receipt(events),
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
            source_timeline_receipt=_timeline_receipt(_events()),
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


def test_games_after_target_start_never_leak_into_features():
    baseline = _reconstruct(_events())
    future = _event(60, "BOS", "NYR", 9, 0)  # starts after START
    leaked = _reconstruct(_events() + [future])
    assert dict(leaked.features) == dict(baseline.features)
    assert leaked.prior_event_count == baseline.prior_event_count
    assert leaked.timeline_hash == baseline.timeline_hash


def test_request_regulation_settlement_mismatches_full_game_evidence():
    request = _request()
    request.settlement_basis = "REGULATION_3_WAY"
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.score_current_nhl_event(request, db=_DB(), now_utc=NOW)
    assert caught.value.code == "MODEL_INPUTS_INSUFFICIENT"
    assert "NHL_SETTLEMENT_BASIS_MISMATCH" in caught.value.blockers


def test_current_event_rejects_mismatched_or_malformed_season_evidence():
    for bad_season in ("20252026", "not-a-season", "²", "٢٠٢٥٢٠٢٦", 20252026, True):
        with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
            nhl.score_current_nhl_event(
                _request(season=bad_season), db=_DB(), now_utc=NOW
            )
        assert caught.value.code == "MODEL_INPUTS_INSUFFICIENT"
        assert "NHL_SEASON_IDENTITY_INVALID" in caught.value.blockers


@pytest.mark.parametrize("bad_season", ["20252026", "not-a-season", "²", "٢٠٢٥٢٠٢٦"])
def test_reconstruction_rejects_mismatched_or_malformed_season_evidence(bad_season):
    events = _DB().table(nhl.SOURCE_TABLE).execute().data
    with pytest.raises(nhl.NHLFittedSpecialistError) as caught:
        nhl.reconstruct_nhl_pregame_features(
            events,
            official_event_id="2026029001",
            event_start_time=START.isoformat(),
            home_team="BOS",
            away_team="NYR",
            season=bad_season,
            source_timeline_receipt=_timeline_receipt(events),
        )
    assert caught.value.code == "MODEL_INPUTS_INSUFFICIENT"
    assert "NHL_SEASON_IDENTITY_INVALID" in caught.value.blockers


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


def test_fitted_http_failure_maps_model_unavailable_to_bridge_409(monkeypatch):
    """Align NHL fitted MODEL_UNAVAILABLE with shared bridge 409 semantics."""
    from fastapi import HTTPException

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
    request = SimpleNamespace(
        sport="NHL",
        league="NHL",
        event_key="NHL:2026029001",
        requester_host_identity="test-requester-host",
        candidate_family="NHL",
    )
    failure = nhl._fitted_http_failure(
        request,
        nhl.NHLFittedSpecialistError(
            "MODEL_UNAVAILABLE", ["NHL_FITTED_REGISTRY_UNAVAILABLE"]
        ),
    )
    assert isinstance(failure, HTTPException)
    assert failure.status_code == 409
    assert failure.detail["code"] == "MODEL_UNAVAILABLE"
    assert "NHL_FITTED_REGISTRY_UNAVAILABLE" in failure.detail["blockers"]


def test_score_one_preserves_fitted_http_goalie_stale_hold(monkeypatch):
    """Production MODEL_SCORERS['NHL'] emits HTTPException; keep 422 + blockers.

    Regression for the wrapper path that previously collapsed typed NHL holds
    into MODEL_SCORER_FAILED.
    """
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
    monkeypatch.setattr(bridges, "_route_contract", lambda _req: SimpleNamespace())
    monkeypatch.setattr(
        bridges.base_runtime, "_run_mandatory_scout_research", lambda _req: {}
    )

    def _blocked(_req, **_kwargs):
        raise nhl.NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_GOALIE_STATUS_STALE"]
        )

    monkeypatch.setattr(nhl, "score_current_nhl_event", _blocked)
    # Keep the production scorer entry so HTTPException is raised into _score_one.
    monkeypatch.setitem(
        bridges.MODEL_SCORERS, "NHL", nhl.score_nhl_fitted_team_event
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
    assert "NHL_GOALIE_STATUS_STALE" in caught.value.detail["blockers"]
    assert caught.value.detail["can_execute"] is False


def test_registered_nhl_scorer_translates_native_typed_failure(monkeypatch):
    from fastapi import HTTPException

    import v17.team_event_bridge_runtime as bridge_runtime
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

    def _blocked(_req, **_kwargs):
        raise nhl.NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_GOALIE_STATUS_STALE"]
        )

    monkeypatch.setattr(nhl, "score_current_nhl_event", _blocked)
    previous = bridge_runtime.TEAM_EVENT_BRIDGES.pop("NHL", None)
    try:
        bridge_runtime.register_team_event_bridge(
            "NHL",
            adapter_name="TEST_NHL_ADAPTER",
            controlling_specialist="TEST_NHL_SPECIALIST",
            scorer=nhl.score_nhl_fitted_team_event,
            required_inputs=("official_event_id",),
        )
        request = _request()
        request.requester_host_identity = "test-requester-host"
        request.candidate_family = "NHL"
        with pytest.raises(HTTPException) as caught:
            bridge_runtime.score_registered_team_event_request(
                request, event_api=object()
            )
    finally:
        bridge_runtime.TEAM_EVENT_BRIDGES.pop("NHL", None)
        if previous is not None:
            bridge_runtime.TEAM_EVENT_BRIDGES["NHL"] = previous
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert "NHL_GOALIE_STATUS_STALE" in caught.value.detail["blockers"]
    assert caught.value.detail["model_invoked"] is True
    assert caught.value.detail["can_execute"] is False


def test_nhl_bridge_registration_uses_the_fitted_server_scorer():
    from v17.multisport_team_event_bridges import (
        BRIDGE_SCORERS,
        MODEL_SCORERS,
        SPORT_MODEL_INPUTS,
        score_nhl_team_event_request,
    )
    from v17.nhl_fitted_specialist import score_nhl_fitted_team_event

    # The bridge entry point must be the governed request wrapper so NHL keeps
    # route contract, Scout/Research barrier, terminal reduction and official
    # publication guarding. The fitted specialist is the raw model scorer.
    assert BRIDGE_SCORERS["NHL"] is score_nhl_team_event_request
    assert MODEL_SCORERS["NHL"] is score_nhl_fitted_team_event
    assert "calibration_artifact" not in SPORT_MODEL_INPUTS["NHL"]
    assert "goalie_identity_snapshot_id" in SPORT_MODEL_INPUTS["NHL"]


def test_registered_nhl_path_applies_reducer_and_publication_guard(monkeypatch):
    import v17.multisport_team_event_bridges as bridges

    called: dict[str, object] = {}

    def _reduce(_req, package):
        called["reduce"] = dict(package)
        return {
            "status": "HOLD",
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
            "probability_publishable": False,
            "rank_eligible": False,
            "global_terminal_reducer": bridges.GLOBAL_TERMINAL_REDUCER,
            "blockers": ["TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"],
            "can_execute": False,
        }

    def _publication(result):
        called["publication"] = dict(result)
        return {
            "official_publication_allowed": False,
            "blockers": ["OFFICIAL_PUBLICATION_NOT_CERTIFIED"],
        }

    monkeypatch.setitem(
        bridges.MODEL_SCORERS,
        "NHL",
        lambda _req: {
            "fitted_registry_scoring": True,
            "selected_participant": "BOS",
            "raw_probability": 0.55,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
            "blockers": ["NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE"],
        },
    )
    monkeypatch.setattr(
        bridges,
        "_route_contract",
        lambda _req: SimpleNamespace(
            requester_host_identity="test-requester-host",
            candidate_family="NHL",
        ),
    )
    monkeypatch.setattr(
        bridges.base_runtime,
        "_run_mandatory_scout_research",
        lambda _req: {"status": "PASS"},
    )
    monkeypatch.setattr(bridges, "reduce_multisport_team_event", _reduce)
    monkeypatch.setattr(
        bridges, "evaluate_team_event_official_publication", _publication
    )

    request = SimpleNamespace(
        sport="NHL",
        league="NHL",
        event_key="NHL:2026029001",
        requester_host_identity="test-requester-host",
        candidate_family="NHL",
    )
    result = bridges._score_one("NHL", request, event_api=None)

    # A raw fitted package may never reach the caller without the shared
    # terminal reducer and official-publication guard being applied.
    assert "reduce" in called and "publication" in called
    assert result["llp_governance"]["status"] == "HOLD"
    assert result["official_publication_guard"]["official_publication_allowed"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert "NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE" in result["blockers"]
    assert "OFFICIAL_PUBLICATION_NOT_CERTIFIED" in result["blockers"]
