"""Read-only, source-frozen NHL prospective candidate feature reconstruction.

This module cannot authorize a model. Exact 10-feature numeric candidates are
research-only and require subsequent certified replay, calibration and bounds.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
from urllib import request, error

HOST = "api-web.nhle.com"
SOURCE_ID = "NHL_PUBLIC_WEB_API"
FEATURE_SCHEMA_VERSION = "NHL_REGULAR_SEASON_FEATURES_V1"
RECONSTRUCTION_VERSION = "NHL_PRIOR_SETTLED_RECONSTRUCTION_V1"
FEATURE_NAMES = (
    "elo_delta", "win_pct_l10_delta", "goal_diff_l10_delta",
    "goals_for_l10_delta", "goals_against_l10_delta", "season_win_pct_delta",
    "rest_days_delta", "home_back_to_back", "away_back_to_back", "neutral_site",
)
BASE_TEAMS = (
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "VAN", "VGK", "WPG", "WSH",
)
ALL_TEAMS_2026 = tuple(sorted(set(BASE_TEAMS + ("UTA",))))
MIN_PRIOR_GAMES = 5
ELO_K = 20.0
ELO_INITIAL = 1500.0
RECENT_WINDOW = 10
MIN_LEAD_SECONDS = 600
MIN_SETTLED_AGE_HOURS = 8
MAX_TEAM_BYTES = 2_500_000
CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


class ForwardFeatureBlocked(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def utc(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            raise ValueError("naive datetime")
        return d.astimezone(timezone.utc)
    except (ValueError, TypeError) as e:
        raise ForwardFeatureBlocked("NHL_SOURCE_EVENT_TIME_INVALID") from e


def _h(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def season_id(t):
    year = t.year if t.month >= 7 else t.year - 1
    return int(f"{year}{year+1}")


def validate_crosswalk(data):
    if not isinstance(data, dict) or data.get("status") != "IDENTITY_RECONCILIATION_RESEARCH_ONLY":
        raise ForwardFeatureBlocked("NHL_CROSSWALK_UNVERIFIED")
    if data.get("can_execute") is not False or data.get("probability_publishable") is not False:
        raise ForwardFeatureBlocked("NHL_CROSSWALK_AUTHORITY_INVALID")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ForwardFeatureBlocked("NHL_CROSSWALK_EMPTY")
    seen_espn, seen_nhl = set(), set()
    for row in rows:
        if row.get("status") != "NHL_CANONICAL_EVENT_IDENTITY_RECONCILED" or row.get("can_execute") is not False:
            raise ForwardFeatureBlocked("NHL_CROSSWALK_ROW_NOT_VERIFIED")
        a,b = str(row.get("espn_event_id") or ""), str(row.get("nhl_official_event_id") or "")
        home,away = row.get("nhl_home_abbreviation"), row.get("nhl_away_abbreviation")
        if not a or not b or not home or not away or home == away or home not in ALL_TEAMS_2026 or away not in ALL_TEAMS_2026:
            raise ForwardFeatureBlocked("NHL_CROSSWALK_ROW_INVALID")
        if a in seen_espn or b in seen_nhl:
            raise ForwardFeatureBlocked("NHL_CROSSWALK_IDENTITY_DUPLICATE")
        seen_espn.add(a); seen_nhl.add(b)
        if utc(row["event_start_utc"]) != utc(row["nhl_official_start_utc"]):
            raise ForwardFeatureBlocked("NHL_CROSSWALK_START_MISMATCH")
    return rows


class RefuseRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ForwardFeatureBlocked("NHL_SOURCE_REDIRECT_REFUSED")


def fetch_schedule(team, sid, *, opener=None, now=None):
    if team not in ALL_TEAMS_2026 or sid != 20262027:
        raise ForwardFeatureBlocked("NHL_SOURCE_TEAM_SEASON_UNSUPPORTED")
    url = f"https://{HOST}/v1/club-schedule-season/{team}/{sid}"
    opener = opener or request.build_opener(RefuseRedirect())
    req = request.Request(url, headers={"Accept":"application/json", "User-Agent":"WOW-V17-research-forward-nhl/1.0"})
    try:
        with opener.open(req, timeout=14) as res:
            if res.geturl() != url:
                raise ForwardFeatureBlocked("NHL_SOURCE_REDIRECT_REFUSED")
            if res.status != 200:
                raise ForwardFeatureBlocked("NHL_SOURCE_STATUS_INVALID")
            if "json" not in (res.headers.get("Content-Type") or "").lower():
                raise ForwardFeatureBlocked("NHL_SOURCE_CONTENT_TYPE_INVALID")
            date_header = res.headers.get("Date")
            if not date_header:
                raise ForwardFeatureBlocked("NHL_SOURCE_DATE_MISSING")
            try:
                source_clock = parsedate_to_datetime(date_header).astimezone(timezone.utc)
            except Exception as e:
                raise ForwardFeatureBlocked("NHL_SOURCE_DATE_INVALID") from e
            if abs((utc(now) - source_clock).total_seconds()) > 600:
                raise ForwardFeatureBlocked("NHL_SOURCE_DATE_SKEW")
            raw = res.read(MAX_TEAM_BYTES+1)
            if not raw or len(raw)>MAX_TEAM_BYTES:
                raise ForwardFeatureBlocked("NHL_SOURCE_BYTES_INVALID")
    except ForwardFeatureBlocked:
        raise
    except error.HTTPError as e:
        if e.code == 429:
            raise ForwardFeatureBlocked("NHL_SOURCE_RATE_LIMITED") from e
        raise ForwardFeatureBlocked("NHL_SOURCE_HTTP_ERROR",str(e.code)) from e
    except (OSError, error.URLError) as e:
        raise ForwardFeatureBlocked("NHL_SOURCE_TRANSPORT_FAILED", type(e).__name__) from e
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as e:
        raise ForwardFeatureBlocked("NHL_SOURCE_JSON_INVALID") from e
    if not isinstance(payload,dict) or not isinstance(payload.get("games"),list):
        raise ForwardFeatureBlocked("NHL_SOURCE_GAMES_MALFORMED")
    return raw, payload, {"source_url":url,"http_date":source_clock.isoformat(),"raw_sha256":hashlib.sha256(raw).hexdigest(),"raw_bytes":len(raw)}


def parse_history(payloads, *, observed_at):
    """Require full 32-team schedule redundancy; never use recent or future outcomes."""
    observations={}
    recent=[]
    for team, payload in payloads.items():
        for raw in payload["games"]:
            if not isinstance(raw,dict):
                raise ForwardFeatureBlocked("NHL_SOURCE_GAME_MALFORMED")
            if int(raw.get("gameType") or 0) != 2:
                continue
            start_raw=raw.get("startTimeUTC")
            if not start_raw:
                continue
            start=utc(start_raw)
            if start >= observed_at:
                continue
            state=str(raw.get("gameState") or "").upper()
            if start > observed_at - timedelta(hours=MIN_SETTLED_AGE_HOURS):
                # Conservative cutoff: finished status fetched after observation is not an as-of attestation.
                if state in {"FINAL","OFF"}:
                    recent.append(str(raw.get("id")))
                continue
            if state not in {"FINAL","OFF"}:
                raise ForwardFeatureBlocked("NHL_PRIOR_GAME_SETTLEMENT_UNCERTAIN", str(raw.get("id")))
            h,a=raw.get("homeTeam"),raw.get("awayTeam")
            if not isinstance(h,dict) or not isinstance(a,dict):
                raise ForwardFeatureBlocked("NHL_PRIOR_GAME_TEAMS_INVALID")
            habbr=str(h.get("abbrev") or "").upper(); aabbr=str(a.get("abbrev") or "").upper()
            if habbr not in ALL_TEAMS_2026 or aabbr not in ALL_TEAMS_2026 or habbr==aabbr:
                raise ForwardFeatureBlocked("NHL_PRIOR_GAME_TEAM_UNKNOWN")
            if team not in {habbr,aabbr}:
                raise ForwardFeatureBlocked("NHL_PRIOR_GAME_SOURCE_IDENTITY_CONFLICT")
            gid=str(raw.get("id") or "")
            try:
                hs=int(h["score"]); ans=int(a["score"])
            except (KeyError,TypeError,ValueError) as e:
                raise ForwardFeatureBlocked("NHL_PRIOR_GAME_SCORE_INVALID") from e
            if not gid or hs<0 or ans<0 or hs==ans or int(raw.get("season") or 0)!=20262027:
                raise ForwardFeatureBlocked("NHL_PRIOR_GAME_OUTCOME_INVALID")
            ident=(gid,start.isoformat(),habbr,aabbr,hs,ans)
            observations.setdefault(gid,[]).append((team,ident,raw))
    if recent:
        raise ForwardFeatureBlocked("NHL_RECENT_GAME_SETTLEMENT_NOT_ATTESTED", ",".join(sorted(set(recent))))
    games=[]
    for gid, copies in observations.items():
        if len(copies)!=2 or len({a[0] for a in copies})!=2 or any(c[1]!=copies[0][1] for c in copies[1:]):
            raise ForwardFeatureBlocked("NHL_PRIOR_GAME_MISSING_OR_CONFLICTING_CLUB_COPY",gid)
        games.append(copies[0][1])
    return sorted(games,key=lambda g:(g[1],g[0]))


def _new_state():
    return {"elo":ELO_INITIAL,"wins":0,"games":0,"recent":deque(maxlen=RECENT_WINDOW),"last_start":None}


def advance_history(games):
    """Mirror nhl_candidate_pipeline.reconstruct_training_rows state advance."""
    states={team:_new_state() for team in ALL_TEAMS_2026}
    for gid,start_str,home,away,hs,ans in games:
        dt=utc(start_str); h=states[home]; a=states[away]
        expect=1/(1+math.pow(10,(a["elo"]-h["elo"])/400))
        delta=ELO_K*((1.0 if hs>ans else 0.0)-expect)
        h["elo"]+=delta; a["elo"]-=delta
        h["wins"]+=int(hs>ans); a["wins"]+=int(ans>hs)
        h["games"]+=1; a["games"]+=1
        h["recent"].append((int(hs>ans),hs,ans))
        a["recent"].append((int(ans>hs),ans,hs))
        h["last_start"]=dt; a["last_start"]=dt
    return states


def _metrics(state):
    rec=state["recent"]
    wins=sum(g[0] for g in rec)
    gf=sum(g[1] for g in rec); ga=sum(g[2] for g in rec)
    return (wins/len(rec),(gf-ga)/len(rec),gf/len(rec),ga/len(rec))


def make_features(row, states, payloads, *,observed_at):
    home,away=row["nhl_home_abbreviation"],row["nhl_away_abbreviation"]
    eid=str(row["nhl_official_event_id"])
    start=utc(row["event_start_utc"])
    if (start-observed_at).total_seconds()<MIN_LEAD_SECONDS:
        raise ForwardFeatureBlocked("NHL_EVENT_MIN_LEAD_NOT_MET",eid)
    official=[]
    for team in (home,away):
        official += [g for g in payloads[team]["games"] if str(g.get("id") or "")==eid]
    if len(official)!=2:
        raise ForwardFeatureBlocked("NHL_FUTURE_EVENT_MISSING_IN_TEAM_SCHEDULES",eid)
    for game in official:
        if str(game.get("homeTeam",{}).get("abbrev") or "").upper()!=home or str(game.get("awayTeam",{}).get("abbrev") or "").upper()!=away or utc(game.get("startTimeUTC"))!=start or int(game.get("season") or 0)!=20262027 or int(game.get("gameType") or 0)!=2:
            raise ForwardFeatureBlocked("NHL_FUTURE_EVENT_IDENTITY_CONFLICT",eid)
        if str(game.get("gameState") or "").upper() in {"FINAL","OFF"}:
            raise ForwardFeatureBlocked("NHL_FUTURE_EVENT_ALREADY_FINAL",eid)
    if bool(official[0].get("neutralSite",False))!=bool(official[1].get("neutralSite",False)):
        raise ForwardFeatureBlocked("NHL_FUTURE_NEUTRAL_SITE_CONFLICT",eid)
    h=states[home];a=states[away]
    if h["games"]<MIN_PRIOR_GAMES or a["games"]<MIN_PRIOR_GAMES:
        raise ForwardFeatureBlocked("NHL_PRIOR_GAMES_INSUFFICIENT",f"{eid}:{h['games']}/{a['games']}")
    hrest=min((start-h["last_start"]).total_seconds()/86400,14.0)
    arest=min((start-a["last_start"]).total_seconds()/86400,14.0)
    if hrest<=0 or arest<=0:
        raise ForwardFeatureBlocked("NHL_PRIOR_GAME_CHRONOLOGY_INVALID",eid)
    hw,hgd,hgf,hga=_metrics(h);aw,agd,agf,aga=_metrics(a)
    result={
      "elo_delta":h["elo"]-a["elo"],
      "win_pct_l10_delta":hw-aw,
      "goal_diff_l10_delta":hgd-agd,
      "goals_for_l10_delta":hgf-agf,
      "goals_against_l10_delta":hga-aga,
      "season_win_pct_delta":h["wins"]/h["games"]-a["wins"]/a["games"],
      "rest_days_delta":hrest-arest,
      "home_back_to_back":1.0 if hrest<=1.5 else 0.0,
      "away_back_to_back":1.0 if arest<=1.5 else 0.0,
      "neutral_site":1.0 if bool(official[0].get("neutralSite",False)) else 0.0,
    }
    if tuple(result)!=FEATURE_NAMES or not all(isinstance(v,float) and math.isfinite(v) for v in result.values()):
        raise ForwardFeatureBlocked("NHL_FEATURE_VECTOR_INVALID")
    return {"espn_event_id":row["espn_event_id"],"official_event_id":eid,
      "event_start_time":start.isoformat(),"feature_as_of":observed_at.isoformat(),
      "feature_schema_version":FEATURE_SCHEMA_VERSION,"reconstruction_version":RECONSTRUCTION_VERSION,
      "features":result,"home_prior_games":h["games"],"away_prior_games":a["games"],
      "model_artifact_id":None,"goaltender_status_attested":False,
      "probability_publishable":False,"can_execute":False,"status":"NHL_CANDIDATE_FORWARD_FEATURES_RECONSTRUCTED_UNCERTIFIED"}


def run(*,crosswalk,outdir,opener=None,observed_at=None,team_universe=ALL_TEAMS_2026):
    rows=validate_crosswalk(crosswalk)
    observed_at=observed_at or datetime.now(timezone.utc)
    observed_at=utc(observed_at)
    if any((utc(r["event_start_utc"])-observed_at).total_seconds()<MIN_LEAD_SECONDS for r in rows):
        raise ForwardFeatureBlocked("NHL_ANY_EVENT_NO_LONGER_PREGAME")
    if tuple(sorted(team_universe))!=ALL_TEAMS_2026:
        raise ForwardFeatureBlocked("NHL_HISTORY_TEAM_UNIVERSE_INCOMPLETE")
    target=Path(outdir)
    target.mkdir(parents=True,exist_ok=False)
    snapshots={};metadata={};now=observed_at
    for team in team_universe:
        raw,payload,meta=fetch_schedule(team,20262027,opener=opener,now=now)
        # Recheck wall clock after acquisition, never assume the first timestamp remains valid.
        current=datetime.now(timezone.utc) if opener is None else now
        if any((utc(r["event_start_utc"])-current).total_seconds()<MIN_LEAD_SECONDS for r in rows):
            raise ForwardFeatureBlocked("NHL_SOURCE_FETCH_CROSSED_PREGAME_BOUNDARY")
        filename=f"nhl_{team}_20262027_raw.json"
        (target/filename).write_bytes(raw)
        metadata[team]={**meta,"file":filename}
        snapshots[team]=payload
    effective_at=max([now]+[utc(meta["http_date"]) for meta in metadata.values()])
    if opener is None:
        effective_at=max(effective_at,datetime.now(timezone.utc))
    if any((utc(r["event_start_utc"])-effective_at).total_seconds()<MIN_LEAD_SECONDS for r in rows):
        raise ForwardFeatureBlocked("NHL_LATEST_SOURCE_TIMESTAMP_TOO_CLOSE_TO_EVENT")
    games=parse_history(snapshots,observed_at=effective_at)
    states=advance_history(games)
    results=[]
    for row in rows:
        try:
            results.append(make_features(row,states,snapshots,observed_at=effective_at))
        except ForwardFeatureBlocked as e:
            results.append({"espn_event_id":row["espn_event_id"],"official_event_id":row["nhl_official_event_id"],"status":e.code,"detail":str(e),"probability_publishable":False,"can_execute":False})
    report={"schema_version":"WOW_NHL_PROSPECTIVE_CANDIDATE_HYDRATION_V1", "sport":"NHL",
        "capture_utc":effective_at.isoformat(),"capture_started_utc":now.isoformat(),"official_source_retrieved_during_run":True,
        "historical_asof_attestation":"CONSERVATIVE_8H_CUTOFF_NOT_INDEPENDENT_PROOF",
        "complete_32_club_source_schedules":True,"past_game_count":len(games),
        "source_manifest":metadata,"rows":results,"rows_total":len(results),
        "candidate_rows":sum(r["status"]=="NHL_CANDIDATE_FORWARD_FEATURES_RECONSTRUCTED_UNCERTIFIED" for r in results),
        "calibration_certified":False,"goaltender_status_attested":False,
        "sporting_probability_available":False,"probability_publishable":False,"can_execute":False,
        "status":"NHL_FORWARD_CANDIDATE_HYDRATION_RESEARCH_ONLY"}
    (target/"nhl_forward_candidate_feature_report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--crosswalk",type=Path,required=True)
    p.add_argument("--outdir",type=Path,required=True)
    args=p.parse_args()
    try:
        report=run(crosswalk=json.loads(args.crosswalk.read_text()),outdir=args.outdir)
        print(json.dumps({k:report[k] for k in ("status","rows_total","candidate_rows","past_game_count","can_execute","probability_publishable")},sort_keys=True))
        return 0
    except ForwardFeatureBlocked as e:
        print(json.dumps({"status":"BLOCKED","reason_code":e.code,"detail":str(e),"can_execute":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())