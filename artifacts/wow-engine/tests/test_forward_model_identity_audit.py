"""No-network negative and positive tests of the source-to-model boundary."""
import copy
from datetime import datetime, timezone
import hashlib, importlib.util, io, json, zipfile
from pathlib import Path
import pytest

P=Path(__file__).resolve().parents[1] / 'scripts/forward_model_identity_audit.py'
spec=importlib.util.spec_from_file_location('audit',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
DATE='2026-10-24'; NHLDATE='2026-10-22'

def build(tmp_path):
    fixture={'schema_version':m.SCHEMA,'can_execute':False,'probability_publishable':False,
        'full_slate_completeness_attested':False,'sports':{}}
    sources={}
    for sport,date in [('NCAAF',DATE),('NHL',NHLDATE)]:
        event={'id':('123' if sport=='NCAAF' else '456'),'date':'2026-10-24T16:00:00Z',
               'status':{'type':{'state':'pre'}},'competitions':[{'competitors':[
                   {'homeAway':'home','team':{'id':'1','displayName':'Home'}},
                   {'homeAway':'away','team':{'id':'2','displayName':'Away'}}]}]}
        raw=json.dumps({'events':[event]}).encode();sha=hashlib.sha256(raw).hexdigest()
        row={'espn_event_id':event['id'],'event_start_utc':'2026-10-24T16:00:00+00:00',
             'home_espn_team_id':'1','away_espn_team_id':'2','home_name':'Home','away_name':'Away'}
        received={'event_id':event['id'],'event_start_utc':row['event_start_utc'],
                  'home':{'team_id':'1'},'away':{'team_id':'2'}}
        receipt={'raw_sha256':sha,'raw_bytes':len(raw),'sport':sport,'target_date':date,
                 'can_execute':False,'probability_publishable':False,'full_slate_completeness_attested':False,
                 'capture_host_clock_utc':'2026-10-22T15:00:00+00:00','events_pregame_eligible':[received]}
        fixture['sports'][sport]={'target_date':date,'raw_sha256':sha,'events':[row]}
        name=sport.lower();root=f'{name}/original/{name}_{date}_espn';sources[root+'_raw.json']=raw;sources[root+'_capture_receipt.json']=json.dumps(receipt).encode()
    path=tmp_path/'a.zip'
    with zipfile.ZipFile(path,'w') as z:
        for name,data in sources.items():z.writestr(name,data)
    return fixture,path,sources

def test_audit_frozen_capture_without_model_input(tmp_path):
    f,p,_=build(tmp_path);result=m.audit(f,p)
    assert result['event_count']==2
    assert result['blocker_counts']=={'NCAAF_FEATURE_SNAPSHOT_NOT_FOUND':1,'NHL_CANONICAL_FIRST_PARTY_IDENTITY_NOT_RECONCILED':1}
    assert result['probability_publishable'] is False and result['can_execute'] is False

def test_raw_hash_tamper_fails_closed(tmp_path):
    f,p,s=build(tmp_path);name='nhl/original/nhl_2026-10-22_espn_raw.json';s[name]=b'{}'
    with zipfile.ZipFile(p,'w') as z:
        for key,data in s.items():z.writestr(key,data)
    with pytest.raises(m.AuditFailure,match='SOURCE_RAW_HASH_MISMATCH'):m.audit(f,p)

def test_frozen_team_swap_fails_closed(tmp_path):
    f,p,_=build(tmp_path);f['sports']['NHL']['events'][0]['home_espn_team_id']='2'
    with pytest.raises(m.AuditFailure,match='SOURCE_TEAM_IDENTITY_MISMATCH'):m.audit(f,p)

def test_frozen_event_id_swap_fails_closed(tmp_path):
    f,p,_=build(tmp_path);f['sports']['NHL']['events'][0]['espn_event_id']='789'
    with pytest.raises(m.AuditFailure,match='SOURCE_EVENT_SET_MISMATCH'):m.audit(f,p)

def snapshot(f):
    e=f['sports']['NCAAF']['events'][0]
    snap={'official_event_id':e['espn_event_id'],'feature_schema_version':m.NCAAF_SCHEMA,
          'home_team':e['home_name'],'away_team':e['away_name'],'event_start_time':e['event_start_utc'],
          'feature_as_of':'2026-10-23T18:00:00Z','injury_evidence_timestamp':'2026-10-23T17:00:00Z',
          'market_timestamp':None,'neutral_site':False,'can_execute':False}
    snap.update({key:1.0 for key in m.NCAAF_NUMERIC_RAW})
    return snap

def test_structural_ncaaf_snapshot_does_not_claim_certification(tmp_path):
    f,p,_=build(tmp_path);r=m.audit(f,p,[snapshot(f)])
    assert r['blocker_counts']['NCAAF_SNAPSHOT_STRUCTURE_READY_UNCERTIFIED']==1
    assert not r['feature_consumption_verified']

def test_snapshot_event_mismatch_rejected(tmp_path):
    f,p,_=build(tmp_path);s=snapshot(f);s['official_event_id']='wrong'
    assert m.validate_ncaaf_snapshot(f['sports']['NCAAF']['events'][0],s)=='NCAAF_EVENT_IDENTITY_MISMATCH'

def test_snapshot_wrong_team_rejected(tmp_path):
    f,p,_=build(tmp_path);s=snapshot(f);s['home_team']='Away'
    assert m.validate_ncaaf_snapshot(f['sports']['NCAAF']['events'][0],s)=='NCAAF_TEAM_NAME_IDENTITY_UNATTESTED'

def test_snapshot_future_leakage_rejected(tmp_path):
    f,p,_=build(tmp_path);s=snapshot(f);s['feature_as_of']=s['event_start_time']
    assert m.validate_ncaaf_snapshot(f['sports']['NCAAF']['events'][0],s)=='NCAAF_FEATURE_TIMESTAMP_NOT_PREGAME'

def test_snapshot_invalid_feature_rejected(tmp_path):
    f,p,_=build(tmp_path);s=snapshot(f);s['home_off_epa']=True
    assert m.validate_ncaaf_snapshot(f['sports']['NCAAF']['events'][0],s)=='NCAAF_REQUIRED_RAW_FEATURE_INVALID'

def test_no_publishability_flag_allowed(tmp_path):
    f,p,_=build(tmp_path);f['probability_publishable']=True
    with pytest.raises(m.AuditFailure,match='FIXTURE_CONTRACT_INVALID'):m.audit(f,p)

def test_duplicate_snapshot_refused(tmp_path):
    f,p,_=build(tmp_path);s=snapshot(f)
    with pytest.raises(m.AuditFailure,match='SNAPSHOT_IDENTITY_DUPLICATE'):m.audit(f,p,[s,s])