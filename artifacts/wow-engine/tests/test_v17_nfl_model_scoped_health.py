"""Reproduce existing #2490 semantic challenge, synthetic data only."""
from dataclasses import replace, asdict
import math
from test_v17_nfl_forward_shadow import _prediction
from test_v17_nfl_calibration_producer_receipt import _CertifiedDb, _bound_evidence
from v17.nfl_forward_shadow import grade_forward_predictions, calibration_health
from v17.nfl_daily_stage_receipts import _forward_certification_proven

def test_exact_model_cannot_borrow_passing_health_from_mixed_models():
    cohorts=[]
    for version,wins in [('A',70),('B',90)]:
        predictions=[replace(_prediction(event_id=f'{version}-{i}',p=.8,lower=.6,upper=.9,index=i),model_version=version) for i in range(100)]
        outcomes=[{'game_id':p.official_event_id,'home_score':24 if i<wins else 17,'away_score':17 if i<wins else 24,'tie':False,'home_win':i<wins} for i,p in enumerate(predictions)]
        cohorts.append(grade_forward_predictions(predictions,outcomes))
    a=calibration_health(cohorts[0]); mixed=calibration_health(cohorts[0]+cohorts[1])
    assert a['status']=='FAIL'
    assert mixed['status']=='PASS'
    _,row,_=_bound_evidence()
    row['model_version']='A'
    db=_CertifiedDb(True,100,grade_version='A')
    db.health=mixed
    db.grades=[{**asdict(g),"model_artifact_id":row["model_artifact_id"]} for g in cohorts[0]]
    assert _forward_certification_proven(db,row) is False

def test_current_artifact_checksum_mismatch_cannot_authorize_scoped_health():
    _,row,_=_bound_evidence()
    db=_CertifiedDb(True,100)
    db.model['artifact_checksum']='different-artifact'
    assert not _forward_certification_proven(db,row)


def test_healthy_exact_artifact_cohort_remains_eligible():
    _,row,_=_bound_evidence()
    assert _forward_certification_proven(_CertifiedDb(True,100),row)


def test_duplicate_verified_event_does_not_inflate_or_weight_health():
    _,row,_=_bound_evidence()
    db=_CertifiedDb(True,100)
    db.grades.append(dict(db.grades[0]))
    assert not _forward_certification_proven(db,row)


def test_other_artifact_cohort_cannot_supply_exact_model_health():
    _,row,_=_bound_evidence()
    db=_CertifiedDb(True,100)
    for grade in db.grades:
        grade['model_artifact_id']='another-artifact'
    assert not _forward_certification_proven(db,row)


def test_complete_multi_page_exact_cohort_is_used():
    _,row,_=_bound_evidence()
    db=_CertifiedDb(True,1000)
    assert _forward_certification_proven(db,row)

def test_artifact_version_mismatch_cannot_authorize_exact_cohort():
    _,row,_=_bound_evidence()
    db=_CertifiedDb(True,100)
    db.model['model_artifact_version']='different-version'
    assert not _forward_certification_proven(db,row)

def test_adverse_later_page_cannot_be_truncated_out_of_health():
    _,row,_=_bound_evidence()
    db=_CertifiedDb(True,1000)
    for grade in db.grades[500:]:
        grade['outcome']=0
        grade['brier']=.7 ** 2
        grade['log_loss']=-math.log(.3)
    assert not _forward_certification_proven(db,row)
