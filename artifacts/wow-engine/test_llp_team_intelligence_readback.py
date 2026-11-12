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


def _spread_row(
    *, index=1, certified=False, side="HOME", line=-3.5, bounds=True,
    family="POINT_SPREAD", identity=None, row_status=None,
):
    status = row_status or ("COMPLETED" if certified else "HELD")
    result = {
        "market_family": family,
        "exact_line_side": side,
        "exact_signed_spread": line,
        "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "source_receipt_id": "spread-receipt-prekick-1",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "controlling_engine_identity": "LLP_TEAM_BETTING_ENGINE",
        "model_class": "CERTIFIED_FITTED" if certified else "RESEARCH_ONLY",
        "model_version": "spread-test-model",
        "calibration_version": "spread-test-calibration",
        "model_timestamp": (START-timedelta(minutes=1)).isoformat(),
        "source_snapshot_timestamp": (START-timedelta(minutes=2)).isoformat(),
        "p_cover": .52, "p_push": 0.0, "p_not_cover": .48,
        "probability_publishable": certified,
        "rank_eligible": certified,
        "can_execute": False,
    }
    if bounds:
        result["calibrated_probability_lower_bound"] = .45
        result["calibrated_probability_upper_bound"] = .59
    return {
        "lane": "SPREAD", "row_index": index, "row_status": status,
        "identity": identity or {
            "sport": "NFL", "official_event_id": "nfl-123",
            "home_team": "DAL", "away_team": "NYG",
            "commence_time_utc": START.isoformat(),
        },
        "detail": {"result": result, "probability_publishable": certified},
    }


def test_saved_governed_spread_receipt_becomes_a_qualified_card_row():
    view = llp.project_llp_intelligence_page(
        _page([_row(certified=True), _spread_row(certified=True)])
    )
    assert len(view["events"]) == 1
    event = view["events"][0]
    card = event["spread_card"]
    assert card["status"] == "QUALIFIED"
    assert card["selected_side"] == "HOME"
    assert card["exact_line"] == -3.5
    assert card["settlement_basis"] == "FULL_GAME_INCLUDING_OVERTIME"
    assert card["p_cover"] == .52 and card["p_push"] == 0.0 and card["p_not_cover"] == .48
    assert card["calibrated_probability_lower_bound"] == .45
    assert card["probability_visible"] is True
    assert card["reason_codes"] == []
    assert event["market_coverage"]["POINT_SPREAD"] == "RECEIPT_PRESENT"
    # The existing winner market is untouched and still first.
    assert [m["market_family"] for m in event["markets"]] == ["OUTRIGHT_WINNER", "POINT_SPREAD"]
    assert event["markets"][0]["status"] == "GOVERNED_RECEIPT_VISIBLE"
    assert view["spread_card_summary"]["qualified"] == 1
    assert view["spread_card_summary"]["every_event_has_spread_status"] is True
    assert view["invalid_event_rows"] == 0
    assert view["duplicate_lane_rows"] == 0
    assert view["can_execute"] is False


def test_research_spread_receipt_is_provisional_and_never_numeric():
    view = llp.project_llp_intelligence_page(_page([_spread_row(certified=False, bounds=False)]))
    card = view["events"][0]["spread_card"]
    assert card["status"] == "PROVISIONAL"
    assert card["probability_visible"] is False
    assert "p_cover" not in card and "calibrated_probability_lower_bound" not in card
    assert "MODEL_CLASS_NOT_CERTIFIED_FITTED" in card["reason_codes"]
    assert card["upstream_probability_publishable"] is False
    assert view["spread_card_summary"]["provisional"] == 1


def test_missing_spread_receipt_is_visible_not_hidden():
    view = llp.project_llp_intelligence_page(_page([_row(certified=True)]))
    card = view["events"][0]["spread_card"]
    assert card["status"] == "MISSING_RECEIPT"
    assert card["reason_codes"] == ["SPREAD_RECEIPT_NOT_SAVED_IN_DAILY_RUN"]
    assert card["exact_line"] is None and card["selected_side"] is None
    assert "p_cover" not in card
    assert view["events"][0]["market_coverage"]["POINT_SPREAD"] == "NO_EXACT_SPREAD_RECEIPT_IN_DAILY_RUN"
    assert view["spread_card_summary"]["missing_receipt"] == 1


def test_spread_without_exact_line_is_held():
    view = llp.project_llp_intelligence_page(_page([_spread_row(certified=True, line=None)]))
    card = view["events"][0]["spread_card"]
    assert card["status"] == "HELD"
    assert "SPREAD_EXACT_LINE_OR_SETTLEMENT_MISSING" in card["reason_codes"]
    assert "p_cover" not in card


def test_spread_row_with_different_participants_is_held_on_identity_mismatch():
    mismatched = _spread_row(
        certified=True,
        identity={
            "sport": "NFL", "official_event_id": "nfl-123",
            "home_team": "PHI", "away_team": "WSH",
            "commence_time_utc": START.isoformat(),
        },
    )
    view = llp.project_llp_intelligence_page(_page([_row(certified=True), mismatched]))
    assert len(view["events"]) == 1
    card = view["events"][0]["spread_card"]
    assert card["status"] == "HELD"
    assert "CROSS_MARKET_EVENT_IDENTITY_MISMATCH" in card["reason_codes"]
    assert "p_cover" not in card


def test_puck_line_row_is_held_and_never_relabelled_as_point_spread():
    view = llp.project_llp_intelligence_page(
        _page([_spread_row(certified=True, family="PUCK_LINE", line=-1.5)])
    )
    card = view["events"][0]["spread_card"]
    assert card["status"] == "HELD"
    assert card["reason_codes"] == ["SPREAD_MARKET_FAMILY_NOT_YET_GOVERNED"]
    assert card["declared_market_family"] == "PUCK_LINE"
    assert card["reported_exact_line"] == -1.5
    assert "p_cover" not in card
    assert view["events"][0]["market_coverage"]["POINT_SPREAD"] == "NO_EXACT_SPREAD_RECEIPT_IN_DAILY_RUN"


def test_two_competing_spread_theses_hold_the_card_row():
    first = _spread_row(certified=True, side="HOME", index=1)
    second = _spread_row(certified=True, side="AWAY", line=3.5, index=2)
    view = llp.project_llp_intelligence_page(_page([first, second]))
    assert view["duplicate_lane_rows"] == 1
    card = view["events"][0]["spread_card"]
    assert card["status"] == "HELD"
    assert "DUPLICATE_SPREAD_THESIS_IN_PAGE" in card["reason_codes"]
    assert card["probability_visible"] is False
    assert "p_cover" not in card


def test_real_producer_payload_written_by_daily_terminal_row_maps_exactly():
    """End-to-end mapping check against the actual producer and writer.

    No spread lane exists in Daily yet, so there is no persisted spread row to
    read. This instead builds the row the way the real code would: the challenger
    receipt from ``_common_result``, the row from ``_terminal_row``, and the saved
    ``detail`` exactly as ``persist_row_detail`` flattens it (``{**row}``).
    """
    from types import SimpleNamespace
    from v17 import daily_snapshot_runtime as daily
    from v17 import spread_forward_shadow_leagues as leagues

    artifact = SimpleNamespace(
        model_family="NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NFL_PRIOR_ONLY_V1",
        training_dataset_hash="hash-abc",
        train_rows=1200,
        calibration_rows=300,
        test_rows=300,
    )
    payload = leagues._common_result(
        sport="NFL",
        event_id="nfl-123",
        event_start_time=START.isoformat(),
        home_team="DAL",
        away_team="NYG",
        home_spread=-6.5,
        artifact=artifact,
        latest_training=START - timedelta(days=1),
        scored={
            "predicted_home_margin_center": -2.1,
            "p_cover": .52, "p_push": .03, "p_not_cover": .45,
            "p_cover_given_no_push": .53,
            "research_lower_bound_cover": .44,
            "distribution_sample_n": 500,
        },
        feature_audit={"market_features_used": False, "can_execute": False},
    )
    identity = {
        "sport": "NFL", "official_event_id": "nfl-123",
        "home_team": "DAL", "away_team": "NYG",
        "commence_time_utc": START.isoformat(),
    }
    row = daily._terminal_row("SPREAD", identity, payload, {"final_terminal": "HELD"})
    saved = {
        "run_id": "run-map", "row_index": 0, "lane": row["lane"],
        "row_status": row["row_status"], "identity": row["identity"],
        "detail": {**row},
    }
    view = llp.project_llp_intelligence_page({
        "run_id": "run-map", "status": "OK", "rows": [saved],
        "offset": 0, "returned": 1, "next_offset": None, "blockers": [],
    })
    assert len(view["events"]) == 1
    card = view["events"][0]["spread_card"]
    # Exact market identity maps through unchanged.
    assert card["selected_side"] == "HOME"
    assert card["exact_line"] == -6.5
    assert card["settlement_basis"] == "FULL_GAME_INCLUDING_OVERTIME"
    # model_family is the real field name; it must not read as missing.
    assert card["model_version"] == "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1"
    # An uncertified research receipt with no receipt id / calibrator / engine
    # identity is HELD, never QUALIFIED, and never numeric.
    assert card["status"] == "HELD"
    assert card["probability_visible"] is False
    assert "p_cover" not in card and "p_push" not in card
    # A research lower bound must never be lifted into calibrated bounds.
    assert "calibrated_probability_lower_bound" not in card
    assert "SPREAD_BOUNDS_MISSING" in card["reason_codes"]
    assert "SOURCE_RECEIPT_MISSING" in card["reason_codes"]
    assert "MODEL_CALIBRATION_PROVENANCE_MISSING" in card["reason_codes"]
    assert view["events"][0]["market_coverage"]["POINT_SPREAD"] == "RECEIPT_PRESENT"
    assert view["spread_card_summary"]["held"] == 1
    assert view["can_execute"] is False


def test_no_card_status_leaks_a_probability_or_execution():
    pages = [
        [_row(certified=True), _spread_row(certified=True)],
        [_spread_row(certified=False, bounds=False)],
        [_spread_row(certified=True, line=None)],
        [_row(certified=True)],
    ]
    for rows in pages:
        view = llp.project_llp_intelligence_page(_page(rows))
        for event in view["events"]:
            card = event["spread_card"]
            assert card["can_execute"] is False
            assert card["probability_publishable"] is False
            if card["status"] != "QUALIFIED":
                assert card["probability_visible"] is False
                assert "p_cover" not in card
            assert event["can_execute"] is False
        assert view["spread_card_summary"]["events_total"] == len(view["events"])
        assert view["spread_card_summary"]["can_execute"] is False


class _TableQuery:
    def __init__(self, rows):
        self._rows = rows
        self._slice = None

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def gte(self, *_args):
        return self

    def lte(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self._slice = (int(start), int(end))
        return self

    def upsert(self, payload, *_args, **_kwargs):
        self._rows[:] = [dict(row) for row in payload]
        return self

    def execute(self):
        data = self._rows
        if self._slice is not None:
            start, end = self._slice
            data = self._rows[start:end + 1]
            self._slice = None
        return type("Response", (), {"data": data})()


class _DualStore:
    """Minimal Supabase-shaped store with the two tables this chain touches."""

    def __init__(self):
        self.tables = {
            "wow_v17_daily_run_row_detail": [],
            "wow_market_price_observations": [],
            "wow_v17_spread_card_receipts": [],
        }

    def table(self, name):
        return _TableQuery(self.tables.setdefault(name, []))


def test_spread_lane_row_reaches_the_llp_card_end_to_end():
    """Producer -> persisted Daily row -> authenticated LLP card projection."""
    from v17.daily_snapshot_runtime import _spread_lane_rows
    from v17.daily_response_contract import persist_row_detail

    store = _DualStore()
    quote_at = (START - timedelta(hours=2)).isoformat()
    common = {
        "provider_event_id": "rd-nfl-9001",
        "sport_key": "americanfootball_nfl",
        "event_start_utc": START.isoformat(),
        "market_id": "2",
        "market_name": "spreads",
        "sportsbook": "Pinnacle",
        "price_updated_at": quote_at,
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    store.tables["wow_market_price_observations"] = [
        {**common, "participant_type": "home", "participant_name": "Dallas Cowboys", "line_value": -6.5},
        {**common, "participant_type": "away", "participant_name": "Atlanta Falcons", "line_value": 6.5},
    ]
    moneyline = [{
        "lane": "MONEYLINE",
        "identity": {
            "official_event_id": "nfl-123",
            "sport": "NFL",
            "home_team": "DAL",
            "away_team": "ATL",
            "event_start_time": START.isoformat(),
        },
        "result": {"sport": "NFL"},
    }]
    spread_rows = _spread_lane_rows(store, moneyline_rows=moneyline, requested_limit=6)
    assert len(spread_rows) == 1
    persistence = persist_row_detail(store, run_id="run-e2e", rows=spread_rows)
    assert persistence["detail_available"] is True

    page = {
        "run_id": "run-e2e",
        "status": "OK",
        "rows": store.tables["wow_v17_daily_run_row_detail"],
        "offset": 0,
        "returned": 1,
        "next_offset": None,
        "blockers": [],
    }
    view = llp.project_llp_intelligence_page(page)
    assert len(view["events"]) == 1
    event = view["events"][0]
    card = event["spread_card"]
    assert event["sport"] == "NFL"
    assert card["status"] == "HELD"
    assert card["exact_line"] == -6.5
    assert card["selected_side"] == "HOME"
    assert card["settlement_basis"] == "FULL_GAME_INCLUDING_OVERTIME"
    assert card["probability_visible"] is False
    assert "p_cover" not in card
    assert event["market_coverage"]["POINT_SPREAD"] == "RECEIPT_PRESENT"
    # The lane's own reason survives to the card's diagnostics.
    assert "SPREAD_RECEIPT_NOT_PUBLISHED" in event["source_diagnostics"]["source_reason_codes"]
    assert card["can_execute"] is False
    assert view["can_execute"] is False


def test_certified_receipt_completes_the_row_and_the_card_shows_qualified():
    """A governed receipt attached by the lane reaches the card as QUALIFIED."""
    from v17.daily_snapshot_runtime import _spread_lane_rows
    from v17.daily_response_contract import persist_row_detail

    store = _DualStore()
    quote_at = (START - timedelta(hours=2)).isoformat()
    common = {
        "provider_event_id": "rd-nfl-9101",
        "sport_key": "americanfootball_nfl",
        "event_start_utc": START.isoformat(),
        "market_id": "2",
        "market_name": "spreads",
        "sportsbook": "Pinnacle",
        "price_updated_at": quote_at,
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    store.tables["wow_market_price_observations"] = [
        {**common, "participant_type": "home", "participant_name": "Dallas Cowboys", "line_value": -6.5},
        {**common, "participant_type": "away", "participant_name": "Atlanta Falcons", "line_value": 6.5},
    ]
    moneyline = [{
        "lane": "MONEYLINE",
        "identity": {
            "official_event_id": "nfl-123",
            "sport": "NFL",
            "home_team": "DAL",
            "away_team": "ATL",
            "event_start_time": START.isoformat(),
        },
        "result": {"sport": "NFL"},
    }]
    receipt = {
        "sport": "NFL",
        "official_event_id": "nfl-123",
        "exact_line_side": "HOME",
        "exact_signed_spread": -6.5,
        "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "controlling_spread_specialist": "NFL_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
        "model_artifact_version": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "model_family": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "p_cover": 0.54, "p_push": 0.02, "p_not_cover": 0.44,
        "lower_bound_required": True,
        "calibrated_lower_bound": 0.47,
        "calibrated_upper_bound": 0.61,
        "probability_publishable": True,
        "rank_eligible": True,
        "model_timestamp": (START - timedelta(minutes=30)).isoformat(),
        "event_state": "SCHEDULED",
        "score_snapshot_status": "PASS",
        "can_execute": False,
        "source_receipt_id": "receipt-spread-1",
        "model_class": "CERTIFIED_FITTED",
        "model_version": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "calibration_version": "cal-v1",
        "source_snapshot_timestamp": (START - timedelta(minutes=40)).isoformat(),
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "controlling_engine_identity": "LLP_TEAM_BETTING_ENGINE",
    }
    spread_rows = _spread_lane_rows(
        store,
        moneyline_rows=moneyline,
        requested_limit=6,
        receipt_provider=lambda _db, **_kwargs: (dict(receipt), None),
    )
    assert spread_rows[0]["row_status"] == "COMPLETED"
    persistence = persist_row_detail(store, run_id="run-qualified", rows=spread_rows)
    assert persistence["detail_available"] is True

    view = llp.project_llp_intelligence_page({
        "run_id": "run-qualified",
        "status": "OK",
        "rows": store.tables["wow_v17_daily_run_row_detail"],
        "offset": 0,
        "returned": 1,
        "next_offset": None,
        "blockers": [],
    })
    event = view["events"][0]
    card = event["spread_card"]
    assert card["status"] == "QUALIFIED"
    assert card["exact_line"] == -6.5
    assert card["selected_side"] == "HOME"
    assert card["probability_visible"] is True
    assert card["p_cover"] == 0.54
    assert card["calibrated_probability_lower_bound"] == 0.47
    assert card["can_execute"] is False
    assert card["probability_publishable"] is False  # LLP card never grants publication
    assert view["spread_card_summary"]["qualified"] == 1
    assert view["can_execute"] is False
