from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.independent_research_audit import audit_mlb_strikeout_forecasts

START = datetime(2026, 9, 17, 23, 0, tzinfo=timezone.utc)


def row(*, prediction_id='1', event_id='MLB:823333', game_pk='823333',
        direction='MORE', line='4.5', probability=.55, hit=True,
        player='Someone', model_offset=3):
    return dict(
        prediction_id=prediction_id, event_id=event_id,
        sport='MLB', model_family='MLB_PITCHER_SO_FAILURE_PATH_NB_V1',
        model_artifact_version='artifact-1',calibration_version='cal-1',
        calibration_status='PRECALIBRATION_SHRINKAGE',
        probability_publishable=True, player=player,stat_type='PITCHER_STRIKEOUTS',
        direction=direction,line=line,calibrated_probability=probability,
        created_at=(START-timedelta(hours=model_offset-0.1)).isoformat(),
        model_timestamp=(START-timedelta(hours=model_offset)).isoformat(),
        event_start_time=START.isoformat(),
        settlement_timestamp=(START+timedelta(hours=4)).isoformat(),
        settlement_source=f'https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live',
        hit=hit,push=False,void=False,actual_stat=6 if hit and direction=='MORE' else 3,
    )


def test_replay_74_times_not_74_independent_outcomes():
    rows=[row(prediction_id=str(i)) for i in range(74)]
    audit=audit_mlb_strikeout_forecasts(rows)
    assert audit['eligible_forecast_rows']==74
    assert audit['canonical_directional_theses']==1
    assert audit['canonical_base_markets']==1
    assert audit['official_game_clusters']==1
    assert audit['approx_game_cluster_se'] is None
    assert audit['status']=='RESEARCH_ONLY' and audit['certified'] is False and audit['can_execute'] is False


def test_event_aliases_and_opposing_sides_preserve_one_game():
    a=row(prediction_id='a',event_id='MLB:823333', direction='MORE',hit=True)
    b=row(prediction_id='b',event_id='823333', direction='LESS',hit=False,probability=.45)
    c=row(prediction_id='c',event_id='MLB-2026-09-17-DET-PIT',direction='MORE',hit=True)
    result=audit_mlb_strikeout_forecasts([a,b,c])
    assert result['raw_event_keys']==3
    assert result['official_game_clusters']==1
    assert result['official_games_with_aliases']==1
    assert result['canonical_directional_theses']==2
    assert result['canonical_base_markets']==1
    assert result['eligible_forecast_rows']==3


def test_pre_event_timestamps_official_source_and_synthetic_probes_are_required():
    invalids=[]
    v=row(prediction_id='late');v['created_at']=(START+timedelta(seconds=1)).isoformat();invalids.append(v)
    v=row(prediction_id='badsource');v['settlement_source']='https://example.com/api/v1.1/game/823333/feed/live';invalids.append(v)
    v=row(prediction_id='probe',event_id='SYNTHETIC-probe');invalids.append(v)
    v=row(prediction_id='unresolved');v['hit']=None;invalids.append(v)
    v=row(prediction_id='push');v['push']=True;invalids.append(v)
    v=row(prediction_id='naive');v['model_timestamp']='2026-09-17T21:00:00';invalids.append(v)
    v=row(prediction_id='noid');v['player']='';invalids.append(v)
    result=audit_mlb_strikeout_forecasts([row(),*invalids])
    assert result['eligible_forecast_rows']==1
    assert result['rejected_rows']==7
    assert result['rejection_reasons']=={
      'EXACT_IDENTITY_UNVERIFIED':1,'NOT_FROZEN_PREGAME':1,'OFFICIAL_GAME_ID_UNVERIFIED':1,
      'OUTCOME_UNRESOLVED':1,'SYNTHETIC_OR_PROBE':1,'TIMESTAMP_UNVERIFIED':1,'VOID_OR_PUSH':1}


def test_conflicting_official_settlement_is_hold_even_across_aliases():
    a=row(prediction_id='a')
    b=row(prediction_id='b',event_id='823333',hit=False)
    result=audit_mlb_strikeout_forecasts([a,b])
    assert result['status']=='HOLD'
    assert result['blocker']=='OFFICIAL_SETTLEMENT_CONFLICT'
    assert 'brier_score' not in result


def test_probability_selection_is_replayable_and_does_not_mutate_source():
    a=row(prediction_id='early',probability=.51, model_offset=5)
    b=row(prediction_id='late',probability=.61, model_offset=1)
    snapshot=deepcopy([a,b])
    first=audit_mlb_strikeout_forecasts(snapshot,selection='earliest')
    last=audit_mlb_strikeout_forecasts(snapshot,selection='latest')
    assert first['brier_score']==(.51-1)**2
    assert last['brier_score']==(.61-1)**2
    assert [a,b]==snapshot
    assert last==audit_mlb_strikeout_forecasts(list(reversed(snapshot)),selection='latest')


def test_unavailable_verified_rows_and_selection_fail_closed():
    assert audit_mlb_strikeout_forecasts([row(hit=False, prediction_id='void') | {'void':True}])['blocker']=='NO_VERIFIED_ROWS'
    try:
        audit_mlb_strikeout_forecasts([],selection='arbitrary')
    except ValueError as exc:
        assert str(exc)=='SELECTION_MUST_BE_EARLIEST_OR_LATEST'
    else:
        raise AssertionError('expected failure')


def test_unknown_settlement_flags_and_bad_lock_time_never_pass():
    a=row(prediction_id='null-flags')
    a['void']=None
    b=row(prediction_id='bad-lock')
    b['locked_at']='naive/invalid'
    c=row(prediction_id='no-event')
    c['event_id']=''
    audit=audit_mlb_strikeout_forecasts([a,b,c])
    assert audit['status']=='HOLD'
    assert audit['rejected_rows']==3
    assert audit['rejection_reasons']=={
      'EXACT_IDENTITY_UNVERIFIED':1,'SETTLEMENT_FLAGS_UNVERIFIED':1,'TIMESTAMP_UNVERIFIED':1}


def test_different_model_artifacts_and_market_lines_do_not_collapse():
    a=row(prediction_id='a',line='3.0')
    b=row(prediction_id='b',line='3',probability=.54)
    c=row(prediction_id='c',line='3.5')
    d=row(prediction_id='d',line='3')
    d['model_artifact_version']='different'
    audit=audit_mlb_strikeout_forecasts([a,b,c,d])
    assert audit['eligible_forecast_rows']==4
    assert audit['canonical_directional_theses']==3
    assert audit['canonical_base_markets']==3
    assert audit['certified'] is False and audit['can_execute'] is False
