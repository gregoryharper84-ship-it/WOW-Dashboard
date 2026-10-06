from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI

from live_state_acquisition_runtime import (
    LIVE_STATE_CAPTURE_SPORTS,
    LiveStateCaptureError,
    LiveStateCaptureRequest,
    capture_live_event_state,
    install_live_state_acquisition_routes,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.pending = None

    def insert(self, row):
        self.pending = dict(row)
        return self

    def execute(self):
        self.rows.append(self.pending)
        return SimpleNamespace(data=[self.pending])


class _DB:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "wow_live_state_snapshots"
        return _Table(self.rows)


def _req(sport):
    return LiveStateCaptureRequest(
        sport=sport,
        league=sport,
        official_event_id="777",
        home_team="Home Club",
        away_team="Away Club",
    )


def _mlb_payload():
    return {
        "gamePk": 777,
        "gameData": {
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
            "teams": {
                "home": {"id": 1, "name": "Home Club"},
                "away": {"id": 2, "name": "Away Club"},
            },
        },
        "liveData": {
            "linescore": {
                "currentInning": 6,
                "inningHalf": "Top",
                "outs": 1,
                "teams": {"home": {"runs": 4}, "away": {"runs": 3}},
                "offense": {"first": {"id": 12}, "second": None, "third": {"id": 13}},
                "defense": {"pitcher": {"id": 44}},
            },
            "plays": {"currentPlay": {"about": {"inning": 6}}},
        },
    }


def _nfl_payload():
    return {
        "header": {
            "competitions": [{
                "id": "777",
                "status": {
                    "period": 3,
                    "displayClock": "08:41",
                    "type": {"state": "in", "completed": False, "name": "STATUS_IN_PROGRESS"},
                },
                "competitors": [
                    {"homeAway": "home", "score": "17", "team": {"id": "1", "displayName": "Home Club"}},
                    {"homeAway": "away", "score": "20", "team": {"id": "2", "displayName": "Away Club"}},
                ],
                "situation": {"down": 2, "distance": 7, "yardLine": 41, "possession": "1"},
            }]
        },
        "drives": {"current": {"id": "drive-9", "team": {"id": "1"}}},
    }


def test_mlb_capture_is_server_owned_immutable_evidence_only():
    db = _DB()
    result = capture_live_event_state(
        _req("MLB"),
        db,
        http_get=lambda *_a, **_k: _Response(_mlb_payload()),
        now=datetime(2026, 10, 5, 23, 0, tzinfo=timezone.utc),
    )
    assert result["status"] == "CAPTURED_RAW_LIVE_STATE"
    assert result["model_ready"] is False
    assert result["model_ready_source_snapshot_id"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert len(db.rows) == 1
    row = db.rows[0]
    assert row["event_status"] == "IN_PROGRESS"
    assert row["state_schema_version"] == "MLB_LIVE_RAW_STATE_V1"
    assert row["state_json"]["inning"] == 6
    assert row["state_json"]["outs"] == 1
    assert row["state_json"]["base_occupancy"] == {"first": True, "second": False, "third": True}
    assert row["feature_model_family"] == "LIVE_RAW_STATE_ACQUISITION_V1"
    assert row["can_execute"] is False


def test_nfl_capture_preserves_live_clock_situation_without_probability():
    db = _DB()
    result = capture_live_event_state(
        _req("NFL"),
        db,
        http_get=lambda *_a, **_k: _Response(_nfl_payload()),
        now=datetime(2026, 10, 5, 23, 0, tzinfo=timezone.utc),
    )
    assert result["model_ready"] is False
    row = db.rows[0]
    assert row["state_schema_version"] == "NFL_LIVE_RAW_STATE_V1"
    assert row["state_json"]["period"] == 3
    assert row["state_json"]["display_clock"] == "08:41"
    assert row["state_json"]["situation"]["down"] == 2
    assert row["state_json"]["home_score"] == 17
    assert row["state_json"]["away_score"] == 20
    assert row["can_execute"] is False



def test_server_owned_capture_covers_all_active_board_team_sports():
    assert LIVE_STATE_CAPTURE_SPORTS == {
        "MLB", "NFL", "NBA", "WNBA", "NCAAF", "NCAAB", "NHL",
    }
    for sport in ("NFL", "NBA", "WNBA", "NCAAF", "NCAAB", "NHL"):
        db = _DB()
        result = capture_live_event_state(
            _req(sport),
            db,
            http_get=lambda *_a, **_k: _Response(_nfl_payload()),
            now=datetime(2026, 10, 5, 23, 0, tzinfo=timezone.utc),
        )
        assert result["sport"] == sport
        assert result["model_ready"] is False
        assert db.rows[0]["state_schema_version"] == f"{sport}_LIVE_RAW_STATE_V1"
        assert db.rows[0]["can_execute"] is False


def test_remaining_manifest_sports_fail_with_sport_specific_capture_blocker():
    for sport in ("SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET"):
        try:
            capture_live_event_state(_req(sport), _DB(), http_get=lambda *_a, **_k: _Response({}))
        except LiveStateCaptureError as exc:
            assert exc.code == f"LIVE_STATE_CAPTURE_PROVIDER_NOT_WIRED:{sport}"
            assert exc.http_status == 409
        else:
            raise AssertionError(f"{sport} must fail closed until a server-owned provider is wired")


def test_capture_rejects_non_live_event():
    payload = _mlb_payload()
    payload["gameData"]["status"] = {"abstractGameState": "Final", "detailedState": "Final"}
    try:
        capture_live_event_state(_req("MLB"), _DB(), http_get=lambda *_a, **_k: _Response(payload))
    except LiveStateCaptureError as exc:
        assert exc.code == "LIVE_EVENT_NOT_IN_PROGRESS"
        assert exc.http_status == 409
    else:
        raise AssertionError("final event should fail closed")


def test_capture_rejects_mlb_provider_event_identity_mismatch():
    payload = _mlb_payload()
    payload["gamePk"] = 778
    try:
        capture_live_event_state(_req("MLB"), _DB(), http_get=lambda *_a, **_k: _Response(payload))
    except LiveStateCaptureError as exc:
        assert exc.code == "LIVE_EVENT_IDENTITY_CONFLICT"
        assert exc.detail["requested_official_event_id"] == "777"
        assert exc.detail["provider_official_event_id"] == "778"
    else:
        raise AssertionError("MLB event-id mismatch should fail closed")


def test_capture_rejects_espn_provider_event_identity_mismatch():
    payload = _nfl_payload()
    payload["header"]["competitions"][0]["id"] = "778"
    try:
        capture_live_event_state(_req("NFL"), _DB(), http_get=lambda *_a, **_k: _Response(payload))
    except LiveStateCaptureError as exc:
        assert exc.code == "LIVE_EVENT_IDENTITY_CONFLICT"
        assert exc.detail["requested_official_event_id"] == "777"
        assert exc.detail["provider_official_event_id"] == "778"
    else:
        raise AssertionError("ESPN event-id mismatch should fail closed")


def test_capture_rejects_missing_provider_event_identity():
    payload = _nfl_payload()
    payload["header"]["competitions"][0].pop("id")
    try:
        capture_live_event_state(_req("NFL"), _DB(), http_get=lambda *_a, **_k: _Response(payload))
    except LiveStateCaptureError as exc:
        assert exc.code == "LIVE_STATE_PROVIDER_PAYLOAD_INVALID"
        assert exc.http_status == 502
    else:
        raise AssertionError("missing provider event identity should fail closed")


def test_capture_rejects_provider_team_identity_mismatch():
    payload = _nfl_payload()
    payload["header"]["competitions"][0]["competitors"][0]["team"]["displayName"] = "Wrong Home"
    try:
        capture_live_event_state(_req("NFL"), _DB(), http_get=lambda *_a, **_k: _Response(payload))
    except LiveStateCaptureError as exc:
        assert exc.code == "LIVE_EVENT_IDENTITY_CONFLICT"
    else:
        raise AssertionError("identity mismatch should fail closed")


def test_capture_route_installs_exactly_once_and_is_authenticated():
    app = FastAPI()
    auth = Depends(lambda: None)
    db = _DB()
    install_live_state_acquisition_routes(app, auth_dependency=auth, db_client_fn=lambda: db)
    install_live_state_acquisition_routes(app, auth_dependency=auth, db_client_fn=lambda: db)
    routes = [
        (route.path, tuple(sorted(route.methods or [])), getattr(route, "operation_id", None))
        for route in app.router.routes
    ]
    matches = [r for r in routes if r[0] == "/capture-live-event-state" and "POST" in r[1]]
    assert matches == [("/capture-live-event-state", ("POST",), "captureWowLiveEventState")]
