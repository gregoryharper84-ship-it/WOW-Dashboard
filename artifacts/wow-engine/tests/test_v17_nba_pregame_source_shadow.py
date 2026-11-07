"""Fail-closed adversarial tests for NBA shadow-only official pregame capture."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import pytest

from v17.nba_pregame_source_shadow import (
    NBAShadowSourceError, capture_nba_pregame_shadow, SOURCE_URL
)

TIP = "2026-11-08T01:00:00Z"
BEFORE = datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc)


def _payload():
    return {
        "header": {"competitions": [{
            "id": "401909999", "date": TIP,
            "status": {"type": {"state": "pre", "completed": False}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "1"}},
                {"homeAway": "away", "team": {"id": "2"}},
            ],
        }]}
    }


class _Response:
    status_code = 200
    def __init__(self, data): self.data = data
    def json(self): return self.data


def _capture(data=None, *, clock_values=None, **changes):
    payload = _payload() if data is None else data
    calls = []
    stamps = iter(clock_values or [BEFORE, BEFORE + timedelta(seconds=1)])
    def get(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return _Response(payload)
    args = dict(
        official_event_id="espn-401909999", home_team_id="espn-1",
        away_team_id="espn-2", event_start_time_utc=TIP,
        http_get=get, clock=lambda: next(stamps)
    )
    args.update(changes)
    return capture_nba_pregame_shadow(**args), calls


def _held(expected, data=None, **changes):
    with pytest.raises(NBAShadowSourceError) as info:
        _capture(data, **changes)
    assert info.value.code == expected


def test_real_source_identity_shadow_digest_is_not_model_or_attestation():
    receipt, calls = _capture()
    assert len(calls) == 1
    assert calls[0][0] == SOURCE_URL
    assert calls[0][1] == {"event": "401909999"}
    assert calls[0][3] == 8.0
    assert receipt["source_provider"] == "ESPN_NBA_GAME_SUMMARY"
    assert receipt["raw_source_sha256"] == hashlib.sha256(
        json.dumps(_payload(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert receipt["official_event_id"] == "espn-401909999"
    assert receipt["timestamp_attestation"] == "UNVERIFIED"
    assert receipt["source_replay_status"] == "RAW_CAPTURE_RESEARCH_ONLY_UNATTESTED"
    assert receipt["lineup_evidence_status"] == "NOT_ACQUIRED"
    assert receipt["immutable_persistence_status"] == "NOT_PERSISTED"
    for key in ("model_ready", "model_invoked", "probability_publishable", "rank_eligible", "can_execute"):
        assert receipt[key] is False
    assert "calibrated_probability" not in receipt
    assert "source_snapshot_id" not in receipt


@pytest.mark.parametrize("override,code", [
    ({"official_event_id": "https://evil.example/"}, "NBA_SHADOW_SOURCE_IDENTITY_INVALID"),
    ({"home_team_id": "espn-2"}, "NBA_SHADOW_SOURCE_IDENTITY_INVALID"),
    ({"away_team_id": ""}, "NBA_SHADOW_SOURCE_IDENTITY_INVALID"),
    ({"event_start_time_utc": "2026-11-08"}, "NBA_SHADOW_SOURCE_EVENT_TIME_INVALID"),
])
def test_invalid_caller_identity_cannot_trigger_fetch(override, code):
    calls = []
    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("network should not be touched")
    args = dict(official_event_id="espn-401909999", home_team_id="espn-1",
                away_team_id="espn-2", event_start_time_utc=TIP,
                http_get=forbidden, clock=lambda: BEFORE)
    args.update(override)
    with pytest.raises(NBAShadowSourceError, match=code):
        capture_nba_pregame_shadow(**args)
    assert not calls


@pytest.mark.parametrize("mutation,code", [
    (("header", "competitions", 0, "id", "401909998"), "NBA_SHADOW_SOURCE_EVENT_IDENTITY_MISMATCH"),
    (("header", "competitions", 0, "date", "2026-11-08T02:00:00Z"), "NBA_SHADOW_SOURCE_EVENT_TIME_MISMATCH"),
    (("header", "competitions", 0, "status", "state", "in"), "NBA_SHADOW_SOURCE_EVENT_NOT_PREGAME"),
    (("header", "competitions", 0, "status", "completed", True), "NBA_SHADOW_SOURCE_EVENT_NOT_PREGAME"),
    (("header", "competitions", 0, "competitors", 0, "id", "3"), "NBA_SHADOW_SOURCE_EVENT_IDENTITY_MISMATCH"),
])
def test_conflicting_provider_data_fails_closed(mutation, code):
    payload = copy.deepcopy(_payload())
    if mutation[3] == "status":
        payload["header"]["competitions"][0]["status"]["type"][mutation[4]] = mutation[5]
    elif mutation[3] == "competitors":
        payload["header"]["competitions"][0]["competitors"][mutation[4]]["team"][mutation[5]] = mutation[6]
    else:
        payload["header"]["competitions"][0][mutation[3]] = mutation[4]
    _held(code, payload)


def test_duplicate_roles_fail_closed():
    payload = _payload()
    payload["header"]["competitions"][0]["competitors"][1]["homeAway"] = "home"
    _held("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID", payload)


@pytest.mark.parametrize("timestamps", [
    [datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc)],
    [BEFORE, datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc)],
    [BEFORE, BEFORE - timedelta(seconds=1)],
])
def test_crossing_tipoff_or_rewinding_server_clock_holds(timestamps):
    _held("NBA_SHADOW_SOURCE_CAPTURE_AFTER_KICKOFF", clock_values=timestamps)


def test_provider_failure_and_malformed_schema_keep_typed_holds():
    _held("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID", {"header": {}})
    def error(*_args, **_kwargs): raise TimeoutError("secret internal provider details")
    with pytest.raises(NBAShadowSourceError) as exc:
        _capture(http_get=error)
    assert exc.value.code == "NBA_SHADOW_SOURCE_PROVIDER_UNAVAILABLE"
    assert "secret" not in str(exc.value)


# The existing DB ledger accepts SCHEDULED source-only snapshots and forbids
# UPDATE/DELETE; these tests keep even successfully persisted data non-model-ready.
from types import SimpleNamespace


class _SnapshotDB:
    def __init__(self, *, fail=False, missing_confirmation=False):
        self.rows = []
        self.names = []
        self.fail = fail
        self.missing_confirmation = missing_confirmation

    def table(self, name):
        self.names.append(name)
        return self

    def insert(self, value):
        self.rows.append(value)
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("private database message should not escape")
        if self.missing_confirmation:
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[self.rows[-1]])


def test_shadow_current_source_can_append_immutable_non_scoring_projection():
    db = _SnapshotDB()
    receipt, requests = _capture(
        db=db,
        clock_values=[BEFORE, BEFORE + timedelta(seconds=1), BEFORE + timedelta(seconds=2)],
    )
    assert len(requests) == 1
    assert db.names == ["wow_live_state_snapshots"]
    assert len(db.rows) == 1
    row = db.rows[0]
    assert row["event_status"] == "SCHEDULED"
    assert row["source_provider"] == "ESPN_NBA_GAME_SUMMARY"
    assert row["source_uri"] == SOURCE_URL + "?event=401909999"
    assert row["official_event_id"] == "espn-401909999"
    assert row["source_snapshot_id"] == receipt["raw_source_snapshot_id"]
    assert row["state_hash"] == hashlib.sha256(
        json.dumps(row["state_json"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert row["state_json"]["source_hash_semantics"] == "CANONICAL_JSON_NOT_HTTP_WIRE_BYTES"
    assert row["state_json"]["source_replay_status"] == "SOURCE_BODY_NOT_RETAINED_NO_EVENT_REPLAY_ATTESTATION"
    assert "probability" not in str(row["state_json"])
    assert "feature" not in row["state_json"] or row["state_json"]["feature_hydration_status"] == "NOT_MATERIALIZED"
    assert receipt["immutable_persistence_status"] == "PERSISTED_SOURCE_PROJECTION_ONLY"
    assert receipt["timestamp_attestation"] == "UNVERIFIED"
    for key in ("model_ready", "model_invoked", "probability_publishable", "rank_eligible", "can_execute"):
        assert receipt[key] is False
    assert row["can_execute"] is False
    assert "calibrated_probability" not in receipt
    assert "model_ready_source_snapshot_id" not in receipt


@pytest.mark.parametrize("failure,expected", [
    ("throw", "NBA_SHADOW_SOURCE_PERSISTENCE_FAILED"),
    ("unverified", "NBA_SHADOW_SOURCE_PERSISTENCE_UNVERIFIED"),
])
def test_shadow_persistence_failure_keeps_native_typed_hold_without_model_output(failure, expected):
    db = _SnapshotDB(fail=failure == "throw", missing_confirmation=failure == "unverified")
    with pytest.raises(NBAShadowSourceError) as caught:
        _capture(db=db, clock_values=[BEFORE, BEFORE + timedelta(seconds=1), BEFORE + timedelta(seconds=2)])
    assert caught.value.code == expected
    assert "private" not in str(caught.value)
    assert db.names == ["wow_live_state_snapshots"]


def test_shadow_capture_crosses_tipoff_before_insert_and_never_writes():
    db = _SnapshotDB()
    with pytest.raises(NBAShadowSourceError) as caught:
        _capture(db=db, clock_values=[
            BEFORE, BEFORE + timedelta(seconds=1),
            datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc),
        ])
    assert caught.value.code == "NBA_SHADOW_SOURCE_CAPTURE_AFTER_KICKOFF"
    assert db.names == []
    assert db.rows == []


def test_shadow_capture_rewinds_clock_before_insert_and_never_writes():
    db = _SnapshotDB()
    with pytest.raises(NBAShadowSourceError) as caught:
        _capture(db=db, clock_values=[BEFORE, BEFORE + timedelta(seconds=1), BEFORE])
    assert caught.value.code == "NBA_SHADOW_SOURCE_CAPTURE_AFTER_KICKOFF"
    assert db.rows == []


def test_shadow_persistence_unsafe_provider_identity_never_writes():
    db = _SnapshotDB()
    payload = _payload()
    payload["header"]["competitions"][0]["competitors"][0]["team"]["id"] = "3"
    with pytest.raises(NBAShadowSourceError) as caught:
        _capture(payload, db=db)
    assert caught.value.code == "NBA_SHADOW_SOURCE_EVENT_IDENTITY_MISMATCH"
    assert db.rows == []
