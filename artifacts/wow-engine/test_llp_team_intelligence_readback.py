"""Regress LLP readback without changing existing NFL Pick'em or Daily routes."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI

from v17 import llp_team_intelligence_readback as llp
from v17 import daily_snapshot_runtime as daily

START = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)


def _row(*, sport="NFL", index=0, certified=False):
    status = "COMPLETED" if certified else "HELD"
    result = {
        "probability_publishable": certified,
        "rank_eligible": certified,
        "selected_side": "DAL",
        "source_receipt_id": "receipt-prekick-1",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "controlling_engine_identity": "LLP_TEAM_BETTING_ENGINE",
        "model_class": "CERTIFIED_FITTED" if certified else "RESEARCH_ONLY",
        "model_version": "test-model",
        "calibration_version": "test-calibration",
        "model_timestamp": (START-timedelta(minutes=1)).isoformat(),
        "source_snapshot_timestamp": (START-timedelta(minutes=2)).isoformat(),
        "calibrated_probability": .61,
        "calibrated_probability_lower_bound": .56,
        "calibrated_probability_upper_bound": .67,
        "can_execute": False,
    }
    return {
        "lane": "MONEYLINE", "row_index": index, "row_status": status,
        "identity": {
            "sport": sport, "official_event_id": "nfl-123",
            "home_team": "DAL", "away_team": "NYG",
            "commence_time_utc": START.isoformat(),
        },
        "detail": {"result": result, "probability_publishable": certified},
    }


def _page(rows):
    return {
        "run_id": "run-123", "status": "OK", "rows": rows,
        "offset": 0, "returned": len(rows), "next_offset": None, "blockers": [],
    }


@pytest.mark.parametrize("sport", ["NFL", "NBA", "NHL"])
def test_all_three_sports_get_read_only_winner_context(sport):
    value = llp.project_llp_intelligence_page(_page([_row(sport=sport, certified=True)]))
    assert len(value["events"]) == 1
    event = value["events"][0]
    assert event["sport"] == sport
    assert event["markets"][0]["status"] == "GOVERNED_RECEIPT_VISIBLE"
    assert event["market_coverage"]["POINT_SPREAD"] == "NO_EXACT_SPREAD_RECEIPT_IN_DAILY_RUN"
    assert event["market_coverage"]["GAME_TOTAL"] == "NO_EXACT_TOTAL_RECEIPT_IN_DAILY_RUN"
    assert value["source_mutated"] is False and value["nfl_pickem_mutated"] is False


def test_research_candidate_keeps_numeric_probability_hidden():
    view = llp.project_llp_intelligence_page(_page([_row(certified=False)]))
    result = view["events"][0]["markets"][0]
    assert result["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in result
    assert view["board_complete"] is False


def test_props_lanes_not_routed_as_llp_team_events():
    view = llp.project_llp_intelligence_page(_page([
        {"lane":"PROPS", "row_index":0},_row(index=1),
    ]))
    assert len(view["events"]) == 1
    assert view["non_team_event_rows_skipped"] == 1


def test_daily_readback_unavailability_preserves_blockers():
    view = llp.project_llp_intelligence_page({
        "run_id":"r", "status":"DETAIL_UNAVAILABLE", "rows":[],
        "returned":0, "blockers":["DAILY_ROW_DETAIL_RETRIEVAL_UNAVAILABLE:TimeoutError"],
    })
    assert view["status"] == "DETAIL_UNAVAILABLE"
    assert view["events"] == []
    assert len(view["blockers"]) == 1
    assert view["board_complete"] is False


def test_read_is_delegated_once_and_never_writes(monkeypatch):
    calls=[]
    def fake_read(db, **args):
        calls.append((db,args))
        return _page([_row(certified=True)])
    monkeypatch.setattr(llp,"read_row_detail_page",fake_read)
    db=object()
    view=llp.read_llp_team_intelligence_page(db, run_id="test-run", offset=0, limit=5)
    assert len(calls)==1 and calls[0][0] is db
    assert view["events"][0]["official_event_id"]=="nfl-123"


@pytest.mark.parametrize("args",[
    {"run_id":""},{"run_id":"r","offset":-1},
    {"run_id":"r","limit":0},{"run_id":"r","limit":26},
])
def test_invalid_readback_pagination_is_rejected(args):
    with pytest.raises(ValueError):
        llp.read_llp_team_intelligence_page(object(),**args)


def _paths(app):
    return [route.path for route in app.router.routes]


def _route(app, path):
    return next(route for route in app.router.routes if route.path==path)


def test_readback_installs_authenticated_and_idempotent():
    app=FastAPI()
    dependency=Depends(lambda: None)
    assert llp.install_llp_team_intelligence_readback(
        app,auth_dependency=dependency,db_client_fn=lambda:None
    ) is True
    assert llp.install_llp_team_intelligence_readback(
        app,auth_dependency=dependency,db_client_fn=lambda:None
    ) is False
    route=_route(app,llp.ROUTE_PATH)
    assert route.operation_id==llp.OPERATION_ID
    assert len(route.dependencies)==1


def test_existing_nfl_pickem_route_stays_intact():
    app=FastAPI()
    calls=[]
    @app.get("/v17/nfl-pickem-board")
    def existing_pickem():
        calls.append("nfl_unchanged")
        return {"status":"still_active"}
    pickem_before=_route(app,"/v17/nfl-pickem-board").endpoint
    llp.install_llp_team_intelligence_readback(
        app,auth_dependency=Depends(lambda:None),db_client_fn=lambda:None
    )
    assert _route(app,"/v17/nfl-pickem-board").endpoint is pickem_before
    assert pickem_before()=={"status":"still_active"}
    assert calls==["nfl_unchanged"]


def test_daily_installer_off_by_default_preserves_original_routes(monkeypatch):
    monkeypatch.delenv("WOW_LLP_TEAM_INTELLIGENCE_READBACK_ENABLED",raising=False)
    monkeypatch.setattr(daily,"install_v17_detailed_evidence",lambda *a,**k:None)
    monkeypatch.setattr(daily,"install_prop_forward_cohort_route",lambda *a,**k:None)
    monkeypatch.setattr(daily,"_install_prop_evidence_acquisition_scheduler",lambda *a,**k:None)
    app=FastAPI()
    daily.install_daily_snapshot_route(
        app,auth_dependency=Depends(lambda:None),
        db_client_fn=lambda:None,market_api=None,event_api=None,
    )
    assert llp.ROUTE_PATH not in _paths(app)
    assert "/v17/daily-snapshot-run" in _paths(app)
    assert "/v17/daily-snapshot-run/{run_id}/rows" in _paths(app)


def test_daily_installer_opt_in_adds_only_new_readback(monkeypatch):
    monkeypatch.setenv("WOW_LLP_TEAM_INTELLIGENCE_READBACK_ENABLED","1")
    monkeypatch.setattr(daily,"install_v17_detailed_evidence",lambda *a,**k:None)
    monkeypatch.setattr(daily,"install_prop_forward_cohort_route",lambda *a,**k:None)
    monkeypatch.setattr(daily,"_install_prop_evidence_acquisition_scheduler",lambda *a,**k:None)
    app=FastAPI()
    daily.install_daily_snapshot_route(
        app,auth_dependency=Depends(lambda:None),
        db_client_fn=lambda:None,market_api=None,event_api=None,
    )
    assert llp.ROUTE_PATH in _paths(app)
    assert "/v17/daily-snapshot-run" in _paths(app)
    assert "/v17/daily-snapshot-run/{run_id}/rows" in _paths(app)
