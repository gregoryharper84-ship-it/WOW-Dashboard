from __future__ import annotations
import hashlib, json
from pathlib import Path
import joblib, numpy as np

OUT=Path('artifact_hardening_out')
MODEL_DIR=OUT/'model'

def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def main():
    required=[
      MODEL_DIR/'mlb_v2_results.json', MODEL_DIR/'mlb_v2_feature_schema.json',
      MODEL_DIR/'mlb_v2_features.jsonl', MODEL_DIR/'selected_base_model.joblib',
      MODEL_DIR/'selected_calibrator.joblib', MODEL_DIR/'governance.json'
    ]
    missing=[str(p) for p in required if not p.exists()]
    if missing: raise SystemExit(f'MISSING_ARTIFACTS={missing}')
    results=json.loads((MODEL_DIR/'mlb_v2_results.json').read_text())
    schema=json.loads((MODEL_DIR/'mlb_v2_feature_schema.json').read_text())
    gov=json.loads((MODEL_DIR/'governance.json').read_text())
    assert results['feature_names']==schema['feature_names']
    assert len(schema['feature_names'])==schema['count']==results['feature_count']
    assert gov['PROMOTED'] is False and gov['ACTIVE'] is False and gov['can_execute'] is False
    assert gov['probability_publishable'] is False and gov['governed_probability_capability']=='UNAVAILABLE'

    model=joblib.load(MODEL_DIR/'selected_base_model.joblib')
    cal=joblib.load(MODEL_DIR/'selected_calibrator.joblib')
    test=[]
    with (MODEL_DIR/'mlb_v2_features.jsonl').open() as f:
        for line in f:
            r=json.loads(line)
            if r['game_date']>='2025-01-01': test.append(r)
    X=np.asarray([r['x'] for r in test[:100]],float)
    raw1=model.predict_proba(X)[:,1]
    p1=cal.predict_proba(raw1.reshape(-1,1))[:,1]
    model2=joblib.load(MODEL_DIR/'selected_base_model.joblib')
    cal2=joblib.load(MODEL_DIR/'selected_calibrator.joblib')
    raw2=model2.predict_proba(X)[:,1]
    p2=cal2.predict_proba(raw2.reshape(-1,1))[:,1]
    maxdiff=float(np.max(np.abs(p1-p2)))
    assert maxdiff < 1e-15
    manifest={
      'status':'MLB_V2_ARTIFACT_HARDENING_PASS',
      'feature_schema_version':results['feature_schema_version'],
      'feature_count':results['feature_count'],
      'selected':results['selected'],
      'reload_prediction_max_abs_diff':maxdiff,
      'artifacts':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in required},
      'governance':gov,
      'production_promotion_authorized':False,
    }
    (OUT/'artifact_manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest,indent=2))
if __name__=='__main__': main()
