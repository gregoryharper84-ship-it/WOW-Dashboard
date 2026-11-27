"""Hourly paper-trade slip lane: builds, stores and grades through the audit."""
from datetime import datetime, timedelta, timezone

import pytest

from v17.prop_priority_lifecycle_runtime import (
    PriorityPropDurableAuditRequest,
    run_priority_durable_audit,
)
from v17.prop_slip_lane_runtime import (
    HELD_AVAILABILITY_NOT_CONFIRMED,
    HELD_ARTIFACT_NOT_PUBLISHABLE,
    HELD_CT_WINDOW,
    grade_pending_slips,
    run_slip_lane,
)
from wow_slip_store import SlipLedgerStore

NOW = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
ROUTE = "NFL:RECEIVING_YARDS"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.rows = list(db.tables.setdefault(table, []))
        self._limit = None
        self._insert = None

    def select(self, *_args):
        return self

    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def in_(self, field, values):
        allowed = set(values)
        self.rows = [row for row in self.rows if row.get(field) in allowed]
        return self

    def gt(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field) or "") > str(value)]
        return self

    def order(self, field):
        self.rows.sort(key=lambda row: str(row.get(field) or ""))
        return self

    def limit(self, value):
        self._limit = value
        return self

    def range(self, start, end):
        self.rows = self.rows[start:end + 1]
        return self

    def insert(self, payload):
        self._insert = dict(payload)
        return self

    def execute(self):
        if self._insert is not None:
            self.db.tables.setdefault(self.table, []).append(self._insert)
            return _Result([self._insert])
        data = self.rows[: self._limit] if self._limit is not None else self.rows
        return _Result(data)


class _Db:
    def __init__(self, tables=None):
        self.tables = {name: list(rows) for name, rows in (tables or {}).items()}

    def table(self, name):
        return _Query(self, name)


def _artifact(*, publishable=True, stat_type="RECEIVING_YARDS", version="NFL_REC_V1"):
    return {
        "sport": "NFL",
        "stat_type": stat_type,
        "model_artifact_version": version,
        "active": True,
        "probability_publishable": publishable,
    }


def _evidence(snapshot_id, *, designation="ACTIVE", hydration="PASS", blockers=()):
    return {
        "source_snapshot_id": snapshot_id,
        "hydration_status": hydration,
        "blockers": list(blockers),
        "role_status": {
            "availability": {
                "designation": designation,
                "source": "OFFICIAL_INJURY_REPORT",
                "captured_at": (NOW - timedelta(hours=1)).isoformat(),
                "evidence_status": "RETRIEVED",
                "athlete_id": f"athlete-{snapshot_id}",
                "event_id": "NFL:2026-11-27-AAA-BBB",
                "team": "AAA",
                "fresh": True,
                "report_complete": True,
            }
        },
    }


def _prediction(index, *, direction="LESS", probability=0.62, lower=0.58, stat_type="RECEIVING_YARDS",
                hours_to_start=2, version="NFL_REC_V1", snapshot=True):
    prediction_id = f"00000000-0000-0000-0000-0000000{index:05d}"
    snapshot_id = f"10000000-0000-0000-0000-0000000{index:05d}" if snapshot else None
    return {
        "prediction_id": prediction_id,
        "event_id": "NFL:2026-11-27-AAA-BBB",
        "event_start_time": (NOW + timedelta(hours=hours_to_start)).isoformat(),
        "model_timestamp": (NOW - timedelta(minutes=30)).isoformat(),
        "locked_at": (NOW - timedelta(minutes=30)).isoformat(),
        "player": f"Receiver {index}",
        "team": "AAA",
        "sport": "NFL",
        "stat_type": stat_type,
        "line": 3.5,
        "direction": direction,
        "model_provider_identity": PROVIDER,
        "model_artifact_version": version,
        "calibration_version": "NFL_REC_CAL_V1",
        "calibrated_probability": probability,
        "calibrated_probability_lower_bound": lower,
        "source_snapshot_id": snapshot_id,
        "probability_publishable": True,
    }


def _full_slate(count=6, **prediction_kwargs):
    db = _Db({
        "wow_prop_fitted_model_artifacts": [_artifact()],
        "wow_predictions": [_prediction(i, **prediction_kwargs) for i in range(1, count + 1)],
        "wow_prop_evidence_snapshots": [_evidence(f"10000000-0000-0000-0000-0000000{i:05d}") for i in range(1, count + 1)],
        "wow_outcomes": [],
        "wow_slip_snapshots": [],
        "wow_slip_grades": [],
    })
    return db


def test_hourly_audit_builds_persists_and_grades_a_paper_slip():
    db = _full_slate()
    result = run_priority_durable_audit(PriorityPropDurableAuditRequest(routes=[ROUTE]), db=db, now=NOW)

    stage = result["paper_trade_slip_lane"]
    assert stage["status"] == "PASS"
    lane = stage["result"]
    assert lane["snapshots_built"] == 1 and lane["snapshots_stored"] == 1
    assert lane["candidates_eligible"] == 6
    assert lane["local_file_written"] is False
    assert lane["persistence_path"] == "SUPABASE_SERVICE_ROLE"
    assert lane["acquisition_performed"] is False and lane["probability_modified"] is False
    assert result["can_execute"] is False and stage["can_execute"] is False

    rows = db.tables["wow_slip_snapshots"]
    assert len(rows) == 1
    row = rows[0]
    assert row["archetype"] == "nfl_niche_fade" and row["product"] == "6_pick_power"
    assert row["can_execute"] is False
    assert len(row["legs_json"]) == 6
    assert {leg["direction"] for leg in row["legs_json"]} == {"LESS"}
    # Most archetypes are outside their CT window at 18:00 UTC and hold, not fail.
    assert lane["holds_by_code"][HELD_CT_WINDOW] >= 1

    assert lane["grading"]["grades_written"] == 0
    assert lane["grading"]["pending_outcomes"] == 1

    # Outcomes settle after the slate; the same hourly lane now grades the slip.
    db.tables["wow_outcomes"] = [
        {
            "prediction_id": leg["leg_id"],
            "actual_stat": 1.0,
            "void": False,
            "settlement_timestamp": (NOW + timedelta(hours=4)).isoformat(),
        }
        for leg in row["legs_json"]
    ]
    again = run_slip_lane(db, now=NOW + timedelta(hours=5))
    assert again["grading"]["grades_written"] == 1
    grade = db.tables["wow_slip_grades"][0]
    assert grade["wins"] == 6 and grade["losses"] == 0
    assert grade["effective_multiplier"] == 37.5 and grade["net_units"] > 0
    assert grade["can_execute"] is False

    # A later hourly cycle while still pregame neither duplicates nor re-grades.
    third = run_slip_lane(db, now=NOW + timedelta(minutes=90))
    assert third["snapshots_built"] == 0
    assert any(d["status"] == "SKIPPED_DUPLICATE_LEG_SET" for d in third["build_dispositions"])
    assert len(db.tables["wow_slip_snapshots"]) == 1
    assert len(db.tables["wow_slip_grades"]) == 1

    # Once the events have started, expired rows are not admitted at all.
    late = run_slip_lane(db, now=NOW + timedelta(hours=6))
    assert late["candidates_eligible"] == 0 and late["snapshots_built"] == 0


def test_unpublishable_artifact_and_unconfirmed_availability_cannot_be_paper_traded():
    db = _full_slate()
    db.tables["wow_prop_fitted_model_artifacts"][0]["probability_publishable"] = False
    out = run_slip_lane(db, now=NOW)
    assert out["candidates_eligible"] == 0 and out["snapshots_built"] == 0
    assert out["candidate_holds"][HELD_ARTIFACT_NOT_PUBLISHABLE] == 6
    assert db.tables["wow_slip_snapshots"] == []

    db = _full_slate()
    db.tables["wow_prop_evidence_snapshots"][0] = _evidence(
        "10000000-0000-0000-0000-000000000001", designation="OUT"
    )
    out = run_slip_lane(db, now=NOW)
    assert out["candidates_eligible"] == 5
    assert out["candidate_holds"][HELD_AVAILABILITY_NOT_CONFIRMED] == 1
    # Five confirmed legs cannot fill a six-pick archetype: fail closed, no slip.
    assert out["snapshots_built"] == 0
    assert db.tables["wow_slip_snapshots"] == []


def test_rows_without_an_active_fitted_artifact_or_calibration_are_held():
    db = _full_slate()
    db.tables["wow_prop_fitted_model_artifacts"] = []
    out = run_slip_lane(db, now=NOW)
    assert out["candidates_eligible"] == 0 and out["snapshots_built"] == 0
    assert all("HELD_NO_ACTIVE_ARTIFACT" == code for code in out["candidate_holds"])


def test_partial_official_outcomes_leave_the_slip_pending_instead_of_pushing():
    db = _full_slate()
    run_slip_lane(db, now=NOW)
    snapshot = db.tables["wow_slip_snapshots"][0]
    db.tables["wow_outcomes"] = [
        {"prediction_id": leg["leg_id"], "actual_stat": 1.0, "void": False,
         "settlement_timestamp": (NOW + timedelta(hours=4)).isoformat()}
        for leg in snapshot["legs_json"][:3]
    ]
    out = grade_pending_slips(db, SlipLedgerStore(supabase_client=db, append_jsonl=False))
    assert out["grades_written"] == 0
    assert out["pending_outcomes"] == 1
    assert db.tables["wow_slip_grades"] == []


def test_service_role_only_store_writes_no_local_file_and_fails_closed_without_a_client(tmp_path):
    db = _Db()
    store = SlipLedgerStore(data_dir=tmp_path / "slips", supabase_client=db, append_jsonl=False)
    assert not (tmp_path / "slips").exists()

    db.tables["wow_slip_grades"] = []
    from wow_slip_builder import ArchetypeId, Direction, PowerProduct, SlipSnapshot

    snapshot = SlipSnapshot(
        archetype=ArchetypeId.NFL_NICHE_FADE,
        product=PowerProduct.SIX_PICK,
        multiplier=37.5,
        primary_direction=Direction.LESS,
        legs=[],
        captured_at=NOW,
    )
    store.write_snapshot(snapshot)
    assert len(db.tables["wow_slip_snapshots"]) == 1
    assert db.tables["wow_slip_snapshots"][0]["can_execute"] is False
    assert not (tmp_path / "slips").exists()

    with pytest.raises(RuntimeError):
        SlipLedgerStore(data_dir=tmp_path / "other", append_jsonl=False).write_snapshot(snapshot)
