from __future__ import annotations
import json, urllib.parse
from collections import defaultdict
from datetime import date
from pathlib import Path
import joblib, numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from run_mlb_v2_pregame import (
    GOVERNANCE, SEED, build_feature_vector, download_sources, elo_expected,
    pair_games, parse_season, starter_summary, team_summary, xy,
)
from confirm_mlb_v2_2026 import (
    AS_OF, MLB_ID_TO_RETRO, MLB_SCHEDULE_URL, fetch_2026_team_games, get_json,
)

TODAY=AS_OF
OUT=Path('live_hydrator_out')

def build_state(games, cutoff:str):
    team_hist=defaultdict(list); pitcher_hist=defaultdict(list); elo=defaultdict(lambda:1500.0)
    by_date=defaultdict(list)
    for g in games:
        if g['game_date'] < cutoff: by_date[g['game_date']].append(g)
    current_year=None
    for ds in sorted(by_date):
        d=date.fromisoformat(ds)
        if current_year is None: current_year=d.year
        elif d.year!=current_year:
            for team in list(elo): elo[team]=1500.0+0.75*(elo[team]-1500.0)
            current_year=d.year
        date_games=sorted(by_date[ds],key=lambda g:g['game_key'])
        deltas=defaultdict(float)
        for g in date_games:
            h,a=g['home'],g['away']; exp=elo_expected(elo[h['team']],elo[a['team']]); delta=20.0*(float(g['y'])-exp)
            deltas[h['team']]+=delta; deltas[a['team']]-=delta
        for g in date_games:
            h,a=g['home'],g['away']
            for side,opp in ((h,a),(a,h)):
                team_hist[side['team']].append({
                    'date':d,'is_home':bool(side['is_home']),'win':float(side['runs']>opp['runs']),
                    'runs':float(side['runs']),'runs_allowed':float(opp['runs']),'run_diff':float(side['runs']-opp['runs']),
                    'hits':float(side['hits']),'hr':float(side['hr']),'bb':float(side['bb']),'so':float(side['so']),'tb':float(side['tb']),
                    'bp_out':side['bp_out'],'bp_er':side['bp_er'],'bp_so':side['bp_so'],'bp_bb':side['bp_bb'],
                    'bp_pitch':side['bp_pitch'],'bp_relief_appearances':side['bp_relief_appearances'],
                })
                if side['starter_id']:
                    pitcher_hist[side['starter_id']].append({
                        'date':d,'out':side['starter_out'],'er':side['starter_er'],'so':side['starter_so'],'bb':side['starter_bb'],
                        'hr':side['starter_hr'],'h':side['starter_h'],'pitch':side['starter_pitch'],'tbf':side['starter_tbf'],
                    })
        for team,delta in deltas.items(): elo[team]+=delta
    return team_hist,pitcher_hist,elo

def todays_preview_games():
    params=urllib.parse.urlencode({'sportId':1,'gameTypes':'R','date':TODAY,'hydrate':'probablePitcher'})
    p=get_json(f'{MLB_SCHEDULE_URL}?{params}')
    out=[]
    for d in p.get('dates',[]):
        for g in d.get('games',[]):
            if g.get('gameType')!='R': continue
            if g.get('status',{}).get('abstractGameState')!='Preview': continue
            h=g['teams']['home']; a=g['teams']['away']; hid=int(h['team']['id']); aid=int(a['team']['id'])
            hp=h.get('probablePitcher') or {}; ap=a.get('probablePitcher') or {}
            out.append({
                'gamePk':int(g['gamePk']),'game_date':str(g['officialDate']),
                'home_id':hid,'away_id':aid,'home_team':MLB_ID_TO_RETRO.get(hid),'away_team':MLB_ID_TO_RETRO.get(aid),
                'home_starter_id':f"mlbam:{int(hp['id'])}" if hp.get('id') else None,
                'away_starter_id':f"mlbam:{int(ap['id'])}" if ap.get('id') else None,
                'home_starter_name':hp.get('fullName'),'away_starter_name':ap.get('fullName'),
            })
    return out

def main():
    OUT.mkdir(exist_ok=True)
    paths=download_sources(OUT/'raw')
    all_team_games={}
    for y in (2023,2024,2025): all_team_games.update(parse_season(paths[y]))
    t2026,audit=fetch_2026_team_games(20)
    # Strict pregame firewall: discard every current-date result, even if already final.
    t2026={k:v for k,v in t2026.items() if v['game_date'] < TODAY}
    all_team_games.update(t2026)
    games=pair_games(all_team_games)
    team_hist,pitcher_hist,elo=build_state(games,TODAY)
    previews=todays_preview_games(); d=date.fromisoformat(TODAY); rows=[]
    for g in previews:
        reason=[]
        if not g['home_team'] or not g['away_team']: reason.append('TEAM_MAPPING_MISSING')
        if not g['home_starter_id'] or not g['away_starter_id']: reason.append('PROBABLE_STARTER_MISSING')
        if reason:
            rows.append({**g,'status':'NOT_SCOREABLE','blockers':reason}); continue
        hh=team_hist[g['home_team']]; ah=team_hist[g['away_team']]
        if len(hh)<10 or len(ah)<10:
            rows.append({**g,'status':'NOT_SCOREABLE','blockers':['INSUFFICIENT_TEAM_HISTORY']}); continue
        ht=team_summary(hh,d,True); at=team_summary(ah,d,False)
        hs=starter_summary(pitcher_hist[g['home_starter_id']],d); ass=starter_summary(pitcher_hist[g['away_starter_id']],d)
        x=build_feature_vector(ht,at,hs,ass,elo[g['home_team']],elo[g['away_team']])
        rows.append({**g,'status':'FEATURES_READY','blockers':[],'feature_count':len(x),'features':x,'home_prior_starts':hs['prior_starts'],'away_prior_starts':ass['prior_starts']})
    result={
        'status':'MLB_V2_LIVE_HYDRATOR_SMOKE', 'date':TODAY,
        'strict_prior_date_only':True,'same_day_results_used':False,
        'preview_games':len(previews),'features_ready':sum(r['status']=='FEATURES_READY' for r in rows),
        'probable_starter_coverage_both':sum(bool(r.get('home_starter_id')) and bool(r.get('away_starter_id')) for r in rows),
        'rows':rows,
        'source_note':'Live 2026 team/pitcher history via MLB Stats API; 2023-2025 team history via Retrosplits. Historical pitcher identifiers are not crosswalked across source families.',
        'governance':GOVERNANCE,
    }
    (OUT/'live_hydrator_smoke.json').write_text(json.dumps(result,indent=2)); print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2));
    for r in rows: print(json.dumps({k:v for k,v in r.items() if k!='features'},sort_keys=True))
if __name__=='__main__': main()
