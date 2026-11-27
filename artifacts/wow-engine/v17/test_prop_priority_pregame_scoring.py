"""Hourly priority audit scores persisted pregame snapshots (DB-first, bounded, inert)."""
from datetime import timedelta

from v17.prop_priority_lifecycle_runtime import (
    PriorityPropDurableAuditRequest,
    run_pregame_forward_scoring,
    run_priority_durable_audit,
)
import v17.prop_universal_forward_schema_repair  # noqa: F401 - production overlay (artifact-pinned ids)
from v17.prop_universal_forward_evidence import COLLECTOR_GENERIC, _collector_for, route_key
from v17.test_prop_universal_forward_evidence import NOW, _Db, _Market, _snapshot

NFL_ROUTE = "NFL:RECEIVING_YARDS"


def _nfl_snapshot(snapshot_id, *, hours_to_start=2, player="Test Receiver"):
    row = _snapshot(snapshot_id)
    row.update({"sport": "NFL", "stat_type": "RECEIVING_YARDS", "event_id": f"NFL:{snapshot_id[-4:]}",
                "player": player, "line": 54.5,
                "event_start_time": (NOW + timedelta(hours=hours_to_start)).isoformat()})
    return row


def _db(*snapshots, active_version="WNBA_POINTS_MODEL_V1"):
    return _Db({"wow_prop_evidence_snapshots": list(snapshots), "wow_predictions": [], "wow_outcomes": [],
                "wow_prop_fitted_model_artifacts": [
                    {"sport": "NFL", "stat_type": "RECEIVING_YARDS", "active": True,
                     "model_artifact_version": active_version}]})


def test_nfl_core_routes_use_the_generic_exact_route_scorer():
    for stat in ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"):
        assert _collector_for(route_key("NFL", stat))[0] == COLLECTOR_GENERIC


def test_pregame_snapshots_become_immutable_forward_predictions_and_past_events_do_not():
    db = _db(_nfl_snapshot("00000000-0000-0000-0000-00000000a001"),
             _nfl_snapshot("00000000-0000-0000-0000-00000000a002", hours_to_start=-3, player="Past Game"))
    out = run_pregame_forward_scoring(db, _Market(), [NFL_ROUTE], now=NOW)
    route = out["routes"][0]
    assert route["status"] == "COMPLETED" and route["captured_forward_predictions"] == 2
    assert route["row_reconciliation"]["balanced"] is True
    rows = db.tables["wow_predictions"]
    assert len(rows) == 2 and {r["player"] for r in rows} == {"Test Receiver"}
    assert all(r["evidence_source_kind"] == "IMMUTABLE_PREGAME_SETTLED" for r in rows)
    assert all(r["probability_publishable"] is False for r in rows)
    assert out["acquisition_performed"] is False and out["can_execute"] is False
    # Hourly reruns skip theses the active artifact already captured and move on.
    db.tables["wow_prop_evidence_snapshots"].append(
        _nfl_snapshot("00000000-0000-0000-0000-00000000a004", hours_to_start=5, player="Later Receiver"))
    again = run_pregame_forward_scoring(db, _Market(), [NFL_ROUTE], now=NOW, max_snapshots=1)
    assert again["routes"][0]["skipped_captured_for_active_artifact"] == 1
    assert again["captured_forward_predictions"] == 2
    assert {r["player"] for r in db.tables["wow_predictions"]} == {"Test Receiver", "Later Receiver"}
    # A new active artifact may collect its own observation on the same thesis.
    db.tables["wow_prop_fitted_model_artifacts"][0]["model_artifact_version"] = "NEW_ARTIFACT_V2"
    fresh = run_pregame_forward_scoring(db, _Market(), [NFL_ROUTE], now=NOW, max_snapshots=1)
    assert fresh["routes"][0]["skipped_captured_for_active_artifact"] == 0


class _SlowMarket(_Market):
    """Production scorer calls took ~5 s each (11-26 05:46-05:47 UTC); simulate 15 s."""

    def __init__(self, clock):
        self.clock = clock

    def score_prop(self, req, identity):
        self.clock[0] += 15.0
        return super().score_prop(req, identity)


def _rush_snapshot(snapshot_id, hours_to_start=2):
    row = _nfl_snapshot(snapshot_id, hours_to_start=hours_to_start, player=f"Rusher {snapshot_id[-1]}")
    row["stat_type"] = "RUSHING_YARDS"
    return row


def test_budget_stops_between_snapshots_and_defers_remaining_routes():
    t = [0.0]
    db = _db(*[_nfl_snapshot(f"00000000-0000-0000-0000-00000000b00{i}", player=f"Receiver {i}") for i in range(3)],
             *[_rush_snapshot(f"00000000-0000-0000-0000-00000000c00{i}") for i in range(3)])
    out = run_pregame_forward_scoring(db, _SlowMarket(t), ["MLB:PITCHER_STRIKEOUTS", NFL_ROUTE, "NFL:RUSHING_YARDS"],
                                      now=NOW, deadline_seconds=45, clock=lambda: t[0])
    by_route = {r["route"]: r for r in out["routes"]}
    assert by_route["MLB:PITCHER_STRIKEOUTS"]["status"] == "SKIPPED_NOT_GENERIC_COLLECTOR"
    receiving = by_route[NFL_ROUTE]
    assert receiving["captured_forward_predictions"] == 4  # 2 snapshots, then the budget stops it
    assert receiving["row_reconciliation"]["balanced"] is True
    assert by_route["NFL:RUSHING_YARDS"]["status"] == "DEFERRED_DEADLINE"
    assert t[0] < 45 + 2 * 15 + 1  # overshoot is at most one snapshot


def test_least_covered_route_is_scored_first():
    t = [0.0]
    db = _db(_nfl_snapshot("00000000-0000-0000-0000-00000000d001"), _rush_snapshot("00000000-0000-0000-0000-00000000d002"))
    db.tables["wow_prop_fitted_model_artifacts"].append(
        {"sport": "NFL", "stat_type": "RUSHING_YARDS", "active": True, "model_artifact_version": "WNBA_POINTS_MODEL_V1"})
    run_pregame_forward_scoring(db, _Market(), [NFL_ROUTE], now=NOW)  # receiving now has 1 captured thesis
    db.tables["wow_prop_evidence_snapshots"].append(
        _nfl_snapshot("00000000-0000-0000-0000-00000000d003", hours_to_start=4, player="Another Receiver"))
    out = run_pregame_forward_scoring(db, _SlowMarket(t), [NFL_ROUTE, "NFL:RUSHING_YARDS"], now=NOW,
                                      deadline_seconds=10, clock=lambda: t[0])
    assert [r["route"] for r in out["routes"]] == ["NFL:RUSHING_YARDS", NFL_ROUTE]
    assert out["routes"][0]["captured_forward_predictions"] == 2
    assert out["routes"][1]["status"] == "DEFERRED_DEADLINE"


def test_route_failures_are_isolated(monkeypatch):

    def boom(**kwargs):
        if kwargs["stat_type"] == "RECEIVING_YARDS":
            raise RuntimeError("scorer down")
        return {"status": "COMPLETED", "rows": [], "captured_forward_predictions": 0}

    monkeypatch.setattr("v17.prop_priority_lifecycle_runtime.run_generic_forward_route", boom)
    out = run_pregame_forward_scoring(_db(), _Market(), [NFL_ROUTE, "NFL:RUSHING_YARDS"], now=NOW)
    assert [r["status"] for r in out["routes"]] == ["FAILED", "COMPLETED"]


def test_durable_audit_scores_before_counting_and_is_unchanged_without_a_scorer():
    db = _db(_nfl_snapshot("00000000-0000-0000-0000-00000000a003"))
    req = PriorityPropDurableAuditRequest(routes=[NFL_ROUTE])
    plain = run_priority_durable_audit(req, db=db)
    assert "pregame_forward_scoring" not in plain and db.tables["wow_predictions"] == []
    scored = run_priority_durable_audit(req, db=db, market_api=_Market(), now=NOW)
    stage = scored["pregame_forward_scoring"]
    assert stage["status"] == "PASS" and stage["result"]["captured_forward_predictions"] == 2
    assert len(db.tables["wow_predictions"]) == 2
    assert scored["automatic_certification"] is False and scored["automatic_promotion"] is False
    assert scored["can_execute"] is False


def test_production_route_wires_the_existing_scorer_into_the_hourly_audit():
    from pathlib import Path

    root = Path(__file__).resolve().parent
    route = (root / "prop_forward_cohort_route.py").read_text()
    runtime = (root / "prop_priority_lifecycle_runtime.py").read_text()
    assert "db_client_fn=db_client_fn,\n        market_api=cohort_market_api,\n    )" in route
    assert "run_priority_durable_audit(req, db=db_client_fn(), market_api=market_api)" in runtime
    assert "run_universal_prop_forward_evidence" not in runtime  # no network-backed acquisition here
