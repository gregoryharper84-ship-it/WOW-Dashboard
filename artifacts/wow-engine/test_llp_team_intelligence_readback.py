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


def test_llp_action_gateway_readback_is_additive_and_authenticated():
    from pathlib import Path
    import json
    import yaml

    root=Path(__file__).resolve().parent
    schema=yaml.safe_load((root/"v17"/"openapi.llp-team-engine.v17.yaml").read_text())
    paths=schema["paths"]
    prefix="/functions/v1/wow-llp-action-gateway"
    old=paths[f"{prefix}/v17/daily-snapshot-run/{{run_id}}/rows"]["get"]
    assert old["operationId"]=="readLlpV17FullSlateRows"
    new=paths[f"{prefix}/v17/llp-team-intelligence/{{run_id}}/events"]["get"]
    assert new["operationId"]==llp.OPERATION_ID
    assert new["x-openai-isConsequential"] is False
    assert new["security"]==[{"actionBearer":[]}]
    assert new["parameters"][2]["schema"]["maximum"]==25

    gateway=(root/"v17"/"supabase"/"functions"/"wow-llp-action-gateway"/"index.ts").read_text()
    assert 'pattern: /^\\/v17\\/llp-team-intelligence\\/[^/]+\\/events$/, auth: true' in gateway
    assert 'pattern: /^\\/v17\\/daily-snapshot-run\\/[^/]+\\/rows$/, auth: true' in gateway
    vercel=json.loads((root/"v17"/"vercel"/"llp-action-gateway"/"vercel.json").read_text())
    assert any(x["source"].endswith("/v17/llp-team-intelligence/:run_id/events") for x in vercel["rewrites"])
    assert any(x["source"].endswith("/v17/daily-snapshot-run/:run_id/rows") for x in vercel["rewrites"])
    handler=(root/"v17"/"vercel"/"llp-action-gateway"/"api"/"gateway.js").read_text()
    assert '"/v17/llp-team-intelligence/__EVENTS__", { method: "GET", auth: true }' in handler
    assert '"/v17/daily-snapshot-run/__ROWS__", { method: "GET", auth: true }' in handler


def test_new_readback_requires_actual_bearer_authorization(monkeypatch):
    from fastapi import Header, HTTPException
    from fastapi.testclient import TestClient

    def require_bearer(authorization: str | None = Header(default=None)):
        if authorization != "Bearer test-only":
            raise HTTPException(status_code=401, detail="AUTH_REQUIRED")

    app=FastAPI()
    llp.install_llp_team_intelligence_readback(
        app, auth_dependency=Depends(require_bearer), db_client_fn=lambda:object()
    )
    monkeypatch.setattr(
        llp, "read_llp_team_intelligence_page",
        lambda *args, **kwargs: llp.project_llp_intelligence_page(_page([_row()])),
    )
    client=TestClient(app)
    path="/v17/llp-team-intelligence/run-test/events"
    assert client.get(path).status_code==401
    assert client.get(path,headers={"Authorization":"Bearer wrong"}).status_code==401
    success=client.get(path,headers={"Authorization":"Bearer test-only"})
    assert success.status_code==200
    assert success.json()["can_execute"] is False


def test_source_model_failure_code_is_preserved_on_held_event():
    saved = _row(certified=False)
    saved["detail"]["result"]["model_status"] = "MODEL_SCORER_FAILED"
    saved["detail"]["result"]["detail"] = {
        "blockers": ["MODEL_SCORER_FAILED:provider timeout", "MODEL_INPUTS_INSUFFICIENT"],
    }
    view = llp.project_llp_intelligence_page(_page([saved]))
    event = view["events"][0]
    assert event["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert event["source_diagnostics"]["source_model_status"] == "MODEL_SCORER_FAILED"
    assert "MODEL_SCORER_FAILED" in event["source_diagnostics"]["source_reason_codes"]
    assert "MODEL_INPUTS_INSUFFICIENT" in event["source_diagnostics"]["source_reason_codes"]
    assert "calibrated_probability" not in event["markets"][0]


def test_real_saved_nba_hold_preserves_typed_failure_reason():
    from pathlib import Path
    import json

    fixture = Path(__file__).resolve().parent / "tests" / "fixtures" / "llp_daily_nba_held_row.json"
    saved = json.loads(fixture.read_text())
    view = llp.project_llp_intelligence_page(_page([saved]))
    event = view["events"][0]
    market = event["markets"][0]
    diagnostics = event["source_diagnostics"]
    assert event["sport"] == "NBA"
    assert market["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in market
    assert diagnostics["source_model_status"] == "MODEL_UNAVAILABLE"
    # The real row stores its typed cause under result.detail.reason, not in a
    # blockers list. Losing it would make the NBA hold look like a generic gap.
    assert "NBA_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE" in diagnostics["source_reason_codes"]
    assert view["can_execute"] is False


def test_unsupported_daily_sport_is_not_counted_as_corrupt():
    other = _row(sport="SOCCER", index=1)
    view = llp.project_llp_intelligence_page(_page([_row(), other]))
    assert len(view["events"]) == 1
    assert view["unsupported_sport_rows_skipped"] == 1
    assert view["invalid_event_rows"] == 0


def test_saved_score_event_schema_translates_only_explicit_winner():
    saved = _row(certified=True)
    raw = saved["detail"]["result"]
    raw["global_terminal_authority"] = raw.pop("terminal_authority")
    raw.pop("calibrated_probability")
    raw.pop("calibrated_probability_lower_bound")
    raw.pop("calibrated_probability_upper_bound")
    raw.update(
        calibrated_home_probability=.61,
        calibrated_home_lower_bound=.56,
        calibrated_home_upper_bound=.67,
        calibrated_away_probability=.39,
        calibrated_away_lower_bound=.33,
        calibrated_away_upper_bound=.44,
    )
    projected_receipt = llp._winner_receipt_from_row(saved, saved["identity"] | {
        "event_start_time_utc": saved["identity"]["commence_time_utc"]
    })
    assert projected_receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert projected_receipt["calibrated_probability"] == .61
    # Legacy persisted rows have no certified pregame receipt ID. A model
    # probability must never manufacture one from a game or run identifier.
    raw.pop("source_receipt_id")
    view = llp.project_llp_intelligence_page(_page([saved]))
    assert view["events"][0]["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "SOURCE_RECEIPT_MISSING" in view["events"][0]["markets"][0]["reason_codes"]
    assert "calibrated_probability" not in view["events"][0]["markets"][0]


def test_no_selected_side_is_invented_from_larger_home_probability():
    saved = _row(certified=True)
    result = saved["detail"]["result"]
    result.pop("selected_side")
    result.update(calibrated_home_probability=.92, calibrated_away_probability=.08)
    view = llp.project_llp_intelligence_page(_page([saved]))
    assert view["events"][0]["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "WINNER_SIDE_INVALID" in view["events"][0]["markets"][0]["reason_codes"]


def _persisted_selection_row(sport, *, participant="DAL", include_participant=True):
    """Saved NBA/NHL schema: explicit participant plus selection triplet."""
    saved = _row(sport=sport, certified=True)
    result = saved["detail"]["result"]
    result.pop("selected_side")
    result.pop("calibrated_probability")
    result.pop("calibrated_probability_lower_bound")
    result.pop("calibrated_probability_upper_bound")
    result.update(
        calibrated_selection_probability=.61,
        calibrated_selection_lower_bound=.56,
        calibrated_selection_upper_bound=.67,
    )
    if include_participant:
        result["selected_participant"] = participant
    saved["identity"]["official_event_id"] = f"{sport.lower()}-123"
    return saved


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
def test_persisted_selection_schema_projects_governed_nba_nhl_winner(sport):
    view = llp.project_llp_intelligence_page(_page([_persisted_selection_row(sport)]))
    market = view["events"][0]["markets"][0]
    assert market["status"] == "GOVERNED_RECEIPT_VISIBLE"
    assert market["side"] == "DAL"
    assert market["calibrated_probability"] == .61
    assert market["calibrated_probability_lower_bound"] == .56
    assert view["invalid_event_rows"] == 0


def test_selection_probability_without_selected_participant_is_not_a_side():
    saved = _persisted_selection_row("NBA", include_participant=False)
    view = llp.project_llp_intelligence_page(_page([saved]))
    market = view["events"][0]["markets"][0]
    assert market["status"] == "HELD_EVIDENCE_ONLY"
    assert "WINNER_SIDE_INVALID" in market["reason_codes"]
    assert "calibrated_probability" not in market


def test_selected_participant_outside_the_event_fails_closed():
    view = llp.project_llp_intelligence_page(
        _page([_persisted_selection_row("NHL", participant="BOS")])
    )
    market = view["events"][0]["markets"][0]
    assert market["status"] == "HELD_EVIDENCE_ONLY"
    assert "WINNER_SIDE_INVALID" in market["reason_codes"]
    assert "calibrated_probability" not in market


def test_conflicting_stored_selection_identities_fail_closed():
    saved = _persisted_selection_row("NBA")
    saved["detail"]["result"]["selected_side"] = "NYG"
    view = llp.project_llp_intelligence_page(_page([saved]))
    market = view["events"][0]["markets"][0]
    assert market["status"] == "HELD_EVIDENCE_ONLY"
    assert "WINNER_SIDE_INVALID" in market["reason_codes"]
    assert "calibrated_probability" not in market
