from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from live_probability_runtime import (
    LiveScoreRequest,
    _apply_live_bounds,
    _request_blockers,
    _score_mlb,
    _snapshot_binding_blockers,
    _state_hash,
)


def request(**overrides):
    now = datetime.now(timezone.utc)
    values = dict(research_run_id='r1', official_event_id='123', sport='MLB', league='MLB', exact_selection='Away', event_status='IN_PROGRESS', settlement_rule='FULL_GAME_OUTRIGHT', source_snapshot_id=UUID('00000000-0000-0000-0000-000000000001'), live_snapshot_timestamp=now, latest_material_update_at=now, market_role='UNDERDOG', market_role_source='test', market_role_timestamp=now, market_role_confidence=.90)
    values.update(overrides)
    return LiveScoreRequest(**values)


def state():
    return {'home_team':'Home','away_team':'Away','home_score':2,'away_score':1,'inning':6,'half':'TOP','outs':1,'home_remaining_runs_mean':1.1,'away_remaining_runs_mean':1.3,'home_extra_inning_win_probability':.54,'feature_provenance':{'home_remaining_runs_mean':'certified_live_feature_pipeline','away_remaining_runs_mean':'certified_live_feature_pipeline','home_extra_inning_win_probability':'certified_live_feature_pipeline'}}


def test_scheduled_event_cannot_enter_live_lane():
    assert 'LIVE_EVENT_NOT_IN_PROGRESS' in _request_blockers(request(event_status='SCHEDULED'), datetime.now(timezone.utc))


def test_only_current_live_underdog_is_rank_eligible():
    assert 'NOT_CURRENT_LIVE_UNDERDOG' in _request_blockers(request(market_role='FAVORITE'), datetime.now(timezone.utc))


def test_stale_state_fails_closed():
    now=datetime.now(timezone.utc); req=request(live_snapshot_timestamp=now-timedelta(seconds=21), latest_material_update_at=now-timedelta(seconds=21))
    assert 'LIVE_STATE_STALE' in _request_blockers(req, now)


def test_material_update_after_snapshot_requires_rerun():
    now=datetime.now(timezone.utc); req=request(live_snapshot_timestamp=now-timedelta(seconds=5), latest_material_update_at=now)
    assert 'LIVE_SNAPSHOT_PREDATES_MATERIAL_UPDATE' in _request_blockers(req, now)


def test_mlb_model_is_deterministic_and_strict_probability():
    req=request(); s=state(); a=_score_mlb(req,s,_state_hash(s)); b=_score_mlb(req,s,_state_hash(s))
    assert a['blockers']==[] and a['raw_probability']==b['raw_probability'] and 0<a['raw_probability']<1
    assert abs(a['failure_path_score']-(1-a['raw_probability']))<1e-12 and a['simulation_draws']>=50000


def test_state_requires_provenance_for_model_features():
    req=request(); s=state(); del s['feature_provenance']
    assert _score_mlb(req,s,_state_hash(s))['blockers']==['LIVE_FEATURE_PROVENANCE_MISSING']


def test_selection_must_match_event():
    req=request(exact_selection='Not A Team'); s=state()
    assert _score_mlb(req,s,_state_hash(s))['blockers']==['SELECTION_EVENT_IDENTITY_MISMATCH']


def test_live_bounds_are_artifact_driven():
    calibrator={'live_bounds_json':[{'p_min':.40,'p_max':.60,'max_state_age_seconds':20,'lower_delta':.04,'upper_delta':.05,'confidence_level':'LIVE_80'}]}
    lower,upper,confidence=_apply_live_bounds(calibrator,.50,10)
    assert lower==pytest.approx(.46) and upper==pytest.approx(.55) and confidence=='LIVE_80'


def test_missing_live_bounds_artifact_blocks():
    with pytest.raises(ValueError,match='LIVE_PREDICTIVE_BOUNDS_ARTIFACT_MISSING'): _apply_live_bounds({},.50,5)


def test_stale_bound_bin_blocks():
    calibrator={'live_bounds_json':[{'p_min':.40,'p_max':.60,'max_state_age_seconds':5,'lower_delta':.04,'upper_delta':.05}]}
    with pytest.raises(ValueError,match='LIVE_PREDICTIVE_BOUNDS_BIN_UNAVAILABLE'): _apply_live_bounds(calibrator,.50,10)


def test_snapshot_feature_binding_must_match_certified_live_artifact():
    snapshot = {"feature_model_family": "MLB_LIVE_REMAINING_RUNS_V1", "feature_model_artifact_version": "live-v1", "feature_schema_version": "MLB_LIVE_STATE_FEATURES_V1", "feature_artifact_checksum": "abc"}
    artifact = {"model_artifact_version": "live-v1", "artifact_checksum": "abc"}
    gate = {"serving_model_version": "live-v1"}
    assert _snapshot_binding_blockers(snapshot, artifact, gate) == []
    snapshot["feature_model_artifact_version"] = "other"
    assert "LIVE_FEATURE_MODEL_VERSION_MISMATCH" in _snapshot_binding_blockers(snapshot, artifact, gate)


def test_snapshot_feature_binding_checks_artifact_checksum():
    snapshot = {"feature_model_family": "MLB_LIVE_REMAINING_RUNS_V1", "feature_model_artifact_version": "live-v1", "feature_schema_version": "MLB_LIVE_STATE_FEATURES_V1", "feature_artifact_checksum": "wrong"}
    artifact = {"model_artifact_version": "live-v1", "artifact_checksum": "abc"}
    gate = {"serving_model_version": "live-v1"}
    assert _snapshot_binding_blockers(snapshot, artifact, gate) == ["LIVE_FEATURE_ARTIFACT_CHECKSUM_MISMATCH"]
