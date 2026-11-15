"""Fail-closed LLP <-> WOW Kalshi research relay regressions."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from v17.kalshi_slip_intelligence_exchange import (
    CAN_EXECUTE, CAN_ALLOCATE_CAPITAL, ExchangeHold, ROUTE,
    install_kalshi_slip_research_routes, normalize_research_observation,
    publish_research, read_research,
)

SAMPLE = {
    "source_host": "LLP_TEAM_BETTING_ENGINE", "scope": "EVENT", "sport": "NBA",
    "reference_id": "nba-2026-ev-1", "market_ref": "KXNBA-ML-YES",
    "finding_type": "SCHEDULE_REST_TRAVEL", "finding_code": "ROAD_BACK_TO_BACK_OBSERVED",
    "analysis_stage": "PREGAME_RESEARCH", "as_of_utc": "2026-11-14T18:00:00Z",
    "source_observed_at_utc": "2026-11-14T17:00:00Z",
    "event_start_utc": "2026-11-15T00:00:00Z",
    "evidence_refs": ["official-schedule-123", "rest-derived-44"],
}

class FakeQuery:
    def __init__(self, store): self.store=store; self.current={}
    def upsert(self, row, **kw):
        assert kw == {"on_conflict":"record_id", "ignore_duplicates":True}
        self.store.setdefault(row["record_id"],dict(row)); return self
    def select(self, fields):
        assert "calibrated_probability" not in fields
        self.fields=fields.split(","); return self
    def eq(self, key, val): self.current[key]=val; return self
    def order(self, *a, **kw): return self
    def limit(self, n): self.n=n; return self
    def execute(self):
        if hasattr(self,"fields"):
            rows=[{key:r.get(key) for key in self.fields} for r in self.store.values() if all(r.get(k)==v for k,v in self.current.items())]
            return SimpleNamespace(data=rows[:self.n])
        return SimpleNamespace(data=[])

class FakeDB:
    def __init__(self):self.store={}
    def table(self, name):
        assert name == "wow_kalshi_slip_research_exchange"; return FakeQuery(self.store)


def test_two_way_llp_produces_wow_consumes_and_reverse():
    db=FakeDB()
    a=publish_research(db,SAMPLE)
    b=publish_research(db,{**SAMPLE,"source_host":"WOW_BETTING_ENGINE", "finding_type":"DEPENDENCY_CORRELATION", "finding_code":"SHARED_GAME_SCRIPT_DEPENDENCY"})
    for consumer in ("LLP_TEAM_BETTING_ENGINE","WOW_BETTING_ENGINE"):
        found=read_research(db,consumer_host=consumer,reference_id="nba-2026-ev-1")
        assert {r["source_host"] for r in found["entries"]} == {"LLP_TEAM_BETTING_ENGINE","WOW_BETTING_ENGINE"}
        assert not found["model_feature_eligible"] and not found["can_execute"]
    assert a["record_id"] != b["record_id"]


def test_exact_retry_is_idempotent_and_immutable():
    db=FakeDB(); publish_research(db,SAMPLE); publish_research(db,SAMPLE)
    assert len(db.store)==1
    assert list(db.store.values())[0]["evidence_refs"]==sorted(SAMPLE["evidence_refs"])

@pytest.mark.parametrize("bad",[
    {"calibrated_probability": .93}, {"can_execute":True},
    {"user_order_id":"secret"}, {"total_cost":99.99},
    {"raw_kalshi_account":"123"}, {"model_feature_eligible":True},
])
def test_probability_money_and_private_account_data_rejected(bad):
    with pytest.raises(ExchangeHold, match="EXCHANGE_SCHEMA_OR_FORBIDDEN_FIELD"):
        normalize_research_observation({**SAMPLE,**bad})


def test_post_event_pregame_leakage_rejected():
    with pytest.raises(ExchangeHold,match="EXCHANGE_PREGAME_CUTOFF_INVALID"):
        normalize_research_observation({**SAMPLE,"as_of_utc":"2026-11-15T01:00:00Z"})


def test_forged_future_observation_rejected():
    with pytest.raises(ExchangeHold,match="EXCHANGE_CHRONOLOGY_INVALID"):
        normalize_research_observation({**SAMPLE,"source_observed_at_utc":"2026-11-15T02:00:00Z"})


def test_historical_cohort_supports_visible_eight_without_fake_event_id():
    doc={**SAMPLE, "scope":"COHORT","sport":"MIXED","reference_id":"KALSHI_VISIBLE_20261114",
         "market_ref":None,"event_start_utc":None,"analysis_stage":"POSTMORTEM_RESEARCH",
         "finding_type":"SLIP_FRAGILITY","finding_code":"LARGE_COMBO_STAKE_CONCENTRATION"}
    row=normalize_research_observation(doc)
    assert row["scope"]=="COHORT" and row["event_start_utc"] is None
    assert not row["probability_publishable"]


def test_historical_review_must_not_claim_pregame_cohort():
    with pytest.raises(ExchangeHold,match="EXCHANGE_PREGAME_CUTOFF_INVALID"):
        normalize_research_observation({**SAMPLE,"scope":"COHORT","sport":"MIXED","market_ref":None,"event_start_utc":None})


def test_source_host_is_only_a_claim_even_after_authenticated_route():
    row=normalize_research_observation(SAMPLE)
    assert row["source_host_attested"] is False
    assert row["evidence_status"] == "UNVERIFIED_RESEARCH"
    assert not row["model_feature_eligible"]


def test_invalid_consumer_or_out_of_range_limit_rejected():
    db=FakeDB()
    for args in ({"consumer_host":"UNKNOWN"},{"consumer_host":"WOW_BETTING_ENGINE","limit":101},
                 {"consumer_host":"LLP_TEAM_BETTING_ENGINE","reference_id":"@bad"}):
        with pytest.raises(ExchangeHold): read_research(db,**args)


def test_route_authenticated_idempotent_and_additive():
    app=FastAPI()
    @app.get("/v17/nfl-pickem-board")
    def pickem():return {"status":"unaltered"}
    before=next(r.endpoint for r in app.routes if r.path=="/v17/nfl-pickem-board")
    assert install_kalshi_slip_research_routes(app,auth_dependency=lambda:None,get_client_fn=FakeDB)
    assert install_kalshi_slip_research_routes(app,auth_dependency=lambda:None,get_client_fn=FakeDB) is False
    routes=[r for r in app.routes if r.path==ROUTE]
    assert len(routes)==2
    assert {r.operation_id for r in routes}=={"publishWowV17KalshiSlipResearch","readWowV17KalshiSlipResearch"}
    assert all(len(r.dependencies)==1 for r in routes)
    assert next(r.endpoint for r in app.routes if r.path=="/v17/nfl-pickem-board") is before


def test_sql_security_and_immutability_contract():
    path=Path(__file__).resolve().parent/"migrations/20261114_kalshi_slip_research_exchange.sql"
    sql=path.read_text()
    for required in ("ENABLE ROW LEVEL SECURITY", "REVOKE ALL", "GRANT SELECT, INSERT", "BEFORE UPDATE OR DELETE", "CHECK (can_execute=false)", "CHECK (model_feature_eligible=false)", "CHECK (probability_publishable=false)"):
        assert required in sql
    assert not CAN_EXECUTE and not CAN_ALLOCATE_CAPITAL


def test_http_cross_engine_authentication_and_round_trip():
    from fastapi import Header, HTTPException
    from fastapi.testclient import TestClient
    db=FakeDB()
    def require_key(authorization: str | None = Header(None)):
        if authorization != "Bearer test-only":
            raise HTTPException(status_code=401, detail="AUTH_REQUIRED")
    app=FastAPI()
    install_kalshi_slip_research_routes(app,auth_dependency=require_key,get_client_fn=lambda:db)
    client=TestClient(app)
    assert client.post(ROUTE,json=SAMPLE).status_code == 401
    assert client.get(ROUTE,params={"consumer_host":"WOW_BETTING_ENGINE"}).status_code == 401
    response=client.post(ROUTE,headers={"Authorization":"Bearer test-only"},json=SAMPLE)
    assert response.status_code == 202
    assert not response.json()["can_execute"]
    consumed=client.get(ROUTE,headers={"Authorization":"Bearer test-only"},params={"consumer_host":"WOW_BETTING_ENGINE"})
    assert consumed.status_code==200 and consumed.json()["entry_count"]==1
    assert consumed.json()["entries"][0]["source_host"]=="LLP_TEAM_BETTING_ENGINE"


def test_http_bad_input_is_422_no_persistence():
    from fastapi.testclient import TestClient
    db=FakeDB();app=FastAPI()
    install_kalshi_slip_research_routes(app,auth_dependency=lambda:None,get_client_fn=lambda:db)
    response=TestClient(app).post(ROUTE,json={**SAMPLE,"calibrated_probability":0.8})
    assert response.status_code==422 and response.json()["detail"]["code"]=="EXCHANGE_SCHEMA_OR_FORBIDDEN_FIELD"
    assert not db.store


def test_http_database_failure_is_503_no_fabricated_success():
    from fastapi.testclient import TestClient
    app=FastAPI()
    def broken_db(): raise RuntimeError("secret database exception")
    install_kalshi_slip_research_routes(app,auth_dependency=lambda:None,get_client_fn=broken_db)
    response=TestClient(app).post(ROUTE,json=SAMPLE)
    assert response.status_code==503
    assert "secret database exception" not in response.text
    assert response.json()["detail"]["code"]=="EXCHANGE_PERSISTENCE_UNAVAILABLE"
