from __future__ import annotations

import argparse, csv, hashlib, json, urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SOURCE_URL = "https://raw.githubusercontent.com/chadwickbureau/retrosplits/refs/heads/master/daybyday/playing-2025.csv"
EXPECTED_TEAM_ROWS = 4956
EXPECTED_GAMES = 2478
EXPECTED_ELIGIBLE = 2397
FEATURES = [
    "runs_per_game","hits_per_game","hr_per_game","bb_per_game","so_per_game",
    "tb_per_game","run_differential_per_game","errors_per_game","sb_per_game",
    "cs_per_game","bullpen_era_proxy","bullpen_k_per_game","bullpen_bb_per_game",
]
FEATURES14 = FEATURES + ["days_rest_differential"]

TARGET_V1 = {
    "n_games_total":2397,"n_train":1677,"n_calib":360,"n_test":360,
    "train_date_range":["2025-04-01","2025-08-09"],
    "test_date_range":["2025-09-05","2025-11-01"],
    "train_home_win_rate":0.5438,"test_home_win_rate":0.5444,
    "naive_constant_prob_brier":0.24803,"homefield_054_brier":0.24804,
    "raw_model_brier":0.24937,"platt_calibrated_brier":0.24802,
    "isotonic_calibrated_brier":0.24564,
    "logistic_coefficients":[
        0.031115431779991473,-0.08078444193961182,-0.12316742577375184,
        -0.01990236641613398,-0.03863398468255991,0.03188814091929574,
        0.04921833807100042,-0.11275593870275867,0.031537720993843076,
        -0.05058986374162408,-0.04078409712016112,0.04834910648676394,
        -0.016924217354137017,0.3785535735170906,
    ],
}

GOVERNANCE = {
    "PROMOTED":False,"ACTIVE":False,"probability_publishable":False,
    "governed_probability_capability":"UNAVAILABLE","can_execute":False,
}

FIELDING_ERROR_SETS = {
    "generic_of": ["F_1B_E","F_2B_E","F_3B_E","F_SS_E","F_OF_E","F_C_E","F_P_E"],
    "specific_of": ["F_1B_E","F_2B_E","F_3B_E","F_SS_E","F_LF_E","F_CF_E","F_RF_E","F_C_E","F_P_E"],
    "all_of": ["F_1B_E","F_2B_E","F_3B_E","F_SS_E","F_OF_E","F_LF_E","F_CF_E","F_RF_E","F_C_E","F_P_E"],
}

def iv(v: Any) -> int:
    try:
        if v in (None, ""): return 0
        return int(v)
    except Exception:
        try: return int(float(v))
        except Exception: return 0

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def download_source(path: Path) -> None:
    if path.exists() and path.stat().st_size > 1_000_000: return
    print(f"Downloading {SOURCE_URL}")
    urllib.request.urlretrieve(SOURCE_URL, path)

def read_and_aggregate(path: Path, error_mode: str) -> dict[tuple[str,str],dict]:
    ecols = FIELDING_ERROR_SETS[error_mode]; ag: dict[tuple[str,str],dict] = {}
    with path.open(newline='', encoding='utf-8') as f:
        r=csv.DictReader(f)
        for row in r:
            k=(row['game.key'], row['team.key'])
            if k not in ag:
                ag[k]={'game_key':row['game.key'],'game_date':row['game.date'],'game_number':iv(row.get('game.number')),'season_phase':row.get('season.phase'),'is_home':row['team.alignment']=='1','team':row['team.key'],'opponent':row['opponent.key'],'runs':0,'hits':0,'hr':0,'bb':0,'so':0,'tb':0,'errors':0,'sb':0,'cs':0,'bp_out':0,'bp_er':0,'bp_so':0,'bp_bb':0}
            a=ag[k]
            a['runs'] += iv(row.get('B_R')); a['hits'] += iv(row.get('B_H')); a['hr'] += iv(row.get('B_HR')); a['bb'] += iv(row.get('B_BB')); a['so'] += iv(row.get('B_SO')); a['tb'] += iv(row.get('B_TB')); a['sb'] += iv(row.get('B_SB')); a['cs'] += iv(row.get('B_CS')); a['errors'] += sum(iv(row.get(c)) for c in ecols)
            if row.get('P_G') == '1' and row.get('P_GS') != '1':
                a['bp_out'] += iv(row.get('P_OUT')); a['bp_er'] += iv(row.get('P_ER')); a['bp_so'] += iv(row.get('P_SO')); a['bp_bb'] += iv(row.get('P_BB'))
    games=defaultdict(dict)
    for a in ag.values(): games[a['game_key']][a['is_home']]=a
    for _,sides in games.items():
        if True in sides and False in sides:
            h,a=sides[True],sides[False]; h['opp_runs']=a['runs']; a['opp_runs']=h['runs']; target=None if h['runs']==a['runs'] else bool(h['runs']>a['runs']); h['training_target_home_win']=target; a['training_target_home_win']=target
        else:
            for a in sides.values(): a['opp_runs']=0; a['training_target_home_win']=None
    return ag

def trailing_metrics(window: list[dict], bullpen_mode: str) -> dict:
    n=len(window); out={'games_available':n}
    if n==0:
        for k in FEATURES: out[k]=None if k=='bullpen_era_proxy' else 0.0
        return out
    sums={k:sum(float(g[k]) for g in window) for k in ['runs','hits','hr','bb','so','tb','errors','sb','cs']}
    out.update({'runs_per_game':sums['runs']/n,'hits_per_game':sums['hits']/n,'hr_per_game':sums['hr']/n,'bb_per_game':sums['bb']/n,'so_per_game':sums['so']/n,'tb_per_game':sums['tb']/n,'run_differential_per_game':sum(float(g['runs']-g['opp_runs']) for g in window)/n,'errors_per_game':sums['errors']/n,'sb_per_game':sums['sb']/n,'cs_per_game':sums['cs']/n,'bullpen_k_per_game':sum(float(g['bp_so']) for g in window)/n,'bullpen_bb_per_game':sum(float(g['bp_bb']) for g in window)/n})
    if bullpen_mode=='aggregate_outs':
        outs=sum(g['bp_out'] for g in window); er=sum(g['bp_er'] for g in window); out['bullpen_era_proxy']=(27.0*er/outs) if outs else None
    elif bullpen_mode=='mean_game_era':
        eras=[27.0*g['bp_er']/g['bp_out'] for g in window if g['bp_out']>0]; out['bullpen_era_proxy']=sum(eras)/len(eras) if eras else None
    else: raise ValueError(bullpen_mode)
    return out

def build_features(ag: dict[tuple[str,str],dict], bullpen_mode: str, same_day_history: bool) -> list[dict]:
    by_team=defaultdict(list)
    for a in ag.values(): by_team[a['team']].append(a)
    for arr in by_team.values(): arr.sort(key=lambda g:(g['game_date'],g['game_number'],g['game_key']))
    result=[]
    for _,arr in by_team.items():
        for idx,g in enumerate(arr):
            prior=arr[:idx] if same_day_history else [p for p in arr[:idx] if p['game_date'] < g['game_date']]
            trailing={str(w):trailing_metrics(prior[-w:], bullpen_mode) for w in (5,10,20)}
            prev=prior[-1] if prior else None; days_rest=(date.fromisoformat(g['game_date'])-date.fromisoformat(prev['game_date'])).days if prev else None
            result.append({'game_key':g['game_key'],'game_date':g['game_date'],'is_home':g['is_home'],'team':g['team'],'opponent':g['opponent'],'season_phase':g['season_phase'],'trailing':trailing,'rest_travel':{'days_since_previous_game':days_rest},'training_target_home_win':g['training_target_home_win']})
    result.sort(key=lambda r:(r['game_date'],r['game_key'],0 if not r['is_home'] else 1)); return result

def game_rows(rows:list[dict]):
    by=defaultdict(dict)
    for r in rows: by[r['game_key']][bool(r['is_home'])]=r
    games=[]; skip=0; miss=0
    for gk,s in by.items():
        if True not in s or False not in s: continue
        h,a=s[True],s[False]; h10=h['trailing']['10']; a10=a['trailing']['10']
        if h10['games_available']<5 or a10['games_available']<5: skip+=1; continue
        if h.get('training_target_home_win') is None: miss+=1; continue
        x=[]
        for fn in FEATURES:
            hv,av=h10.get(fn),a10.get(fn); x.append(0.0 if hv is None or av is None else float(hv)-float(av))
        hr=h['rest_travel'].get('days_since_previous_game'); ar=a['rest_travel'].get('days_since_previous_game'); x.append(float(hr)-float(ar) if hr is not None and ar is not None else 0.0)
        games.append({'game_key':gk,'game_date':h['game_date'],'home_team':h['team'],'away_team':a['team'],'x':x,'y':int(bool(h['training_target_home_win']))})
    games.sort(key=lambda g:(g['game_date'],g['game_key'])); return games, {'skipped_insufficient':skip,'skipped_target':miss,'unique_games':len(by)}

def brier(p,y): return float(np.mean((np.asarray(p)-np.asarray(y))**2))
def run_v1(games:list[dict]) -> dict:
    n=len(games); te=int(n*.70); ce=int(n*.85); tr,ca,ts=games[:te],games[te:ce],games[ce:]
    def arr(q): return np.asarray([g['x'] for g in q],float),np.asarray([g['y'] for g in q],int)
    Xtr,ytr=arr(tr); Xc,yc=arr(ca); Xt,yt=arr(ts); m=LogisticRegression(max_iter=1000,C=1.0); m.fit(Xtr,ytr); pr=m.predict_proba(Xt)[:,1]; pc=m.predict_proba(Xc)[:,1]; pl=LogisticRegression(); pl.fit(pc.reshape(-1,1),yc); pp=pl.predict_proba(pr.reshape(-1,1))[:,1]; iso=IsotonicRegression(out_of_bounds='clip'); iso.fit(pc,yc); pi=iso.predict(pr); nv=np.full(len(yt),ytr.mean()); hf=np.full(len(yt),.54)
    return {'n_games_total':n,'n_train':len(tr),'n_calib':len(ca),'n_test':len(ts),'train_date_range':[tr[0]['game_date'],tr[-1]['game_date']],'test_date_range':[ts[0]['game_date'],ts[-1]['game_date']],'train_home_win_rate':round(float(ytr.mean()),4),'test_home_win_rate':round(float(yt.mean()),4),'naive_constant_prob_brier':round(brier(nv,yt),5),'homefield_054_brier':round(brier(hf,yt),5),'raw_model_brier':round(brier(pr,yt),5),'platt_calibrated_brier':round(brier(pp,yt),5),'isotonic_calibrated_brier':round(brier(pi,yt),5),'logistic_coefficients':[float(x) for x in m.coef_[0]]}
def fingerprint_score(r:dict)->dict:
    hard=['n_games_total','n_train','n_calib','n_test','train_date_range','test_date_range','train_home_win_rate','test_home_win_rate','naive_constant_prob_brier','homefield_054_brier']; hard_matches={k:r.get(k)==TARGET_V1[k] for k in hard}; bkeys=['raw_model_brier','platt_calibrated_brier','isotonic_calibrated_brier']; bdiff={k:abs(float(r[k])-float(TARGET_V1[k])) for k in bkeys}; c=np.asarray(r['logistic_coefficients']); t=np.asarray(TARGET_V1['logistic_coefficients'])
    return {'hard_matches':hard_matches,'hard_match_count':sum(hard_matches.values()),'hard_total':len(hard),'brier_abs_diffs':bdiff,'brier_max_abs_diff':max(bdiff.values()),'coef_rmse':float(np.sqrt(np.mean((c-t)**2))),'coef_max_abs_diff':float(np.max(np.abs(c-t))),'exact_rounded_briers':all(r[k]==TARGET_V1[k] for k in bkeys)}
def whole_date_split(games:list[dict]):
    n=len(games); target_tr=round(n*.70); target_ca=round(n*.85); band=max(50,int(.05*n))
    def nearest(target,lo,hi):
        cand=[i for i in range(max(1,lo),min(n,hi)+1) if i<n and games[i-1]['game_date']!=games[i]['game_date']]; return min(cand,key=lambda i:(abs(i-target),i))
    te=nearest(target_tr,target_tr-band,target_tr+band); ce=nearest(target_ca,max(te+1,target_ca-band),target_ca+band); return games[:te],games[te:ce],games[ce:]
def rel_bins(y,p,n=10):
    y=np.asarray(y); p=np.asarray(p); edges=np.linspace(0,1,n+1); out=[]
    for i in range(n):
        m=(p>=edges[i]) & ((p<=edges[i+1]) if i==n-1 else (p<edges[i+1])); k=int(m.sum()); out.append({'bin':i+1,'count':k,'mean_pred':float(p[m].mean()) if k else None,'observed':float(y[m].mean()) if k else None})
    return out
def met(y,p):
    y=np.asarray(y); p=np.clip(np.asarray(p,float),1e-12,1-1e-12); bins=rel_bins(y,p); N=len(y); ece=sum((b['count']/N)*abs(b['mean_pred']-b['observed']) for b in bins if b['count']); return {'brier':float(brier_score_loss(y,p)),'log_loss':float(log_loss(y,p,labels=[0,1])),'roc_auc':float(roc_auc_score(y,p)),'accuracy_at_0_5':float(accuracy_score(y,p>=.5)),'ece_10bin':float(ece),'probability_stats':{'min':float(p.min()),'p05':float(np.quantile(p,.05)),'median':float(np.median(p)),'p95':float(np.quantile(p,.95)),'max':float(p.max()),'mean':float(p.mean()),'std':float(p.std())},'reliability_bins':bins}
def bootstrap(y,base,p,n=10000,seed=20260827):
    y=np.asarray(y); base=np.asarray(base); p=np.asarray(p); rng=np.random.default_rng(seed); N=len(y); d=[]
    for _ in range(n):
        idx=rng.integers(0,N,N); d.append(brier(base[idx],y[idx])-brier(p[idx],y[idx]))
    a=np.asarray(d); return {'improvement_mean':float(a.mean()),'ci95':[float(np.quantile(a,.025)),float(np.quantile(a,.975))],'p_improvement_gt_0':float(np.mean(a>0))}
def run_v11(games:list[dict], outdir:Path, boots:int=10000):
    tr,ca,ts=whole_date_split(games)
    def arr(q): return np.asarray([g['x'] for g in q],float),np.asarray([g['y'] for g in q],int)
    Xtr,ytr=arr(tr); Xc,yc=arr(ca); Xt,yt=arr(ts); pipe=Pipeline([('scaler',StandardScaler()),('logistic',LogisticRegression(max_iter=5000,C=1.0,solver='lbfgs',random_state=20260827))]); pipe.fit(Xtr,ytr); pc=pipe.predict_proba(Xc)[:,1]; pr=pipe.predict_proba(Xt)[:,1]; pl=LogisticRegression(max_iter=5000,C=1.0,solver='lbfgs',random_state=20260827); pl.fit(pc.reshape(-1,1),yc); pp=pl.predict_proba(pr.reshape(-1,1))[:,1]; iso=IsotonicRegression(out_of_bounds='clip'); iso.fit(pc,yc); pi=np.asarray(iso.predict(pr)); nv=np.full(len(yt),float(ytr.mean())); hf=np.full(len(yt),.54); pred={'naive_training_home_rate':nv,'home_field_0_54':hf,'raw_standardized_logistic':pr,'platt_calibrated':pp,'isotonic_calibrated':pi}; res={'status':'BASELINE_V1_1_RESEARCH_VALIDATION','feature_names':FEATURES14,'split':{'counts':{'train':len(tr),'calibration':len(ca),'test':len(ts)},'date_ranges':{'train':[tr[0]['game_date'],tr[-1]['game_date']],'calibration':[ca[0]['game_date'],ca[-1]['game_date']],'test':[ts[0]['game_date'],ts[-1]['game_date']]},'no_shared_calendar_dates':True},'base_rates':{'train':float(ytr.mean()),'calibration':float(yc.mean()),'test':float(yt.mean())},'test_metrics':{k:met(yt,v) for k,v in pred.items()},'paired_bootstrap_brier_vs_naive':{},'governance':GOVERNANCE}
    for i,(k,v) in enumerate([('raw_standardized_logistic_vs_naive',pr),('platt_vs_naive',pp),('isotonic_vs_naive',pi)]): res['paired_bootstrap_brier_vs_naive'][k]=bootstrap(yt,nv,v,boots,20260827+i)
    outdir.mkdir(parents=True,exist_ok=True); joblib.dump(pipe,outdir/'model_pipeline.joblib'); joblib.dump(pl,outdir/'platt_calibrator.joblib'); joblib.dump(iso,outdir/'isotonic_calibrator.joblib'); (outdir/'mlb_baseline_v1_1_results.json').write_text(json.dumps(res,indent=2))
    with (outdir/'test_predictions_v1_1.csv').open('w',newline='') as f:
        w=csv.writer(f); w.writerow(['game_key','game_date','home_team','away_team','home_win','naive','homefield_054','raw','platt','isotonic'])
        for j,g in enumerate(ts): w.writerow([g['game_key'],g['game_date'],g['home_team'],g['away_team'],g['y'],nv[j],hf[j],pr[j],pp[j],pi[j]])
    return res
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,default=Path('research/mlb-v1-repro-out')); ap.add_argument('--bootstraps',type=int,default=10000); args=ap.parse_args(); out=args.out; out.mkdir(parents=True,exist_ok=True); raw=out/'playing-2025.csv'; download_source(raw); print('raw sha256',sha256(raw),'bytes',raw.stat().st_size); candidates=[]
    for em in FIELDING_ERROR_SETS:
      ag=read_and_aggregate(raw,em)
      for bm in ('aggregate_outs','mean_game_era'):
       for sd in (False,True):
        rows=build_features(ag,bm,sd); games,audit=game_rows(rows); v1=run_v1(games); sc=fingerprint_score(v1); rec={'config':{'error_mode':em,'bullpen_mode':bm,'same_day_history':sd},'team_rows':len(rows),'audit':audit,'v1':v1,'fingerprint':sc}; candidates.append(rec); print(json.dumps({'config':rec['config'],'rows':len(rows),'eligible':len(games),'fingerprint':sc},sort_keys=True))
    candidates.sort(key=lambda z:(-z['fingerprint']['hard_match_count'],z['fingerprint']['brier_max_abs_diff'],z['fingerprint']['coef_rmse'])); best=candidates[0]; report={'source_url':SOURCE_URL,'source_sha256':sha256(raw),'expected_team_rows':EXPECTED_TEAM_ROWS,'expected_games':EXPECTED_GAMES,'target_v1':TARGET_V1,'candidates':candidates,'best_config':best['config'],'best_fingerprint':best['fingerprint'],'governance':GOVERNANCE}; exact=best['fingerprint']['hard_match_count']==best['fingerprint']['hard_total'] and best['fingerprint']['exact_rounded_briers'] and best['fingerprint']['coef_max_abs_diff'] < 1e-6; report['canonical_reconstruction_proven']=bool(exact); (out/'v1_reconstruction_report.json').write_text(json.dumps(report,indent=2)); ag=read_and_aggregate(raw,best['config']['error_mode']); rows=build_features(ag,best['config']['bullpen_mode'],best['config']['same_day_history']); games,_=game_rows(rows)
    with (out/'features_2025_reconstructed.jsonl').open('w') as f:
        for r in rows: f.write(json.dumps(r,separators=(',',':'))+'\n')
    (out/'reconstruction_status.json').write_text(json.dumps({'canonical_reconstruction_proven':exact,'best_config':best['config'],'best_fingerprint':best['fingerprint'],'governance':GOVERNANCE},indent=2)); v11=run_v11(games,out/'v1_1',args.bootstraps); v11['canonical_input_reconstruction_proven']=bool(exact); (out/'v1_1'/'mlb_baseline_v1_1_results.json').write_text(json.dumps(v11,indent=2)); print('BEST',json.dumps(best['config']),json.dumps(best['fingerprint'])); print('CANONICAL_RECONSTRUCTION_PROVEN',exact)
if __name__=='__main__': main()
