from __future__ import annotations
import json, urllib.parse, urllib.request
from collections import Counter
from pathlib import Path

AS_OF='2026-08-27'
SCHEDULE='https://statsapi.mlb.com/api/v1/schedule'

def get_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'WOW-MLB-V2-source-audit/20260827'})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read().decode())

params=urllib.parse.urlencode({'sportId':1,'gameTypes':'R','startDate':'2026-03-01','endDate':AS_OF})
p=get_json(f'{SCHEDULE}?{params}')
rows=[]
for d in p.get('dates',[]):
    for g in d.get('games',[]):
        if g.get('gameType')!='R': continue
        if g.get('status',{}).get('abstractGameState')!='Final': continue
        rows.append((int(g['gamePk']),str(g['officialDate'])))
counts=Counter(pk for pk,_ in rows)
dupes={str(pk):n for pk,n in counts.items() if n>1}
unique=list(dict.fromkeys(pk for pk,_ in rows))
box_fail=[]; side_fail=[]
for i,pk in enumerate(unique,1):
    try:
        b=get_json(f'https://statsapi.mlb.com/api/v1/game/{pk}/boxscore')
        teams=b.get('teams',{})
        if not teams.get('home') or not teams.get('away'):
            side_fail.append(pk)
    except Exception as e:
        box_fail.append({'gamePk':pk,'error':str(e)[:200]})
    if i%300==0: print('AUDIT_PROGRESS',i,len(unique))
out={
 'as_of':AS_OF,
 'schedule_final_rows':len(rows),
 'unique_final_game_pks':len(unique),
 'duplicate_schedule_rows':len(rows)-len(unique),
 'duplicate_game_pks':dupes,
 'expected_team_rows_unique_games':2*len(unique),
 'boxscore_failures':box_fail,
 'boxscore_missing_sides':side_fail,
 'data_complete_after_gamepk_dedupe': not box_fail and not side_fail,
}
Path('source_audit_out').mkdir(exist_ok=True)
Path('source_audit_out/source_audit.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
