"""Isolated no-network tests for source-safe NHL forward candidate features."""
from __future__ import annotations
import importlib.util
import json
from datetime import datetime,timezone,timedelta
from email.utils import format_datetime
from pathlib import Path
import pytest

P=Path(__file__).resolve().parents[1] / 'scripts' / 'nhl_forward_candidate_feature_hydrator.py'
spec=importlib.util.spec_from_file_location('nhlforward',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NOW=datetime(2026,10,22,20,40,tzinfo=timezone.utc)


def fake_data(*,history_rounds=6,conflicting=False,recent=False):
    teams=m.ALL_TEAMS_2026
    schedules={t:[] for t in teams}
    # 32 clubs, each plays once per round; 6 historical games per club.
    for rd in range(history_rounds):
        for i in range(0,32,2):
            home=teams[(i+rd)%32];away=teams[(i+rd+1)%32]
            # each team appears exactly twice per date? schedule may cause repeats across i, correct pairing modulo 32.
            event={"id":str(2026020000+rd*16+i//2),"season":20262027,"gameType":2,"gameState":"OFF",
                   "startTimeUTC":(NOW-timedelta(days=20-rd*2)).isoformat(),
                   "homeTeam":{"abbrev":home,"score":3+(i%2)},"awayTeam":{"abbrev":away,"score":2},"neutralSite":False}
            schedules[home].append(event.copy());schedules[away].append(event.copy())
    if recent:
        t1,t2=teams[:2]
        event={"id":"90009","season":20262027,"gameType":2,"gameState":"FINAL",
            "startTimeUTC":(NOW-timedelta(hours=1)).isoformat(),"homeTeam":{"abbrev":t1,"score":2},"awayTeam":{"abbrev":t2,"score":1}}
        schedules[t1].append(event.copy());schedules[t2].append(event.copy())
    cross={"status":"IDENTITY_RECONCILIATION_RESEARCH_ONLY","can_execute":False,"probability_publishable":False,"rows":[]}
    for i in range(12):
        home,away=teams[i*2],teams[i*2+1]
        start=NOW+timedelta(hours=8)
        event={"id":str(2026020154+i),"season":20262027,"gameType":2,"gameState":"FUT",
             "startTimeUTC":start.isoformat(),"homeTeam":{"abbrev":home},"awayTeam":{"abbrev":away},"neutralSite":False}
        schedules[home].append(event.copy());schedules[away].append(event.copy())
        cross["rows"].append({"status":"NHL_CANONICAL_EVENT_IDENTITY_RECONCILED","can_execute":False,
              "espn_event_id":str(401892552+i),"nhl_official_event_id":str(2026020154+i),
              "event_start_utc":start.isoformat(),"nhl_official_start_utc":start.isoformat(),
              "nhl_home_abbreviation":home,"nhl_away_abbreviation":away})
    if conflicting:
        first=schedules[teams[0]][0]
        first["homeTeam"]={"abbrev":first["homeTeam"]["abbrev"],"score":99}
    return cross,schedules

class R:
    def __init__(self,raw,url):
        self.data=raw;self.status=200;self.url=url
        self.headers={"Content-Type":"application/json;charset=UTF-8","Date":format_datetime(NOW,usegmt=True)}
    def geturl(self):return self.url
    def read(self,n):return self.data[:n]
    def __enter__(self):return self
    def __exit__(self,*args):pass

class Opener:
    def __init__(self,schedules): self.schedules=schedules
    def open(self,req,timeout):
        url=req.full_url;team=url.split('/')[5]
        return R(json.dumps({"games":self.schedules[team]}).encode(),url)


def test_12_candidate_feature_rows_no_scoring(tmp_path):
    cross,sched=fake_data()
    r=m.run(crosswalk=cross,outdir=tmp_path/'evidence',opener=Opener(sched),observed_at=NOW)
    assert r['rows_total']==12 and r['candidate_rows']==12
    assert len(r['source_manifest'])==32
    assert r['can_execute'] is False and r['probability_publishable'] is False
    assert r['calibration_certified'] is False and r['goaltender_status_attested'] is False
    for row in r['rows']:
        assert tuple(row['features'])==m.FEATURE_NAMES
        assert row['feature_schema_version']==m.FEATURE_SCHEMA_VERSION
        assert row['model_artifact_id'] is None
        assert row['home_prior_games']>=5 and row['away_prior_games']>=5
    assert (tmp_path/'evidence'/'nhl_BOS_20262027_raw.json').exists()


def test_recent_final_state_not_retroactively_claimed():
    cross,sched=fake_data(recent=True)
    with pytest.raises(m.ForwardFeatureBlocked, match='NHL_RECENT_GAME_SETTLEMENT_NOT_ATTESTED'):
        m.parse_history({k:{"games":v} for k,v in sched.items()},observed_at=NOW)


def test_conflicting_historical_team_copy_rejected():
    _,sched=fake_data(conflicting=True)
    with pytest.raises(m.ForwardFeatureBlocked,match='NHL_PRIOR_GAME_MISSING_OR_CONFLICTING_CLUB_COPY'):
        m.parse_history({k:{"games":v} for k,v in sched.items()},observed_at=NOW)


def test_missing_prior_game_history_blocks(tmp_path):
    cross,sched=fake_data(history_rounds=4)
    r=m.run(crosswalk=cross,outdir=tmp_path/'evidence',opener=Opener(sched),observed_at=NOW)
    assert r['candidate_rows']==0
    assert all(row['status']=='NHL_PRIOR_GAMES_INSUFFICIENT' for row in r['rows'])


def test_crosswalk_duplicate_cannot_score():
    cross,_=fake_data();cross['rows'][1]['nhl_official_event_id']=cross['rows'][0]['nhl_official_event_id']
    with pytest.raises(m.ForwardFeatureBlocked,match='NHL_CROSSWALK_IDENTITY_DUPLICATE'):
        m.validate_crosswalk(cross)


def test_unverified_crosswalk_fails():
    cross,_=fake_data();cross['status']='CERTIFIED_MODEL_SUPPORTED'
    with pytest.raises(m.ForwardFeatureBlocked,match='NHL_CROSSWALK_UNVERIFIED'):
        m.validate_crosswalk(cross)


def test_wrong_source_team_universe_fails(tmp_path):
    cross,sched=fake_data()
    with pytest.raises(m.ForwardFeatureBlocked,match='NHL_HISTORY_TEAM_UNIVERSE_INCOMPLETE'):
        m.run(crosswalk=cross,outdir=tmp_path/'evidence',opener=Opener(sched),observed_at=NOW,team_universe=('BOS','NSH'))


def test_near_kickoff_blocks_before_network(tmp_path):
    cross,sched=fake_data()
    with pytest.raises(m.ForwardFeatureBlocked,match='NHL_ANY_EVENT_NO_LONGER_PREGAME'):
        m.run(crosswalk=cross,outdir=tmp_path/'evidence',opener=Opener(sched),observed_at=NOW+timedelta(hours=7,minutes=59))