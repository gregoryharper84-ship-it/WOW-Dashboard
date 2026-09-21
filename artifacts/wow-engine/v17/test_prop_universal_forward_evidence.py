from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock

from v17.cross_sport_certification_inventory import CERTIFICATION_SPORTS
from v17.prop_capability_manifest import BUILD_REQUIRED, DECLARED_PROP_LANES
from v17.prop_universal_forward_evidence import (
    COLLECTOR_GENERIC,
    COLLECTOR_NONE,
    COLLECTOR_SEPARATE,
    MODEL_BUILD_REQUIRED,
    UniversalPropForwardEvidenceRequest,
    build_forward_evidence_inventory,
    run_generic_forward_route,
    run_universal_prop_forward_evidence,
)


NOW = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, rows):
        self.db = db
        self.table = table
        self.rows = list(rows)
        self._limit = None
        self._upsert = None

    def select(self, *_args): return self
    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self
    def gt(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field) or "") > str(value)]
        return self
    def in_(self, field, values):
        allowed = set(values)
        self.rows = [row for row in self.rows if row.get(field) in allowed]
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
    def upsert(self, payload, **_kwargs):
        self._upsert = dict(payload)
        return self
    def execute(self):
        if self._upsert is not None:
            self.db.tables.setdefault(self.table, []).append(self._upsert)
            return _Result([self._upsert])
        data = self.rows[: self._limit] if self._limit is not None else self.rows
        return _Result(data)


class _Db:
    def __init__(self, tables):
        self.tables = {name: list(rows) for name, rows in tables.items()}
    def table(self, name):
        return _Query(self, name, self.tables.setdefault(name, []))


class _Req:
    def __init__(self, **kwargs):
        self.payload = kwargs


class _Market:
    ScorePropRequest = _Req

    def score_prop(self, req, _identity):
        direction = req.payload["direction"]
        p_more, p_less, p_push = 0.62, 0.36, 0.02
        return {
            "probability_publishable": False,
            "research_only": True,
            "research_model_output": {
                "raw_specialist_probability": p_more if direction == "MORE" else p_less,
                "raw_probability_more": p_more,
                "raw_probability_less": p_less,
                "push_probability": p_push,
                "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
                "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
                "model_artifact_version": "WNBA_POINTS_MODEL_V1",
                "model_artifact_checksum": "a" * 64,
                "calibration_status": "PRECALIBRATION_SHRINKAGE",
                "calibration_version": "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1",
                "model_timestamp": NOW.isoformat(),
            },
            "blockers": ["FORWARD_CALIBRATION_CERTIFICATION_REQUIRED"],
        }


def _snapshot(snapshot_id, *, captured_minutes=10):
    return {
        "source_snapshot_id": snapshot_id,
        "captured_at": (NOW - timedelta(minutes=captured_minutes)).isoformat(),
        "event_id": "WNBA:TEST-1",
        "event_start_time": (NOW + timedelta(hours=2)).isoformat(),
        "sport": "WNBA",
        "player": "Test Player",
        "team": "AAA",
        "opponent": "BBB",
        "stat_type": "POINTS",
        "line": 20.5,
        "hydration_status": "PASS",
        "blockers": [],
    }


def test_inventory_accounts_for_every_declared_route_and_required_sport():
    rows = build_forward_evidence_inventory()
    route_rows = {
        (row["sport"], row["stat_type"]): row
        for row in rows
        if row["stat_type"] != "__SPORT_PROP_CATEGORY_INVENTORY__"
    }
    assert set(route_rows) == set(DECLARED_PROP_LANES)
    assert set(CERTIFICATION_SPORTS).issubset({row["sport"] for row in rows})
    assert all(row["can_execute"] is False for row in rows)


def test_cross_sport_build_targets_are_visible_but_have_no_phantom_collector():
    rows = build_forward_evidence_inventory()
    ncaaf_rows = [row for row in rows if row["sport"] == "NCAAF"]
    assert ncaaf_rows
    assert all(row["stat_type"] != "__SPORT_PROP_CATEGORY_INVENTORY__" for row in ncaaf_rows)
    assert all(row["declared_lane_status"] == BUILD_REQUIRED for row in ncaaf_rows)
    assert all(row["controlling_specialist"] is None for row in ncaaf_rows)
    assert all(row["collector"] == COLLECTOR_NONE for row in ncaaf_rows)
    assert all(row["status"] == MODEL_BUILD_REQUIRED for row in ncaaf_rows)
    assert all(row["blocker"] == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED" for row in ncaaf_rows)
    assert all(row["can_execute"] is False for row in ncaaf_rows)


def test_1ip_remains_on_its_separate_exact_route_contract():
    rows = build_forward_evidence_inventory()
    row = next(
        row for row in rows
        if row["sport"] == "MLB" and row["stat_type"] == "1ST_INNING_PITCHES_THROWN"
    )
    assert row["collector"] == COLLECTOR_SEPARATE
    assert row["blocker"] == "EXACT_ROUTE_SEPARATE_FORWARD_CONTRACT_REQUIRED"


def test_generic_wnba_route_dedupes_refreshed_snapshots_before_capture():
    db = _Db({
        "wow_prop_evidence_snapshots": [
            _snapshot("00000000-0000-0000-0000-000000000001", captured_minutes=20),
            _snapshot("00000000-0000-0000-0000-000000000002", captured_minutes=10),
        ],
        "wow_predictions": [],
        "wow_outcomes": [],
    })
    result = run_generic_forward_route(
        sport="WNBA",
        stat_type="POINTS",
        max_snapshots=10,
        db=db,
        market_api=_Market(),
        now=NOW,
    )
    assert result["collector"] == COLLECTOR_GENERIC
    assert result["snapshots_considered"] == 1
    assert result["captured_forward_predictions"] == 2
    assert result["calibration_readiness"]["forward_prediction_n"] == 1
    assert result["calibration_readiness"]["counting_basis"] == "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS"
    assert len(db.tables["wow_predictions"]) == 2
    assert {row["direction"] for row in db.tables["wow_predictions"]} == {"MORE", "LESS"}
    assert all(row["probability_publishable"] is False for row in db.tables["wow_predictions"])
    assert result["can_execute"] is False


def test_directional_twins_settle_as_one_independent_thesis():
    db = _Db({
        "wow_prop_evidence_snapshots": [_snapshot("00000000-0000-0000-0000-000000000003")],
        "wow_predictions": [],
        "wow_outcomes": [],
    })
    first = run_generic_forward_route(
        sport="WNBA",
        stat_type="POINTS",
        max_snapshots=10,
        db=db,
        market_api=_Market(),
        now=NOW,
    )
    prediction_ids = [row["prediction_id"] for row in db.tables["wow_predictions"]]
    db.tables["wow_outcomes"] = [
        {
            "prediction_id": prediction_id,
            "actual_stat": 24.0,
            "settlement_timestamp": (NOW + timedelta(hours=5)).isoformat(),
            "void": False,
        }
        for prediction_id in prediction_ids
    ]
    second = run_generic_forward_route(
        sport="WNBA",
        stat_type="POINTS",
        max_snapshots=10,
        db=db,
        market_api=_Market(),
        now=NOW,
    )
    assert first["calibration_readiness"]["forward_prediction_n"] == 1
    assert second["calibration_readiness"]["forward_prediction_n"] == 1
    assert second["calibration_readiness"]["forward_settled_n"] == 1


def test_universal_selected_route_reconciles_exactly_once_without_promotion():
    db = _Db({
        "wow_prop_evidence_snapshots": [_snapshot("00000000-0000-0000-0000-000000000004")],
        "wow_predictions": [],
        "wow_outcomes": [],
    })
    result = run_universal_prop_forward_evidence(
        UniversalPropForwardEvidenceRequest(routes=["WNBA:POINTS"], max_snapshots_per_route=5),
        db=db,
        market_api=_Market(),
        now=NOW,
    )
    assert result["run_status"] == "COMPLETED"
    assert result["routes_requested"] == 1
    assert result["routes_terminated"] == 1
    assert result["route_reconciliation_balanced"] is True
    assert result["certification_performed"] is False
    assert result["promotion_performed"] is False
    assert result["production_registration_performed"] is False
    assert result["can_execute"] is False


def test_universal_route_collection_is_bounded_parallel_and_ordered(monkeypatch):
    routes = ["WNBA:POINTS", "WNBA:REBOUNDS"]
    barrier = Barrier(len(routes))
    lock = Lock()
    active = 0
    peak = 0

    def fake_collect(*, sport, stat_type, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return {"sport": sport, "stat_type": stat_type, "collector": COLLECTOR_GENERIC, "can_execute": False}

    monkeypatch.setattr("v17.prop_universal_forward_evidence.run_generic_forward_route", fake_collect)
    result = run_universal_prop_forward_evidence(
        UniversalPropForwardEvidenceRequest(routes=routes, max_snapshots_per_route=1),
        db=_Db({}),
        market_api=_Market(),
        now=NOW,
    )

    assert peak == 2
    assert [f'{row["sport"]}:{row["stat_type"]}' for row in result["route_results"]] == routes
    assert result["route_reconciliation_balanced"] is True
    assert result["can_execute"] is False
