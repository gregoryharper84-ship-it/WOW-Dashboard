from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from mlb_v1_reproduce_and_v11 import (
    FIELDING_ERROR_SETS, download_source, read_and_aggregate, build_features,
    game_rows, whole_date_split, brier, bootstrap, fingerprint_score, run_v1,
)

OUT=Path('research/mlb-v11-sensitivity-out')
RAW=OUT/'playing-2025.csv'
OUT.mkdir(parents=True,exist_ok=True)
download_source(RAW)
records=[]
for error_mode in FIELDING_ERROR_SETS:
    ag=read_and_aggregate(RAW,error_mode)
    for bullpen_mode in ('aggregate_outs','mean_game_era'):
        for same_day_history in (False,True):
            cfg={'error_mode':error_mode,'bullpen_mode':bullpen_mode,'same_day_history':same_day_history}
            rows=build_features(ag,bullpen_mode,same_day_history)
            games,_=game_rows(rows)
            fp=fingerprint_score(run_v1(games))
            tr,ca,ts=whole_date_split(games)
            def arr(q): return np.asarray([g['x'] for g in q],float),np.asarray([g['y'] for g in q],int)
            Xtr,ytr=arr(tr); Xc,yc=arr(ca); Xt,yt=arr(ts)
            pipe=Pipeline([('scaler',StandardScaler()),('logistic',LogisticRegression(max_iter=5000,C=1.0,solver='lbfgs',random_state=20260827))])
            pipe.fit(Xtr,ytr)
            pc=pipe.predict_proba(Xc)[:,1]; raw=pipe.predict_proba(Xt)[:,1]
            pl=LogisticRegression(max_iter=5000,C=1.0,solver='lbfgs',random_state=20260827); pl.fit(pc.reshape(-1,1),yc); pp=pl.predict_proba(raw.reshape(-1,1))[:,1]
            iso=IsotonicRegression(out_of_bounds='clip'); iso.fit(pc,yc); pi=np.asarray(iso.predict(raw))
            naive=np.full(len(yt),float(ytr.mean()))
            methods={'raw':raw,'platt':pp,'isotonic':pi}
            result={'config':cfg,'v1_fingerprint':fp,'split_counts':{'train':len(tr),'calibration':len(ca),'test':len(ts)},'test_base_rate':float(yt.mean()),'naive_brier':brier(naive,yt),'methods':{}}
            for i,(name,p) in enumerate(methods.items()):
                bs=bootstrap(yt,naive,p,5000,20260827+i)
                result['methods'][name]={'brier':brier(p,yt),'point_improvement_vs_naive':brier(naive,yt)-brier(p,yt),'bootstrap':bs}
            records.append(result)
            print(json.dumps({'config':cfg,'raw':result['methods']['raw'],'platt':result['methods']['platt'],'isotonic':result['methods']['isotonic']},sort_keys=True))

summary={
 'status':'RECONSTRUCTION_SENSITIVITY_ONLY_NOT_CANONICAL_PROOF',
 'candidate_count':len(records),
 'all_structural_fingerprints_match':all(r['v1_fingerprint']['hard_match_count']==r['v1_fingerprint']['hard_total'] for r in records),
 'methods':{},
 'records':records,
 'governance':{'PROMOTED':False,'ACTIVE':False,'probability_publishable':False,'governed_probability_capability':'UNAVAILABLE','can_execute':False},
}
for method in ('raw','platt','isotonic'):
    pts=[r['methods'][method]['point_improvement_vs_naive'] for r in records]
    lows=[r['methods'][method]['bootstrap']['ci95'][0] for r in records]
    highs=[r['methods'][method]['bootstrap']['ci95'][1] for r in records]
    summary['methods'][method]={
      'point_improvement_range':[float(min(pts)),float(max(pts))],
      'ci_low_range':[float(min(lows)),float(max(lows))],
      'ci_high_range':[float(min(highs)),float(max(highs))],
      'candidates_ci_excludes_zero_positive':sum(1 for r in records if r['methods'][method]['bootstrap']['ci95'][0] > 0),
      'candidates_ci_excludes_zero_negative':sum(1 for r in records if r['methods'][method]['bootstrap']['ci95'][1] < 0),
      'candidates_ci_crosses_zero':sum(1 for r in records if r['methods'][method]['bootstrap']['ci95'][0] <= 0 <= r['methods'][method]['bootstrap']['ci95'][1]),
    }
summary['robust_promotion_signal']=any(summary['methods'][m]['candidates_ci_excludes_zero_positive']==len(records) for m in summary['methods'])
summary['robust_no_statistically_secure_improvement']=all(summary['methods'][m]['candidates_ci_crosses_zero']==len(records) for m in summary['methods'])
(OUT/'mlb_v11_reconstruction_sensitivity.json').write_text(json.dumps(summary,indent=2))
print('SENSITIVITY_SUMMARY',json.dumps({k:v for k,v in summary.items() if k!='records'},sort_keys=True))
