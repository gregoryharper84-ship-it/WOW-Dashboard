from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from team_event_request_runtime import install_team_event_request_routes

EVENT = {"official_event_id":"123","official_date":"2026-09-02",
 "event_start_time":"2026-09-02T23:00:00+00:00","home_team":"Home","away_team":"Away",
 "venue_name":"Park","home_probable_pitcher":"H","away_probable_pitcher":"A",
 "snapshot_id":"11111111-1111-4111-8111-111111111111",
 "snapshot_timestamp":"2026-09-01T12:00:00+00:00","feature_hydration_status":"PASS"}

class Query:
    def __init__(self, rows=None, error=None): self.rows, self.error = rows, error
    def select(self,*a): return self
    def eq(self,*a): return self
    def order(self,*a,**k): return self
    def limit(self,*a): return self
    def execute(self):
        if self.error: raise self.error
        return type("R",(),{"data":self.rows})()
class DB:
    def __init__(self, rows=None, error=None): self.rows,self.error=rows,error
    def table(self,n): return Query(self.rows,self.error)
class API:
    class ScoreEventRequest:
        def __init__(self,**p): self.payload=p
    @staticmethod
    def score_event(req): return {"calibrated_home_probability":.62,"calibrated_away_probability":.38,
      "calibrated_home_lower_bound":.57,"calibrated_away_lower_bound":.33,
      "calibrated_home_upper_bound":.67,"calibrated_away_upper_bound":.43,
      "probability_publishable":True}

def row(**u):
    x={"research_run_id":"run","objective_lane":"OUTRIGHT_WIN_PROBABILITY","sport":"MLB",
       "league":"MLB","event_key":"MLB:123","event_state":"PREGAME","event_date":"2026-09-02",
       "timezone":"America/Chicago","price_required_for_objective":False}; x.update(u); return x
def client(db,api=API):
    app=FastAPI(); install_team_event_request_routes(app,auth_dependency=Depends(lambda: None),db_client_fn=lambda:db,event_api=api)
    return TestClient(app)

def test_market_failure_does_not_erase_sporting_probability():
    b=client(DB([EVENT])).post("/score-team-event-request",json={"rows":[row(objective_lane="MARKET_EDGE",price_required_for_objective=True)]}).json()
    assert b["rows"][0]["calibrated_probability"]==.62
    assert b["rows"][0]["blockers"]==["MARKET_DATA_UNOBTAINABLE"]
    assert b["can_execute"] is False
def test_connector_failure_taxonomy():
    b=client(DB(error=RuntimeError())).post("/score-team-event-request",json={"rows":[row()]}).json()
    assert b["rows"][0]["code"]=="PROVIDER_UNAVAILABLE" and b["reconciliation_pass"]
def test_one_row_failure_is_partial():
    class Partial(API):
        calls=0
        @classmethod
        def score_event(c,r):
            c.calls+=1
            if c.calls==2: raise RuntimeError()
            return API.score_event(r)
    b=client(DB([EVENT]),Partial).post("/score-team-event-request",json={"rows":[row(research_run_id="a"),row(research_run_id="b")]}).json()
    assert b["run_status"]=="RUN_PARTIAL" and b["rows_completed"]==1 and len(b["rows"])==2
    assert b["rows"][1]["code"]=="TRANSPORT_FAILURE"
def test_missing_specialist_is_model_unavailable():
    b=client(DB([EVENT])).post("/score-team-event-request",json={"rows":[row(sport="NFL",league="NFL",event_key="NFL:1")]}).json()
    assert b["rows"][0]["code"]=="MODEL_UNAVAILABLE"
