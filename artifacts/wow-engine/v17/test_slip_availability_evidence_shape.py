"""Availability evidence-shape contract for the hourly paper-trade slip lane.

The slip lane admits a prediction only when its persisted evidence snapshot
confirms official availability. If a hydrator's ``role_status`` shape ever
drifts from what the gate reads, every production row would be silently held
and the lane would look healthy while producing nothing. These tests run the
real NFL and WNBA hydrators and assert the produced shape still clears the
gate, so that failure becomes a red test instead of an empty ledger.

Fixture provenance: the provider payloads below are the documented ESPN/WNBA
response shapes already used by the hydration suites (see
``v17/fixtures/participation/README.md`` — that corpus is reconstructed, not
network-captured). What is real here is the hydrator output: ``role_status``
is produced by production code, not hand-written, which is what the gate
actually consumes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import nfl_prop_auto_hydration as nfl
import wnba_prop_auto_hydration as wnba
from test_nfl_prop_history_chronology import _Sources, _event, _hydrate, _regular
from test_wnba_prop_auto_hydration import NOW as WNBA_NOW
from test_wnba_prop_auto_hydration import EVENT as WNBA_EVENT
from test_wnba_prop_auto_hydration import http_get_factory, submitted_report

from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNVERIFIED,
    evaluate_player_availability,
)
from v17.prop_slip_lane_runtime import (
    HELD_AVAILABILITY_NOT_CONFIRMED,
    _availability_confirmed,
    run_slip_lane,
)
from v17.test_prop_slip_lane_runtime import _Db

# Keys the availability gate reads from the persisted role_status["availability"].
_GATE_READ_KEYS = (
    "source",
    "captured_at",
    "evidence_status",
    "fresh",
    "report_complete",
    "athlete_id",
    "event_id",
    "team",
)

NFL_EVENT = "2026-09-15T17:00:00Z"
NFL_CAPTURED = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)
NFL_INJURY_DATE = "2026-09-14T12:00:00Z"


class _NflSources(_Sources):
    """Documented ESPN team-roster shape on top of the real NFL hydration harness."""

    def __init__(self, *args, injury_date: str = NFL_INJURY_DATE, **kwargs):
        super().__init__(*args, **kwargs)
        self.injury_date = injury_date

    def __call__(self, url, *, params, headers, timeout, follow_redirects):
        if url.endswith("/teams/kc/roster"):
            return _RosterResponse({
                "team": {"abbreviation": "KC"},
                "athletes": [
                    {
                        "position": "offense",
                        "items": [
                            {"id": "123", "status": {"type": "active"}},
                            {
                                "id": "999",
                                "status": {"type": "active"},
                                "injuries": [
                                    {"date": self.injury_date, "status": "Questionable"}
                                ],
                            },
                        ],
                    },
                    {"position": "defense", "items": [{"id": "888", "status": {"type": "active"}}]},
                    {"position": "injuredReserveOrOut", "items": []},
                ],
            })
        return super().__call__(url, params=params, headers=headers, timeout=timeout,
                                follow_redirects=follow_redirects)


class _RosterResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.content = b""
        self.text = ""

    def json(self):
        return self._payload


def _nfl_sources(*, injury_date: str = NFL_INJURY_DATE) -> _NflSources:
    # Contiguous seasons are required: the hydrator refuses to cross a missing
    # prior season rather than silently narrowing its fit window.
    rows26, events26 = _regular(2026, range(1, 19))
    rows25, events25 = _regular(2025, range(1, 19), value_offset=100)
    return _NflSources(
        _event(2026, 3, NFL_EVENT, completed=False),
        {2026: rows26, 2025: rows25},
        {(2026, 2): events26, (2025, 2): events25},
        injury_date=injury_date,
    )


def _stored_evidence(payload: dict, snapshot_id: str) -> dict:
    """The row the router persists: hydrate output plus hydration bookkeeping."""
    return {
        "source_snapshot_id": snapshot_id,
        "hydration_status": "PASS",
        "blockers": [],
        "role_status": payload["role_status"],
    }


def _prediction(index: int, snapshot_id: str) -> dict:
    return {
        "prediction_id": f"00000000-0000-0000-0000-0000000{index:05d}",
        "event_id": "NFL:2026-09-15-KC-BUF",
        "event_start_time": NFL_EVENT,
        "model_timestamp": (NFL_CAPTURED - timedelta(minutes=30)).isoformat(),
        "locked_at": (NFL_CAPTURED - timedelta(minutes=30)).isoformat(),
        "player": f"Receiver {index}",
        "team": "KC",
        "sport": "NFL",
        "stat_type": "RECEIVING_YARDS",
        "line": 3.5,
        "direction": "LESS",
        "model_provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_artifact_version": "NFL_REC_V1",
        "calibration_version": "NFL_REC_CAL_V1",
        "calibrated_probability": 0.62,
        "calibrated_probability_lower_bound": 0.58,
        "source_snapshot_id": snapshot_id,
        "probability_publishable": True,
    }


def test_nfl_hydration_role_status_clears_the_slip_availability_gate():
    payload = _hydrate(_nfl_sources(), NFL_EVENT)
    role = payload["role_status"]
    availability = role["availability"]

    # The gate reads these keys; hydration must keep writing every one of them.
    for key in _GATE_READ_KEYS:
        assert key in availability, f"NFL hydration stopped writing {key!r}"

    assert availability["basis"] == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert availability["evidence_status"] == "RETRIEVED"
    assert availability["fresh"] is True and availability["report_complete"] is True
    assert availability["athlete_id"] == "123"
    assert availability["event_id"] and availability["team"] == "KC"
    assert evaluate_player_availability(role).status == AVAILABILITY_AVAILABLE

    snapshot_id = "10000000-0000-0000-0000-000000000001"
    assert _availability_confirmed(_stored_evidence(payload, snapshot_id)) is True


def test_stale_nfl_official_report_stays_unconfirmed_so_the_gate_is_not_a_rubber_stamp():
    stale = (NFL_CAPTURED - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    payload = _hydrate(_nfl_sources(injury_date=stale), NFL_EVENT)
    role = payload["role_status"]
    assert evaluate_player_availability(role).status == AVAILABILITY_UNVERIFIED
    assert _availability_confirmed(_stored_evidence(payload, "snapshot-stale")) is False


def test_wnba_hydration_role_status_clears_the_slip_availability_gate(monkeypatch):
    get, _calls = http_get_factory()
    monkeypatch.setattr(
        wnba,
        "_latest_injury_report",
        lambda now, http_get: (
            "https://ak-static.cms.nba.com/referee/wnba_injury/test.pdf",
            WNBA_NOW.astimezone(wnba.ET),
            submitted_report(),
        ),
    )
    payload = wnba.hydrate_wnba_prop_evidence(
        player="Test Player",
        stat_type="POINTS",
        event_start_time=WNBA_EVENT.isoformat(),
        now=WNBA_NOW,
        opponent="BBB",
        http_get=get,
    )
    role = payload["role_status"]
    availability = role["availability"]

    for key in _GATE_READ_KEYS:
        assert key in availability, f"WNBA hydration stopped writing {key!r}"

    assert availability["basis"] == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert availability["evidence_status"] == "RETRIEVED"
    assert availability["fresh"] is True and availability["report_complete"] is True
    assert availability["athlete_id"] == "1001"
    assert availability["event_id"] == "1022600200"
    assert evaluate_player_availability(role).status == AVAILABILITY_AVAILABLE
    assert _availability_confirmed(_stored_evidence(payload, "snapshot-wnba")) is True


def test_hourly_lane_builds_a_slip_from_hydrated_nfl_evidence_rows():
    """End to end: real hydration output persisted as evidence -> built snapshot."""
    payload = _hydrate(_nfl_sources(), NFL_EVENT)
    evidence = []
    predictions = []
    for index in range(1, 7):
        snapshot_id = f"10000000-0000-0000-0000-0000000{index:05d}"
        evidence.append(_stored_evidence(payload, snapshot_id))
        predictions.append(_prediction(index, snapshot_id))

    db = _Db({
        "wow_prop_fitted_model_artifacts": [{
            "sport": "NFL",
            "stat_type": "RECEIVING_YARDS",
            "model_artifact_version": "NFL_REC_V1",
            "active": True,
            "probability_publishable": True,
        }],
        "wow_predictions": predictions,
        "wow_prop_evidence_snapshots": evidence,
        "wow_outcomes": [],
        "wow_slip_snapshots": [],
        "wow_slip_grades": [],
    })

    out = run_slip_lane(db, now=NFL_CAPTURED)
    assert out["candidate_holds"] == {}
    assert out["candidates_eligible"] == 6
    assert out["snapshots_built"] == 1 and out["snapshots_stored"] == 1
    stored = db.tables["wow_slip_snapshots"][0]
    assert stored["archetype"] == "nfl_niche_fade"
    assert len(stored["legs_json"]) == 6
    assert stored["can_execute"] is False

    # A caller-supplied non-availability blocker on the same evidence must not
    # turn into a silent production hold of an otherwise cleared row.
    for row in db.tables["wow_prop_evidence_snapshots"]:
        row["blockers"] = ["SOME_NON_AVAILABILITY_NOTE"]
    db.tables["wow_slip_snapshots"].clear()
    again = run_slip_lane(db, now=NFL_CAPTURED)
    assert again["candidates_eligible"] == 6
    assert again["snapshots_built"] == 1


def test_non_availability_blocker_codes_do_not_hold_a_cleared_row():
    """blockers also carries caller-supplied codes; only availability holds."""
    payload = _hydrate(_nfl_sources(), NFL_EVENT)
    cleared = _stored_evidence(payload, "snapshot-1")

    unrelated = {**cleared, "blockers": ["SOME_NON_AVAILABILITY_NOTE"]}
    assert _availability_confirmed(unrelated) is True

    for code in ("PLAYER_RULED_OUT", "PLAYER_AVAILABILITY_UNVERIFIED",
                 "PLAYER_AVAILABILITY_RECHECK_REQUIRED"):
        assert _availability_confirmed({**cleared, "blockers": [code]}) is False

    assert _availability_confirmed({**cleared, "hydration_status": "INCOMPLETE"}) is False


def test_hydrated_shape_is_the_only_reason_rows_are_admitted():
    """Dropping the availability block reproduces the hold the shape test guards."""
    payload = _hydrate(_nfl_sources(), NFL_EVENT)
    broken = _stored_evidence(payload, "snapshot-1")
    broken["role_status"] = {
        key: value for key, value in broken["role_status"].items() if key != "availability"
    }
    assert _availability_confirmed(broken) is False

    db = _Db({
        "wow_prop_fitted_model_artifacts": [{
            "sport": "NFL", "stat_type": "RECEIVING_YARDS",
            "model_artifact_version": "NFL_REC_V1", "active": True,
            "probability_publishable": True,
        }],
        "wow_predictions": [_prediction(i, f"10000000-0000-0000-0000-0000000{i:05d}") for i in range(1, 7)],
        "wow_prop_evidence_snapshots": [
            {**_stored_evidence(payload, f"10000000-0000-0000-0000-0000000{i:05d}"),
             "role_status": broken["role_status"]}
            for i in range(1, 7)
        ],
        "wow_outcomes": [],
        "wow_slip_snapshots": [],
        "wow_slip_grades": [],
    })
    out = run_slip_lane(db, now=NFL_CAPTURED)
    assert out["candidates_eligible"] == 0
    assert out["candidate_holds"][HELD_AVAILABILITY_NOT_CONFIRMED] == 6
    assert out["snapshots_built"] == 0
