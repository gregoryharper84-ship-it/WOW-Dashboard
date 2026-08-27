from __future__ import annotations
import csv, json
from collections import defaultdict
from pathlib import Path
import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

IN=Path('uncertainty_out/confirmation')
PRED=IN/'mlb_v2_2026_predictions.csv'
OUT=Path('uncertainty_out/uncertainty_audit.json')

def metrics(rows):
    y=np.asarray([int(r['home_win']) for r in rows]); p=np.asarray([float(r['rolling_platt']) for r in rows]); h=np.full(len(y),.54)
    return {
      'n':len(y),
      'brier':float(brier_score_loss(y,p)),
      'home54_brier':float(brier_score_loss(y,h)),
      'brier_gain_vs_home54':float(brier_score_loss(y,h)-brier_score_loss(y,p)),
      'log_loss':float(log_loss(y,p,labels=[0,1])),
      'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,
      'mean_prob':float(p.mean()),
      'observed_home_win_rate':float(y.mean()),
    }

def block_bootstrap(rows,n_iter=10000,seed=20260827):
    by_date=defaultdict(list)
    for r in rows: by_date[r['game_date']].append(r)
    dates=sorted(by_date); rng=np.random.default_rng(seed); diffs=[]
    for _ in range(n_iter):
        sample_dates=rng.choice(dates,size=len(dates),replace=True)
        ys=[]; ps=[]
        for d in sample_dates:
            for r in by_date[d]: ys.append(int(r['home_win'])); ps.append(float(r['rolling_platt']))
        y=np.asarray(ys); p=np.asarray(ps); h=np.full(len(y),.54)
        diffs.append(float(np.mean((h-y)**2)-np.mean((p-y)**2)))
    a=np.asarray(diffs)
    return {'date_blocks':len(dates),'mean':float(a.mean()),'ci95':[float(np.quantile(a,.025)),float(np.quantile(a,.975))],'p_gt_0':float(np.mean(a>0))}

rows=list(csv.DictReader(PRED.open()))
months=defaultdict(list)
for r in rows: months[r['game_date'][:7]].append(r)
out={
 'status':'MLB_V2_2026_UNCERTAINTY_AUDIT',
 'overall':metrics(rows),
 'monthly':{m:metrics(v) for m,v in sorted(months.items())},
 'date_block_bootstrap_vs_home54':block_bootstrap(rows),
 'stable_months_positive_brier_gain':sum(1 for v in months.values() if metrics(v)['brier_gain_vs_home54']>0),
 'total_months':len(months),
 'model_changed':False,
 'calibrator_changed':False,
 'governance':{'PROMOTED':False,'ACTIVE':False,'probability_publishable':False,'governed_probability_capability':'UNAVAILABLE','can_execute':False},
}
OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
