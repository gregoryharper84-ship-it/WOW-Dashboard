"""NCAAB candidate research inference; never a certified serving model."""
import hashlib, json
from datetime import datetime,timezone
import pytest
from v17.ncaab_forward_candidate_shadow import score_ncaab_candidate_shadow,NCAABShadowBlocked
NAMES=(
    'home_recent_win_rate','away_recent_win_rate',
    'home_recent_point_diff','away_recent_point_diff',
    'home_recent_points_for','away_recent_points_for',
    'home_recent_rebound_margin_proxy','away_recent_rebound_margin_proxy',
    'home_recent_turnovers','away_recent_turnovers',
    'home_recent_fg_pct','away_recent_fg_pct',
    'home_recent_3p_pct','away_recent_3p_pct',
    'home_games_prior_log','away_games_prior_log',
    'home_rest_days_capped','away_rest_days_capped',
)
AS_OF='2026-11-18T15:00:00+00:00'
START='2026-11-18T23:00:00+00:00'
def h(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def candidate():
    payload={'model_family':'NCAAB_RESULT_FORM_LOGIT_V1','artifact_format':'STANDARDIZED_LOGISTIC_JSON_V1','split_policy':'CHRONOLOGICAL_60_20_20','feature_names':list(NAMES),'coefficients':[0.1]*18,'scaler_mean':[0.]*18,'scaler_scale':[1.]*18,'intercept':-0.2}
    return {'candidate_id':'candidate-1','sport':'NCAAB','league':'NCAAB','market_family':'OUTRIGHT_WINNER','model_family':'NCAAB_RESULT_FORM_LOGIT_V1','feature_schema_version':'NCAAB_TEAM_FORM_PRIOR_V1','source_policy_id':'SPORTSDATAVERSE_NCAAB_TEAM_BOX_CC_BY_4_V1','source_review_status':'CC_BY_4_0_PROVENANCE_READY', 'lifecycle_state':'CANDIDATE','research_screen_pass':True,'promoted':False,'active':False,'probability_publishable':False,'can_execute':False,'automatic_certification':False,'automatic_promotion':False,'created_at':'2026-11-17T13:00:00Z','artifact_payload':payload,'artifact_checksum':h(payload),'training_dataset_hash':'a'*64,'training_code_sha':'abcdef1234567890','model_artifact_version':'NCAAB_RESULT_FORM_LOGIT_V1_'+('a'*16)+'_abcdef123456', 'calibrator_payload':{'method':'IDENTITY_RAW_PROBABILITY_V1','bins':[],'selection_version':'FORWARD_CALIBRATION_SELECTION_V2','selection':{'test_block_used_for_selection':False}}}
def features():
    e={'target_event_id':'NCAAB:401920158','event_version':1,'home_team_id':'26','away_team_id':'2239','season':2027,'event_start_time':START,'feature_as_of':AS_OF,'target_source_manifest_sha256':'b'*64,'prior_game_sources':[{'game_id':'111','source_manifest_sha256':'c'*64,'settled_at':'2026-11-17T10:00:00Z','source_as_of':'2026-11-17T11:00:00Z'}], 'home_prior_event_ids':['111'],'away_prior_event_ids':['222']}
    f={name:float(i)/100 for i,name in enumerate(NAMES)}
    return {'status':'RESEARCH_FEATURES_READY','sport':'NCAAB','official_event_id':e['target_event_id'],'model_family':'NCAAB_RESULT_FORM_LOGIT_V1','feature_schema_version':'NCAAB_TEAM_FORM_PRIOR_V1','source_policy_id':'SPORTSDATAVERSE_NCAAB_TEAM_BOX_CC_BY_4_V1','feature_names':list(NAMES),'feature_values':[f[n] for n in NAMES],'features':f,'input_sha256':h(e),'source_evidence':e,'market_features_used':False,'research_only':True,'probability_publishable':False,'can_execute':False}
def score(*,feature=None,c=None):return score_ncaab_candidate_shadow(features() if feature is None else feature,candidate() if c is None else c,as_of_utc=AS_OF)
def test_real_fitted_inference_remains_shadow_and_unranked():
    x=score();assert x['status']=='FITTED_SHADOW_ONLY';assert x['sport']=='NCAAB'
    assert 0<x['research_probability_home']<1
    assert x['model_artifact_version']==candidate()['model_artifact_version']
    assert x['calibrated_lower_bound'] is None and x['probability_publishable'] is False and x['rank_eligible'] is False and x['can_execute'] is False

def test_fitted_coefficient_changes_probability():
    base=score()['research_probability_home'];c=candidate();c['artifact_payload']['intercept']=-5;c['artifact_checksum']=h(c['artifact_payload'])
    assert score(c=c)['research_probability_home']!=base

@pytest.mark.parametrize('which,expected',[
    ('model_checksum','NCAAB_SHADOW_ARTIFACT_CHECKSUM_MISMATCH'),
    ('promoted','NCAAB_SHADOW_CANDIDATE_LIFECYCLE_INVALID'),
    ('calibration','NCAAB_SHADOW_CALIBRATOR_UNSUPPORTED'),
    ('model_family','NCAAB_SHADOW_CANDIDATE_LIFECYCLE_INVALID'),
])
def test_model_invariants_fail_closed(which,expected):
    c=candidate()
    if which=='model_checksum':c['artifact_checksum']='0'*64
    if which=='promoted':c['promoted']=True
    if which=='calibration':c['calibrator_payload']['method']='UNVERIFIED'
    if which=='model_family':c['model_family']='NCAAF_RESULT_FORM_LOGIT_V1'
    with pytest.raises(NCAABShadowBlocked,match=expected):score(c=c)

@pytest.mark.parametrize('which,expected',[
    ('feature_checksum','NCAAB_SHADOW_SOURCE_RECEIPT_HASH_MISMATCH'),
    ('wrong_sport','NCAAB_SHADOW_FEATURE_PACKAGE_INVALID'),
    ('wrong_timestamp','NCAAB_SHADOW_SOURCE_NOT_PREGAME'),
    ('mutated_feature','NCAAB_SHADOW_FEATURE_VECTOR_MISMATCH'),
])
def test_source_invariants_fail_closed(which,expected):
    f=features()
    if which=='feature_checksum':f['source_evidence']['season']=2024
    if which=='wrong_sport':f['sport']='NBA'
    if which=='wrong_timestamp':f['source_evidence']['feature_as_of']='2026-11-19T00:00:00Z';f['input_sha256']=h(f['source_evidence'])
    if which=='mutated_feature':f['feature_values'][1]=55
    with pytest.raises(NCAABShadowBlocked,match=expected):score(feature=f)