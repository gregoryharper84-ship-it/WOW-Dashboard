"""TDD: pregame evidence chronology and reproducible research replay."""
from datetime import datetime
import copy
import math
import pytest

from replay import (
    classify_feature_receipt,
    evaluate_chronological_replay,
    paired_cluster_ci,
)


def game(season, week, home=True, *, locked_at=None, **k):
    year=season
    gameday=f"{year}-10-{(week%20)+1:02d}"
    out=dict(game_id=f"{year}_{week:02d}_BUF_NYJ",season=year,week=week,
             gameday=gameday,feature_cutoff_date=gameday,max_prior_gameday=f"{year}-09-25",
             event_start_time_utc=gameday+"T20:00:00Z",locked_at=locked_at or gameday+"T18:00:00Z",
             feature_order=["off_epa_edge","def_epa_edge","rest_edge"],
             feature_vector=[1 if home else -1,.4 if home else -.4,0.3 if home else -.3],
             target_outcome="HOME_WIN" if home else "AWAY_WIN",
             source_content_sha256s=["a"*64],row_inputs_hash="b"*64)
    out.update(k)
    return out


def cohort():
    # Distinct game IDs, 2021-24 train, 2025 calibration, 2026 holdout.
    rows=[]
    for year, games in [(2021,16),(2022,16),(2023,16),(2024,16),(2025,12),(2026,12)]:
        for idx in range(games):
            w=idx+1; r=game(year,w,home=(idx%3!=0),game_id=f"{year}_{idx:02d}_BUF_NYJ")
            rows.append(r)
    return rows


def incumbent(rows):
    return {r['game_id']:dict(official_event_id=r['game_id'],probability=.62,
        created_at=r['event_start_time_utc'].replace("20:00","19:00"),
        event_start_time_utc=r['event_start_time_utc']) for r in rows if r['season']==2026}


def test_classify_retrospective_capture_not_certified():
    r=game(2026,5,locked_at='2026-11-17T19:00:00Z')
    assert classify_feature_receipt(r)=="RETROSPECTIVE_PRIOR_ONLY"


def test_classify_true_pregame_snapshot():
    assert classify_feature_receipt(game(2026,5))=="PRE_EVENT_LOCKED_UNAUTHENTICATED"


def test_classify_does_not_accept_future_prior_games():
    assert classify_feature_receipt(game(2026,5,max_prior_gameday="2026-10-18"))=="FEATURE_CHRONOLOGY_INVALID"


def test_same_day_lock_after_kickoff_is_not_pregame():
    r=game(2026,5,locked_at="2026-10-06T20:00:01Z")
    assert classify_feature_receipt(r)=="RETROSPECTIVE_PRIOR_ONLY"


def test_classify_requires_independent_source_hash_fields_not_fabricated():
    assert classify_feature_receipt(game(2026,5,source_content_sha256s=[]))=="SOURCE_MANIFEST_MISSING"
    assert classify_feature_receipt(game(2026,5,row_inputs_hash="not-sha"))=="SOURCE_MANIFEST_MISSING"


def test_replay_split_and_metrics_and_governance():
    rows=cohort()
    result=evaluate_chronological_replay(rows,incumbent(rows),fit_end_season=2024,
                                         calibration_season=2025,holdout_season=2026)
    assert result['status']=="RETROSPECTIVE_RESEARCH_ONLY"
    assert result['sample_sizes']=={'fit':64,'calibration':12,'holdout':12,'paired':12}
    assert math.isfinite(result['holdout']['challenger']['brier'])
    assert result['can_execute'] is False and result['probability_publishable'] is False
    assert result['model_promoted'] is False and result['terminal_authority']=="V17_TERMINAL_REDUCER"
    assert result['certification_eligible'] is False
    assert len(result['source_manifest_sha256'])==64


def test_replay_determinism():
    rows=cohort(); baseline=incumbent(rows)
    p=evaluate_chronological_replay(rows,baseline,fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    q=evaluate_chronological_replay(rows,baseline,fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert p==q


def test_replay_refuses_duplicate_event_identifiers():
    rows=cohort();rows.append(copy.deepcopy(rows[-1]))
    r=evaluate_chronological_replay(rows,incumbent(cohort()),fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert r['status']=="REPLAY_INPUT_INVALID"


def test_replay_rejects_mismatched_schema_and_future_in_training():
    rows=cohort();rows[0]['feature_order']=['wrong','names','here']
    r=evaluate_chronological_replay(rows,incumbent(cohort()),fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert r['status']=="REPLAY_INPUT_INVALID"


def test_replay_rejects_nonfinite_and_bool_vectors():
    for bad in (float('nan'),float('inf'),True):
        rows=cohort();rows[4]['feature_vector'][0]=bad
        assert evaluate_chronological_replay(rows,incumbent(cohort()),fit_end_season=2024,calibration_season=2025,holdout_season=2026)['status']=="REPLAY_INPUT_INVALID"


def test_incumbent_predictions_must_be_pre_event():
    rows=cohort();b=incumbent(rows);k=next(iter(b));b[k]['created_at']=b[k]['event_start_time_utc']
    r=evaluate_chronological_replay(rows,b,fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert r['sample_sizes']['paired']==11
    assert r['source_status']['excluded_nonpregame_incumbent']==1


def test_cluster_interval_is_reproducible_and_contains_point_when_identical():
    d=[(1,0.0),(1,0.0),(2,0.0),(2,0.0)]
    assert paired_cluster_ci(d)==(0.0,0.0)
    assert paired_cluster_ci([(1,-.1),(2,.08),(3,.2)],seed=7)==paired_cluster_ci([(1,-.1),(2,.08),(3,.2)],seed=7)


def test_replay_rejects_calibration_leakage_into_fit():
    r=evaluate_chronological_replay(cohort(),incumbent(cohort()),fit_end_season=2025,calibration_season=2025,holdout_season=2026)
    assert r['status']=="REPLAY_INPUT_INVALID"


def test_overnight_utc_kickoff_is_valid_for_local_gameday():
    r=game(2026,5,event_start_time_utc="2026-10-07T01:20:00Z")
    assert classify_feature_receipt(r)=="PRE_EVENT_LOCKED_UNAUTHENTICATED"


def test_source_manifest_changes_with_feature_values():
    x=cohort();y=copy.deepcopy(x)
    y[2]['feature_vector'][0]+=.01
    bx=incumbent(x);by=incumbent(y)
    a=evaluate_chronological_replay(x,bx,fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    b=evaluate_chronological_replay(y,by,fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert a['source_manifest_sha256'] != b['source_manifest_sha256']


def test_giant_finite_feature_does_not_publish_nonfinite_result():
    rows=cohort();rows[0]['feature_vector'][0]=1e308
    result=evaluate_chronological_replay(rows,incumbent(cohort()),fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert result['status']=="REPLAY_INPUT_INVALID"


def test_nonpregame_source_does_not_become_eligible_even_if_model_improves():
    rows=cohort()
    for r in rows:
        if r['season']==2026:r['locked_at']='2026-11-17T19:00:00Z'
    result=evaluate_chronological_replay(rows,incumbent(rows),fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert result['source_status']['holdout_retrospective']==12
    assert result['certification_eligible'] is False


def test_forbidden_postgame_feature_names_are_rejected():
    rows=cohort()
    rows[0]['feature_order'][0]='home_score'
    for r in rows:
        r['feature_order']=['home_score','def_epa_edge','rest_edge']
    result=evaluate_chronological_replay(rows,incumbent(rows),fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert result['status']=='REPLAY_INPUT_INVALID'


def test_empty_source_manifest_and_nonfinite_incumbent_not_claimed():
    rows=cohort();base=incumbent(rows)
    k=next(iter(base));base[k]['probability']=float('inf')
    r=evaluate_chronological_replay(rows,base,fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert r['sample_sizes']['paired']==11
    assert r['source_status']['excluded_nonpregame_incumbent']==1


def test_offline_cli_reads_json_and_writes_research_only_report(tmp_path):
    from run_replay import run_from_file
    import json
    dataset=tmp_path/'dataset.json';dest=tmp_path/'result.json'
    rows=cohort()
    dataset.write_text(json.dumps({'rows':rows,'incumbent':incumbent(rows)}))
    result=run_from_file(dataset,dest)
    assert result['status']=='RETROSPECTIVE_RESEARCH_ONLY'
    report=json.loads(dest.read_text())
    assert report['certification_eligible'] is False
    assert report['model_promoted'] is False
    assert report['sample_sizes']['paired']==12


def test_offline_cli_rejects_improper_dataset_without_writing(tmp_path):
    from run_replay import run_from_file
    import json
    dataset=tmp_path/'dataset.json';dest=tmp_path/'result.json'
    dataset.write_text(json.dumps({'rows':[], 'incumbent':{}}))
    result=run_from_file(dataset,dest)
    assert result['status']=='REPLAY_INPUT_INVALID'
    assert not dest.exists()


def test_challenger_incumbent_must_match_exact_kickoff():
    rows=cohort();old=incumbent(rows);key=next(iter(old))
    old[key]['event_start_time_utc']='2026-10-06T21:00:00Z'
    out=evaluate_chronological_replay(rows,old,fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert out['sample_sizes']['paired']==11
    assert out['source_status']['excluded_nonpregame_incumbent']==1


def test_replay_rejects_foreign_future_year_rows():
    rows=cohort();rows.append(game(2027,4,game_id='2027_04_LA_SF'))
    out=evaluate_chronological_replay(rows,incumbent(cohort()),fit_end_season=2024,calibration_season=2025,holdout_season=2026)
    assert out['status']=='REPLAY_INPUT_INVALID'
