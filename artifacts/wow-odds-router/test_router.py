import os
import pytest
from fastapi.testclient import TestClient
import app

class R:
    def __init__(self,status=200,payload=None): self.status_code=status; self._payload={} if payload is None else payload
    def json(self): return self._payload

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("WOW_ODDS_ROUTER_ACTION_KEY","OPTICODDS_API_KEY"):
        monkeypatch.delenv(k,raising=False)

@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_ROUTER_ACTION_KEY","router-secret")
    return {"Authorization":"Bearer router-secret"}

def test_health_is_evidence_only_and_non_executing():
    c=TestClient(app.app); body=c.get('/odds-api/health').json()
    assert body['provider_authority']=='EVIDENCE_ONLY'; assert body['can_execute'] is False

def test_primary_success_passes_through(monkeypatch,auth):
    monkeypatch.setattr(app,'_primary_get',lambda *a,**k:R(200,[{'key':'baseball_mlb','active':True}]))
    c=TestClient(app.app); r=c.get('/odds-api/v4/sports',headers=auth)
    assert r.status_code==200; assert r.headers['x-wow-odds-provider']=='THE_ODDS_API'; assert r.headers['x-wow-odds-failover']=='false'

def test_primary_401_without_backup_fails_closed(monkeypatch,auth):
    monkeypatch.setattr(app,'_primary_get',lambda *a,**k:R(401,{'code':'PRIMARY_FAILED','can_execute':False}))
    c=TestClient(app.app); r=c.get('/odds-api/v4/sports',headers=auth)
    assert r.status_code==401; assert r.json()['can_execute'] is False

def test_primary_401_with_backup_returns_mapped_sports(monkeypatch,auth):
    monkeypatch.setenv('OPTICODDS_API_KEY','optic-secret')
    monkeypatch.setattr(app,'_primary_get',lambda *a,**k:R(401,{'code':'PRIMARY_FAILED'}))
    c=TestClient(app.app); r=c.get('/odds-api/v4/sports',headers=auth)
    assert r.status_code==200; assert r.headers['x-wow-odds-provider']=='OPTICODDS'; assert any(x['key']=='baseball_mlb' for x in r.json())

def test_events_failover_tags_provider_identity(monkeypatch,auth):
    monkeypatch.setenv('OPTICODDS_API_KEY','optic-secret')
    monkeypatch.setattr(app,'_primary_get',lambda *a,**k:R(503,{}))
    monkeypatch.setattr(app,'_optic_get',lambda *a,**k:R(200,{'data':[{'id':'FX1','start_date':'2026-09-12T00:00:00Z','home_team_display':'A','away_team_display':'B'}]}))
    c=TestClient(app.app); r=c.get('/odds-api/v4/sports/baseball_mlb/events',headers=auth)
    row=r.json()[0]; assert row['id']=='optic__FX1'; assert row['source_provider']=='OPTICODDS'; assert row['provider_event_id']=='FX1'

def test_optic_odds_normalize_without_probability_authority():
    payload={'data':[{'id':'FX1','start_date':'2026-09-12T00:00:00Z','home_team_display':'A','away_team_display':'B','odds':[{'id':'o1','sportsbook':'BetMGM','market':'Moneyline','market_id':'moneyline','selection':'A','price':-120,'points':None,'player_id':None}]}]}
    out=app._normalize_optic_odds(payload,'baseball_mlb','FX1')
    assert out['source_provider']=='OPTICODDS'; assert out['bookmakers'][0]['markets'][0]['key']=='h2h'; assert 'probability' not in str(out).lower()

def test_prop_market_id_is_preserved_not_promoted():
    assert app._optic_market_to_wow('player_passing_yards','americanfootball_nfl')=='player_passing_yards'

def test_unauthorized_router_request_is_blocked(monkeypatch):
    monkeypatch.setenv('WOW_ODDS_ROUTER_ACTION_KEY','router-secret')
    monkeypatch.setattr(app,'verify_github_actions_oidc',lambda token: (_ for _ in ()).throw(app.GitHubOIDCValidationError('bad')))
    c=TestClient(app.app); assert c.get('/odds-api/v4/sports').status_code==401
