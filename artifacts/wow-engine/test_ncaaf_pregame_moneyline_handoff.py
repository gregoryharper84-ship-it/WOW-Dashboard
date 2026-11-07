from datetime import datetime, timedelta, timezone

from ncaaf_cfbd_hydrator import SourceSnapshot
from ncaaf_event_feature_handoff import NCAAFEventFeatureHandoffUnavailable
from ncaaf_feature_compiler import REQUIRED_SCOPED_KEYS
from ncaaf_pregame_moneyline_handoff import (
    classify_source_snapshot_for_pregame,
    establish_official_event_identity,
    evaluate_fitted_challenger,
    score_pregame_moneyline,
    verify_pregame_inputs,
)


START = datetime(2026, 11, 8, 20, tzinfo=timezone.utc)
NOW = START - timedelta(hours=6)


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def games(self, *, year, classification=None):
        assert year == 2026
        assert classification == "fbs"
        return type("Response", (), {"rows": self.rows})()


def _event():
    return {
        "official_event_id": "401800001",
        "event_start_time": START.isoformat(),
        "home_team": "Texas",
        "away_team": "Oklahoma",
        "can_execute": False,
    }


def _evidence(kind, scope, *, idx, timestamp=None, team=None, event_start=None):
    if team is None:
        team = {"HOME": "Texas", "AWAY": "Oklahoma"}.get(scope)
    payload = {"value": 0.8 if kind == "QB_CERTAINTY" else float(idx + 1) / 10}
    if kind == "REST_TRAVEL":
        payload = {"rest_days": 7.0}
        if scope == "AWAY":
            payload["travel_distance_miles"] = 191.0
    elif kind == "WEATHER":
        payload = {"wind_mph": 8.0, "precip_probability": 0.2, "temperature_f": 70.0}
    return {
        "evidence_id": f"ev-{idx}",
        "official_event_id": "401800001",
        "event_start_time": (event_start or START).isoformat(),
        "evidence_kind": kind,
        "scope": scope,
        "team": team,
        "source_provider": "TEST_APPROVED_PROVIDER",
        "evidence_timestamp": (timestamp or (NOW - timedelta(hours=2, seconds=idx))).isoformat(),
        "payload": payload,
        "payload_sha256": f"{idx + 1:064x}",
        "provenance_grade": "A",
        "blocker_codes": [],
        "can_execute": False,
    }


def _complete():
    return [_evidence(kind, scope, idx=i) for i, (kind, scope) in enumerate(REQUIRED_SCOPED_KEYS)]


def _snapshot(endpoint, *, retrieved, blockers=()):
    return SourceSnapshot(
        provider="CFBD",
        endpoint=endpoint,
        season=2026,
        week=10,
        requested_at=retrieved,
        retrieved_at=retrieved,
        request_params={"year": 2026, "week": 10},
        response_rows=[{"id": 1}],
        response_row_count=1,
        payload_sha256="c" * 64,
        acquisition_status="AVAILABLE",
        blocker_codes=list(blockers),
    )


def test_official_identity_rejects_post_start_and_id_mismatch():
    client = _Client([{
        "id": "401800001",
        "season": 2026,
        "startDate": START.isoformat(),
        "homeTeam": "Texas",
        "awayTeam": "Oklahoma",
    }])
    started = establish_official_event_identity(
        event_start_time=START.isoformat(),
        home_team="Texas Longhorns",
        away_team="Oklahoma Sooners",
        season=2026,
        now=START + timedelta(minutes=1),
        client=client,
    )
    assert started["code"] == "NCAAF_EVENT_NOT_PREGAME"
    assert started["model_invoked"] is False
    assert started["can_execute"] is False

    mismatched = establish_official_event_identity(
        event_start_time=START.isoformat(),
        home_team="Texas Longhorns",
        away_team="Oklahoma Sooners",
        season=2026,
        official_event_id="999",
        now=NOW.isoformat(),
        client=client,
    )
    assert mismatched["code"] == "NCAAF_EVENT_IDENTITY_MISMATCH"
    assert mismatched["probability_publishable"] is False

    resolved = establish_official_event_identity(
        event_start_time=START.isoformat(),
        home_team="Texas Longhorns",
        away_team="Oklahoma Sooners",
        season=2026,
        official_event_id="401800001",
        now=NOW.isoformat(),
        client=client,
    )
    assert resolved["status"] == "OFFICIAL_IDENTITY_RESOLVED"
    assert resolved["official_event_id"] == "401800001"
    assert resolved["season"] == 2026
    assert resolved["home_team"] == "Texas"
    assert resolved["can_execute"] is False


def test_missing_features_are_inputs_insufficient_not_model_unavailable():
    rows = [row for row in _complete() if not (row["evidence_kind"] == "QB_VALUE" and row["scope"] == "AWAY")]
    result = verify_pregame_inputs(_event(), rows, now=NOW)
    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["native_code"] == "NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE"
    assert "QB_VALUE:AWAY" in result["detail"]["detail"]
    assert result["model_invoked"] is False
    assert result["probability_publishable"] is False
    assert result["calibrated_lower_bound"] is None


def test_post_start_evidence_is_temporal_leakage():
    rows = _complete()
    rows[0] = {**rows[0], "evidence_timestamp": (START + timedelta(minutes=1)).isoformat()}
    result = verify_pregame_inputs(_event(), rows, now=START + timedelta(hours=2))
    assert result["code"] == "NCAAF_FEATURE_TEMPORAL_LEAKAGE"
    assert result["code"] != "MODEL_UNAVAILABLE"
    assert result["model_invoked"] is False


def test_future_evidence_and_team_mismatch_fail_closed():
    future = _complete()
    future[0] = {**future[0], "evidence_timestamp": (NOW + timedelta(minutes=5)).isoformat()}
    leaked = verify_pregame_inputs(_event(), future, now=NOW)
    assert leaked["code"] == "NCAAF_FEATURE_TEMPORAL_LEAKAGE"

    mismatched = _complete()
    mismatched[0] = {**mismatched[0], "team": "Texas Tech"}
    identity = verify_pregame_inputs(_event(), mismatched, now=NOW)
    assert identity["code"] == "NCAAF_EVENT_IDENTITY_MISMATCH"


def test_verified_inputs_keep_market_features_empty():
    result = verify_pregame_inputs(_event(), _complete(), now=NOW)
    assert result["status"] == "PREGAME_INPUTS_VERIFIED"
    assert result["feature_row"]["market_home_no_vig"] is None
    assert result["market_features_used"] is False
    assert result["can_execute"] is False


def test_raw_snapshots_never_become_fitted_features():
    kickoff = START.isoformat()
    box = classify_source_snapshot_for_pregame(
        _snapshot("/games/players", retrieved="2026-11-01T00:00:00+00:00"),
        event_start_time=kickoff,
    )
    elo = classify_source_snapshot_for_pregame(
        _snapshot("/ratings/elo", retrieved="2026-11-01T00:00:00+00:00", blockers=["RETROSPECTIVE_RATING_NOT_PREGAME_FEATURE"]),
        event_start_time=kickoff,
    )
    late = classify_source_snapshot_for_pregame(
        _snapshot("/games", retrieved=START.isoformat()),
        event_start_time=kickoff,
    )
    for receipt in (box, elo, late):
        assert receipt["admitted_as_pregame_feature"] is False
        assert receipt["source_receipt_only"] is True
        assert receipt["can_execute"] is False
    assert "NCAAF_POSTGAME_SOURCE_NOT_PREGAME_FEATURE" in box["blocker_codes"]
    assert "NCAAF_RATING_SNAPSHOT_NOT_FITTED_FEATURE" in elo["blocker_codes"]
    assert "NCAAF_SOURCE_RETRIEVED_AFTER_KICKOFF" in late["blocker_codes"]


def test_insufficient_corpus_does_not_fit_a_challenger():
    calls = []

    def train(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("trainer must not run")

    empty_evidence = evaluate_fitted_challenger(
        object(),
        training_code_sha="abcdef1234567890",
        materialize=lambda _db: {
            "complete_feature_rows": 0,
            "games_seen": 4288,
            "evidence_rows_seen": 0,
            "probability_publishable": False,
            "can_execute": False,
        },
        train=train,
    )
    partial = evaluate_fitted_challenger(
        object(),
        training_code_sha="abcdef1234567890",
        materialize=lambda _db: {
            "complete_feature_rows": 12,
            "games_seen": 4288,
            "evidence_rows_seen": 348,
        },
        train=train,
    )
    assert calls == []
    assert empty_evidence["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert empty_evidence["native_code"] == "NCAAF_TRAINING_DATA_EMPTY"
    assert partial["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert partial["native_code"] == "NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT"
    assert partial["detail"]["challenger_fit"] is False
    assert partial["promoted"] is False
    assert partial["can_execute"] is False


def test_empty_corpus_uses_training_data_empty():
    result = evaluate_fitted_challenger(
        object(),
        training_code_sha="abcdef1234567890",
        materialize=lambda _db: {"complete_feature_rows": 0, "games_seen": 0, "evidence_rows_seen": 0},
        train=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no fit")),
    )
    assert result["native_code"] == "NCAAF_TRAINING_DATA_EMPTY"
    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"


def test_sufficient_rows_fit_a_held_candidate_only():
    result = evaluate_fitted_challenger(
        object(),
        training_code_sha="abcdef1234567890",
        materialize=lambda _db: {"complete_feature_rows": 300, "games_seen": 300, "evidence_rows_seen": 8700},
        train=lambda _db, *, training_code_sha: {
            "model_artifact_version": "NCAAF_LOGISTIC_V1-test",
            "calibrator_version": "NCAAF_CAL-test",
            "training_rows": 300,
            "lifecycle_state": "CANDIDATE",
            "calibration_health_status": "BLOCKED",
            "probability_publishable": False,
            "promoted": False,
            "can_execute": False,
            "training_code_sha": training_code_sha,
        },
    )
    assert result["status"] == "CHALLENGER_EVALUATED_HELD"
    assert result["lifecycle_state"] == "CANDIDATE"
    assert result["calibration_health_status"] == "BLOCKED"
    assert result["calibrated_lower_bound"] is None
    assert result["probability_publishable"] is False
    assert result["promoted"] is False
    assert result["automatic_promotion"] is False


def test_scorer_failure_and_stale_context_keep_native_codes():
    def explode(*_args, **_kwargs):
        raise NCAAFEventFeatureHandoffUnavailable("MODEL_SCORER_FAILED", "TimeoutError")

    failed = score_pregame_moneyline(
        object(),
        official_event_id="401800001",
        event_start_time=START.isoformat(),
        home_team="Texas",
        away_team="Oklahoma",
        expected_source_manifest_sha256="a" * 64,
        score_fn=explode,
    )
    assert failed["code"] == "MODEL_SCORER_FAILED"
    assert failed["model_invoked"] is True
    assert failed["code"] != "MODEL_UNAVAILABLE"

    def stale(*_args, **_kwargs):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_CURRENT_EVENT_CONTEXT_CHANGED", "injury_evidence_timestamp")

    changed = score_pregame_moneyline(
        object(),
        official_event_id="401800001",
        event_start_time=START.isoformat(),
        home_team="Texas",
        away_team="Oklahoma",
        expected_source_manifest_sha256="b" * 64,
        score_fn=stale,
    )
    assert changed["code"] == "NCAAF_CURRENT_EVENT_CONTEXT_CHANGED"
    assert changed["model_invoked"] is False
    assert changed["probability_publishable"] is False


def test_missing_snapshot_is_inputs_insufficient_and_success_stays_unpublished():
    def missing(*_args, **_kwargs):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND")

    absent = score_pregame_moneyline(
        object(),
        official_event_id="401800001",
        event_start_time=START.isoformat(),
        home_team="Texas",
        away_team="Oklahoma",
        expected_source_manifest_sha256="c" * 64,
        score_fn=missing,
    )
    assert absent["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert absent["native_code"] == "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND"
    assert absent["model_invoked"] is False

    def scored(*_args, **_kwargs):
        return {
            "status": "SCORED_HELD",
            "raw_home_probability": 0.61,
            "raw_away_probability": 0.39,
            "calibrated_lower_bound": None,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
            "blockers": ["NCAAF_CERTIFIED_DYNAMIC_BOUND_NOT_AVAILABLE"],
        }

    receipt = score_pregame_moneyline(
        object(),
        official_event_id="401800001",
        event_start_time=START.isoformat(),
        home_team="Texas",
        away_team="Oklahoma",
        expected_source_manifest_sha256="d" * 64,
        score_fn=scored,
    )
    assert receipt["status"] == "SCORED_HELD"
    assert receipt["raw_home_probability"] == 0.61
    assert receipt["calibrated_lower_bound"] is None
    assert receipt["probability_publishable"] is False
    assert receipt["rank_eligible"] is False
    assert receipt["promoted"] is False
    assert receipt["can_execute"] is False
    assert "NCAAF_CERTIFIED_DYNAMIC_BOUND_NOT_AVAILABLE" in receipt["blockers"]
