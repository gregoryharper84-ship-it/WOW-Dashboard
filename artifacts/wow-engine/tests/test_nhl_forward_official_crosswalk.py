"""Tests for independent NHL identity mapping; fake the NHL resolver (no network)."""
from pathlib import Path
import importlib.util

spec=importlib.util.spec_from_file_location('identity',Path(__file__).resolve().parents[1]/'scripts/nhl_forward_official_crosswalk.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class NHLEventIdentityError(RuntimeError):
    def __init__(self,code): self.code=code

EVENT={'espn_event_id':'401892552','event_start_utc':'2026-10-22T23:00:00+00:00',
       'home_espn_team_id':'1','away_espn_team_id':'27',
       'home_name':'Boston Bruins','away_name':'Nashville Predators'}
FIX={'schema_version':'WOW_FORWARD_MODEL_IDENTITY_FIXTURE_V1','can_execute':False,
    'sports':{'NHL':{'events':[EVENT]}}}

def resolved(**kwargs):
    assert kwargs=={'event_start_time':EVENT['event_start_utc'],
                     'home_team':'Boston Bruins','away_team':'Nashville Predators'}
    return {'event_id':2026020100,'event_start_time':kwargs['event_start_time'],
            'home_team':'BOS','away_team':'NSH','game_type':2,
            'identity_provider':'NHL_PUBLIC_WEB_API','can_execute':False,
            'identity_source':'https://api-web.nhle.com/v1/club-schedule-season/BOS/20262027',
            'identity_verified_at':'2026-10-22T20:00:00+00:00'}

def test_canonical_id_pass_still_not_model_probability():
    r=m.resolve_rows(FIX,resolver=resolved)
    assert r['status_counts']=={'NHL_CANONICAL_EVENT_IDENTITY_RECONCILED':1}
    assert r['rows'][0]['nhl_official_event_id']=='2026020100'
    assert r['probability_publishable'] is False and r['sporting_probability_available'] is False

def test_ambiguous_match_keeps_typed_failure():
    def fail(**kw):raise NHLEventIdentityError('NHL_CANONICAL_EVENT_AMBIGUOUS')
    r=m.resolve_rows(FIX,resolver=fail)
    assert r['status_counts']=={'NHL_CANONICAL_EVENT_AMBIGUOUS':1}

def test_non_regular_season_rejected():
    def playoff(**kw):return {**resolved(**kw),'game_type':3}
    assert m.resolve_rows(FIX,resolver=playoff)['status_counts']=={'NHL_CANONICAL_GAME_NOT_REGULAR_SEASON':1}

def test_provider_ownership_rejected():
    def wrong(**kw):return {**resolved(**kw),'identity_provider':'ESPN'}
    assert m.resolve_rows(FIX,resolver=wrong)['status_counts']=={'NHL_CANONICAL_PROVENANCE_INVALID':1}

def test_duplicate_espn_event_rejected():
    f={**FIX,'sports':{'NHL':{'events':[EVENT,EVENT]}}}
    try:m.resolve_rows(f,resolver=resolved)
    except ValueError as exc:assert 'DUPLICATE' in str(exc)
    else:raise AssertionError('duplicate accepted')