"""Maintenance runner for leakage-safe LLP dynamic team-state challengers.

Each lane is isolated. Failures remain typed candidate evidence and cannot alter
production champions, publish probabilities, auto-promote, or execute wagers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
from typing import Any, Callable, Mapping, Sequence

import requests

from v17.team_state_challenger_training import train_binary_challenger, train_multiclass_challenger

CAN_EXECUTE = False
PROGRAM = "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1"


class TeamStateMaintenanceUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message); self.code = code


def _sha() -> str:
    return str(os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT_SHA") or "").strip().lower()


def _paginate(query: Any, page_size: int = 1000) -> list[dict[str,Any]]:
    rows=[]; offset=0
    while True:
        result=query.range(offset,offset+page_size-1).execute()
        batch=[dict(r) for r in (result.data or []) if isinstance(r,Mapping)]
        rows.extend(batch)
        if len(batch)<page_size: break
        offset += page_size
    return rows


def _basketball_events(client: Any, sport: str) -> list[dict[str,Any]]:
    from basketball_specialist_pipeline import load_games
    return [{"event_id":f"{sport}:{g.game_id}","event_start_time":f"{g.game_date.isoformat()}T12:00:00+00:00",
             "season":int(g.season),"home_team":str(g.home_team_id),"away_team":str(g.away_team_id),
             "home_score":float(g.home_score),"away_score":float(g.away_score),
             "source_manifest":{"source":f"wow_{sport.lower()}_training_games","game_id":str(g.game_id)}}
            for g in load_games(client,sport)]


def _ncaaf_events(client: Any) -> list[dict[str,Any]]:
    from v17.ncaaf_result_form_candidate import _load_games
    out=[]
    for r in _load_games(client):
        if r.get("home_points") is None or r.get("away_points") is None: continue
        out.append({"event_id":f"NCAAF:{r.get('official_event_id')}","event_start_time":r.get("event_start_time"),
                    "season":r.get("season"),"home_team":r.get("home_team"),"away_team":r.get("away_team"),
                    "home_score":float(r["home_points"]),"away_score":float(r["away_points"]),
                    "source_manifest":{"source":r.get("result_source") or "wow_ncaaf_training_games",
                                       "source_timestamp":r.get("result_source_timestamp"),"official_event_id":r.get("official_event_id")}})
    return out


def _ncaab_events() -> list[dict[str,Any]]:
    from v17.ncaab_sportsdataverse_candidate import fetch_games
    games,sources=fetch_games(); by_sha={r.get("sha256"):r for r in sources}
    return [{"event_id":f"NCAAB:{g['game_id']}","event_start_time":g["event_start"].isoformat(),"season":g.get("season"),
             "home_team":g["home_team_id"],"away_team":g["away_team_id"],"home_score":float(g["home_score"]),
             "away_score":float(g["away_score"]),"source_manifest":by_sha.get(g.get("source_sha256"),{"source":"SportsDataverse"})}
            for g in games]


def _soccer_events(competition: str, code: str) -> list[dict[str,Any]]:
    from v17.soccer_openfootball_candidate import _event_time, _score, fetch_competition
    matches,_=fetch_competition(code); out=[]
    for raw in matches:
        score=_score(raw)
        if score is None: continue
        home,away=str(raw.get("team1") or "").strip(),str(raw.get("team2") or "").strip()
        if not home or not away: continue
        start=_event_time(raw); eid=sha256(f"{competition}|{start.isoformat()}|{home}|{away}".encode()).hexdigest()[:24]
        out.append({"event_id":f"SOCCER:{competition}:{eid}","event_start_time":start.isoformat(),"season":raw.get("_season"),
                    "home_team":home,"away_team":away,"home_score":float(score[0]),"away_score":float(score[1]),
                    "source_manifest":{"source":"OPENFOOTBALL_CC0","url":raw.get("_source_url"),
                                       "sha256":raw.get("_source_sha256"),"competition":competition}})
    return out


def _nfl_events(client: Any) -> list[dict[str,Any]]:
    games=_paginate(client.table("wow_nfl_training_games").select(
        "game_id,season,week,gameday,home_team,away_team,home_score,away_score,schedule_content_sha256").order("gameday").order("game_id"))
    summaries=_paginate(client.table("wow_nfl_game_team_summaries").select(
        "game_id,team,offensive_plays,offensive_epa_sum,offensive_epa_mean,defensive_epa_mean,qb_gsis_ids,pbp_content_sha256").order("game_id").order("team"))
    by_game: dict[str,dict[str,dict[str,Any]]]={}
    for r in summaries: by_game.setdefault(str(r.get("game_id")),{})[str(r.get("team"))]=r
    out=[]
    for g in games:
        gid=str(g.get("game_id") or ""); home,away=str(g.get("home_team") or ""),str(g.get("away_team") or "")
        if not gid or not home or not away: continue
        h=by_game.get(gid,{}).get(home,{}); a=by_game.get(gid,{}).get(away,{})
        hepa,aepa=float(h.get("offensive_epa_sum") or 0),float(a.get("offensive_epa_sum") or 0)
        hplays,aplays=float(h.get("offensive_plays") or 0),float(a.get("offensive_plays") or 0)
        out.append({"event_id":f"NFL:{gid}","event_start_time":f"{g['gameday']}T12:00:00+00:00","season":int(g.get("season") or 0),
                    "home_team":home,"away_team":away,"home_score":float(g.get("home_score") or 0),"away_score":float(g.get("away_score") or 0),
                    "home_process_margin":hepa-aepa,"away_process_margin":aepa-hepa,
                    "home_attack_style_index":float(h.get("offensive_epa_mean") or 0),"away_attack_style_index":float(a.get("offensive_epa_mean") or 0),
                    "home_defense_style_index":-float(h.get("defensive_epa_mean") or 0),"away_defense_style_index":-float(a.get("defensive_epa_mean") or 0),
                    "home_pace_or_tempo_index":hplays/70 if hplays else 0,"away_pace_or_tempo_index":aplays/70 if aplays else 0,
                    "home_history_lineup_ids":list(h.get("qb_gsis_ids") or []) if isinstance(h.get("qb_gsis_ids"),list) else [],
                    "away_history_lineup_ids":list(a.get("qb_gsis_ids") or []) if isinstance(a.get("qb_gsis_ids"),list) else [],
                    "source_manifest":{"source":"NFLVERSE_PUBLIC_DATA","schedule_sha256":g.get("schedule_content_sha256"),
                                       "home_pbp_sha256":h.get("pbp_content_sha256"),"away_pbp_sha256":a.get("pbp_content_sha256")}})
    return out


def _mlb_official_events(seasons: Sequence[int]=(2023,2024,2025,2026)) -> list[dict[str,Any]]:
    out=[]; seen_event_ids:set[str]=set()
    for season in seasons:
        response=requests.get("https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId":1,"season":int(season),"gameType":"R","hydrate":"team"},timeout=60)
        if response.status_code != 200: raise TeamStateMaintenanceUnavailable("MLB_STATSAPI_FAILED",f"{response.status_code}:{season}")
        digest=sha256(response.content).hexdigest()
        for date_row in (response.json().get("dates") or []):
            for game in date_row.get("games") or []:
                if str(((game.get("status") or {}).get("abstractGameState") or "")).upper() != "FINAL": continue
                teams=game.get("teams") or {}; h=teams.get("home") or {}; a=teams.get("away") or {}
                home=str(((h.get("team") or {}).get("id") or "")); away=str(((a.get("team") or {}).get("id") or "")); gid=str(game.get("gamePk") or "")
                event_id=f"MLB:{gid}" if gid else ""
                if not home or not away or not event_id or h.get("score") is None or a.get("score") is None: continue
                if event_id in seen_event_ids: continue
                seen_event_ids.add(event_id)
                out.append({"event_id":event_id,"event_start_time":game.get("gameDate"),"season":int(season),"home_team":home,"away_team":away,
                            "home_score":float(h["score"]),"away_score":float(a["score"]),
                            "source_manifest":{"source":"MLB_STATSAPI_OFFICIAL","season":int(season),"sha256":digest}})
    if not out: raise TeamStateMaintenanceUnavailable("MLB_STATSAPI_EMPTY","no settled regular-season games")
    return sorted(out,key=lambda r:(str(r["event_start_time"]),str(r["event_id"])))


def _blocked(name: str, exc: Exception) -> dict[str,Any]:
    return {"sport":name,"status":"BLOCKED","code":str(getattr(exc,"code",None) or f"{name}_TEAM_STATE_MAINTENANCE_FAILED"),
            "detail":{"error_type":type(exc).__name__,"message":str(exc)[:600]},"automatic_certification":False,
            "automatic_promotion":False,"probability_publishable":False,"can_execute":False}


def run_all_team_state_challengers(client: Any, *, training_code_sha: str|None=None) -> dict[str,Any]:
    code=str(training_code_sha or _sha()).strip().lower()
    if len(code)<7: return {"status":"BLOCKED","code":"TEAM_STATE_TRAINING_CODE_SHA_UNAVAILABLE","rows":[],
                           "automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}
    jobs: list[tuple[str,Callable[[],dict[str,Any]]]]=[
        ("NFL",lambda:train_binary_challenger(client,sport="NFL",league="NFL",events=_nfl_events(client),expected_season_games=17,training_code_sha=code,min_rows=300)),
        ("MLB",lambda:train_binary_challenger(client,sport="MLB",league="MLB",events=_mlb_official_events(),expected_season_games=162,training_code_sha=code,min_rows=500)),
        ("NBA",lambda:train_binary_challenger(client,sport="NBA",league="NBA",events=_basketball_events(client,"NBA"),expected_season_games=82,training_code_sha=code,min_rows=300)),
        ("WNBA",lambda:train_binary_challenger(client,sport="WNBA",league="WNBA",events=_basketball_events(client,"WNBA"),expected_season_games=44,training_code_sha=code,min_rows=250)),
        ("NCAAF",lambda:train_binary_challenger(client,sport="NCAAF",league="NCAAF",events=_ncaaf_events(client),expected_season_games=13,training_code_sha=code,min_rows=300)),
        ("NCAAB",lambda:train_binary_challenger(client,sport="NCAAB",league="NCAAB",events=_ncaab_events(),expected_season_games=31,training_code_sha=code,min_rows=500)),
    ]
    from v17.soccer_openfootball_candidate import COMPETITIONS
    for competition,provider_code in COMPETITIONS.items():
        jobs.append((f"SOCCER_{competition}",lambda c=competition,p=provider_code:train_multiclass_challenger(
            client,sport="SOCCER",league=c,events=_soccer_events(c,p),expected_season_games=38,training_code_sha=code,min_rows=500)))
    results=[]
    for name,fn in jobs:
        try:
            row=dict(fn()); row["status"]="CANDIDATE_EVIDENCE_UPDATED"; results.append(row)
        except Exception as exc: results.append(_blocked(name,exc))
    updated=sum(r.get("status")=="CANDIDATE_EVIDENCE_UPDATED" for r in results)
    return {"status":"COMPLETED_WITH_EVIDENCE" if updated else "COMPLETED_NO_CANDIDATE_SURVIVORS","program":PROGRAM,
            "generated_at":datetime.now(timezone.utc).isoformat(),"candidate_rows_updated":updated,"candidate_rows_blocked":len(results)-updated,
            "rows":results,"participant_sports_not_forced_into_team_state_model":["TENNIS","PGA","MMA","BOXING"],
            "automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}


__all__=["CAN_EXECUTE","PROGRAM","TeamStateMaintenanceUnavailable","run_all_team_state_challengers"]
