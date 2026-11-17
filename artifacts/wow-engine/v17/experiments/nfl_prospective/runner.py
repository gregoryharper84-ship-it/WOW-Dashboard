"""Scheduled protected-main NFL prospective shadow capture and settlement.

Research-only. Uses the existing shadow ledger, nflverse canonical snapshots and
an independent ESPN result check. Fitted parameters are trained/calibrated using
prior completed seasons and never ingest 2026 outcomes. No placing bets or
changing the controlling LLP specialist or V17_TERMINAL_REDUCER.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import argparse, csv, gzip, io, json, os, sys, urllib.request
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from prospective import (CHALLENGER_ID, CAN_EXECUTE, TERMINAL_AUTHORITY,
                         canonical_source_bytes, choose_incumbent,
                         fit_research_challenger, parse_fixture, reconcile_two_sources,
                         score_future_event, _ts)

TABLE = "wow_nfl_ml_challenger_forward_shadow"
SOURCE_TABLE = "wow_nfl_source_snapshots"
MAX_LOOKAHEAD_HOURS = 36


def paginate(q: Any, *, page_size: int = 500) -> list[dict[str,Any]]:
    output=[];start=0
    while True:
        batch=q.range(start,start+page_size-1).execute().data or []
        output.extend(dict(x) for x in batch)
        if len(batch)<page_size:break
        start+=page_size
    return output


def capture_clock_eligible(kickoff: str, now: datetime, horizon_hours: int=MAX_LOOKAHEAD_HOURS) -> bool:
    k=_ts(kickoff);n=_ts(now)
    return k is not None and n is not None and n<k<=n+timedelta(hours=horizon_hours)


def status_from_counts(eligible: int, frozen: int, existing: int, held: int) -> dict[str,Any]:
    return {"status":"NO_EVENTS_IN_CAPTURE_WINDOW" if eligible==0 else
                     ("CAPTURED" if frozen>0 else "EXISTING_OR_HELD"),
            "eligible_events":eligible,"new_forecasts":frozen,"already_frozen":existing,
            "evidence_held":held,"can_execute":False,"model_promoted":False,
            "probability_publishable":False,"certification_eligible":False,
            "terminal_authority":TERMINAL_AUTHORITY}


def make_capture_receipt(row: Mapping[str,Any], incumbent: Mapping[str,Any] | None) -> dict[str,Any]:
    out=dict(row); meta=dict(row.get("research_metadata") or {})
    if incumbent is not None:
        meta["incumbent"]={"event_prediction_id":str(incumbent["event_prediction_id"]),
                           "model_version":str(incumbent["model_version"]),
                           "calibrated_home_probability":float(incumbent["calibrated_home_probability"]),
                           "created_at":str(incumbent["created_at"]),
                           "event_start_time_utc":str(incumbent["event_start_time_utc"]),
                           "is_scientifically_certified":False}
    else:
        meta["incumbent_status"]="MATCHED_IMMUTABLE_CHAMPION_UNAVAILABLE"
    out["research_metadata"]=meta
    out["can_execute"]=False
    return out


def extract_espn_event(payload: Mapping[str,Any], fixture: Mapping[str,Any], *, raw_sha256: str) -> dict[str,Any] | None:
    """Match one ESPN final to exact NFL canonical identity and kickoff."""
    match=[]
    for evt in payload.get("events", []):
        if _ts(evt.get("date"))!=_ts(fixture.get("event_start_time_utc")):
            continue
        if evt.get("status",{}).get("type",{}).get("completed") is not True:
            continue
        comp=evt.get("competitions") or []
        if len(comp)!=1:continue
        participants=comp[0].get("competitors") or []
        if len(participants)!=2:continue
        teams={}
        for side in participants:
            key=side.get("homeAway")
            abbr=str((side.get("team") or {}).get("abbreviation") or "").upper()
            abbr={"WSH":"WAS","LAR":"LA"}.get(abbr,abbr)
            if key in ("home","away") and key not in teams:
                try:points=int(side["score"])
                except (KeyError,ValueError,TypeError):break
                teams[key]=(abbr,points)
        if set(teams)!={"home","away"}:continue
        if teams["home"][0]!=fixture.get("home_team") or teams["away"][0]!=fixture.get("away_team"):continue
        match.append(dict(event_id=str(evt.get("id") or ""),date=_ts(evt["date"]).isoformat(),
                          home_team=teams["home"][0],away_team=teams["away"][0],
                          home_score=teams["home"][1],away_score=teams["away"][1],
                          completed=True,source_sha256=raw_sha256))
    return match[0] if len(match)==1 else None


def verify_nflverse_final(fixture: Mapping[str,Any], raw_row: Mapping[str,Any],
                            training: Mapping[str,Any], snapshot: Mapping[str,Any]) -> dict[str,Any] | None:
    """Grade only the final in a freshly SHA-verified, postgame source snapshot."""
    kickoff=_ts(fixture.get("event_start_time_utc"));seen=_ts(snapshot.get("fetched_at"))
    content_hash=snapshot.get("content_sha256")
    if kickoff is None or seen is None or seen<=kickoff or not isinstance(content_hash,str) or len(content_hash)!=64:
        return None
    if any(x.get("game_id")!=fixture.get("official_event_id") for x in (raw_row,training)):
        return None
    for row in (raw_row,training):
        if (str(row.get("home_team") or "").upper()!=fixture.get("home_team") or
            str(row.get("away_team") or "").upper()!=fixture.get("away_team")):
            return None
    try:
        h,a=int(raw_row["home_score"]),int(raw_row["away_score"])
        th,ta=int(training["home_score"]),int(training["away_score"])
    except (TypeError,KeyError,ValueError):return None
    if min(h,a)<0 or (h,a)!=(th,ta):return None
    return {"game_id":fixture["official_event_id"],"home_team":fixture["home_team"],
            "away_team":fixture["away_team"],"home_score":h,"away_score":a,
            "schedule_content_sha256":content_hash}


def _schedule(db: Any) -> tuple[dict[str,Any],list[dict[str,str]]]:
    result=(db.table(SOURCE_TABLE)
              .select("snapshot_id,content_sha256,fetched_at,raw_object_uri,source_status,dataset_name")
              .eq("dataset_name","SCHEDULES").eq("source_status","CAPTURED")
              .order("fetched_at",desc=True).limit(1).execute())
    rec=result.data or []
    if len(rec)!=1:raise RuntimeError("NFL_CANONICAL_SCHEDULE_MISSING")
    snapshot=dict(rec[0]);uri=str(snapshot.get("raw_object_uri") or "")
    prefix="supabase://wow-nfl-raw/"
    if not uri.startswith(prefix):raise RuntimeError("NFL_CANONICAL_SCHEDULE_RAW_URI_INVALID")
    archive=db.storage.from_("wow-nfl-raw").download(uri[len(prefix):])
    raw=gzip.decompress(archive)
    canonical_source_bytes(raw,str(snapshot.get("content_sha256") or ""))
    reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    events=list(reader)
    if not events:raise RuntimeError("NFL_CANONICAL_SCHEDULE_EMPTY")
    return snapshot,events


def _training_inputs(db: Any) -> list[dict[str,Any]]:
    return paginate(db.table("wow_nfl_pregame_feature_rows").select(
        "game_id,season,gameday,feature_cutoff_date,max_prior_gameday,feature_order,feature_vector,target_outcome,source_content_sha256s,row_inputs_hash")
        .lte("season",2025).eq("training_eligible",True))


def _require_migration(db:Any) -> None:
    # Refuse to write without the integrity migration's sentinel column.
    db.table(TABLE).select("shadow_id,grade_source_manifest").limit(1).execute()


def run_capture(db: Any, *, now: datetime, dry_run: bool = False) -> dict[str,Any]:
    if not dry_run:_require_migration(db)
    snapshot,source_events=_schedule(db)
    at=_ts(now);fetched=_ts(snapshot["fetched_at"])
    if fetched is None or at is None or fetched>at or at-fetched>timedelta(hours=96):
        raise RuntimeError("NFL_CANONICAL_SCHEDULE_SNAPSHOT_STALE")
    window=[x for row in source_events if (x:=parse_fixture(row,at,horizon_hours=MAX_LOOKAHEAD_HOURS)) is not None]
    if len({x["official_event_id"] for x in window})!=len(window):
        raise RuntimeError("NFL_CANONICAL_DUPLICATE_FIXTURE")
    if not window:return status_from_counts(0,0,0,0)
    historical=_training_inputs(db)
    fitted=fit_research_challenger(historical)
    # Source/feature assembly must match the champion-owned prior-only builder.
    from v17.nfl_team_event_specialist import _load_history, _prior_completed_count
    from nfl_event_prediction_features import build_prediction_feature_row
    games,summaries=_load_history(db)
    existing=frozen=held=0
    for fixture in window:
        event_id=fixture["official_event_id"]
        old=(db.table(TABLE).select("shadow_id,prediction_created_at,source_feature_hash,grade_source_manifest")
             .eq("challenger_id",CHALLENGER_ID).eq("official_event_id",event_id).limit(2).execute().data or [])
        if len(old)>1:raise RuntimeError("NFL_CHALLENGER_DUPLICATE_SHADOW_IDENTITY")
        if old:existing+=1;continue
        try:
            feature=build_prediction_feature_row(training_games=games,team_summaries=summaries,
                         game_id=event_id,season=fixture["season"],week=fixture["week"],
                         gameday=fixture["gameday"],home_team=fixture["home_team"],away_team=fixture["away_team"],
                         schedule_content_sha256=snapshot["content_sha256"])
            expected_h=_prior_completed_count(source_events,season=fixture["season"],team=fixture["home_team"],
                                                target_date=datetime.fromisoformat(fixture["gameday"]).date())
            expected_a=_prior_completed_count(source_events,season=fixture["season"],team=fixture["away_team"],
                                                target_date=datetime.fromisoformat(fixture["gameday"]).date())
            if (int(feature["features"]["home_season_prior_games"])!=expected_h or
                int(feature["features"]["away_season_prior_games"])!=expected_a):
                held+=1;continue
            rec=score_future_event(fitted,fixture,feature,at,schedule_snapshot=snapshot)
        except (ValueError,KeyError,TypeError):
            held+=1;continue
        incumbent_rows=(db.table("wow_nfl_event_predictions")
                        .select("event_prediction_id,model_version,calibrated_home_probability,created_at,event_start_time_utc,official_event_id")
                        .eq("official_event_id",event_id).order("created_at",desc=True).limit(100).execute().data or [])
        inc=choose_incumbent(incumbent_rows,fixture,at)
        rec=make_capture_receipt(rec,inc)
        if not dry_run:
            inserted=(db.table(TABLE).insert(rec).execute().data or [])
            if len(inserted)!=1 or inserted[0].get("source_feature_hash")!=rec["source_feature_hash"]:
                raise RuntimeError("NFL_FROZEN_FORWARD_READBACK_FAILED")
            if _ts(inserted[0].get("created_at")) is None or _ts(inserted[0]["created_at"])>=_ts(rec["event_start_time_utc"]):
                raise RuntimeError("NFL_DB_CREATED_POST_KICKOFF")
        frozen+=1
    result=status_from_counts(len(window),frozen,existing,held)
    result["model_artifact_sha256"]=fitted["artifact_sha256"]
    result["dry_run"]=bool(dry_run)
    return result


def _espn_scoreboard(day: str) -> tuple[dict[str,Any],str]:
    from urllib.parse import urlencode
    uri="https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?"+urlencode({"dates":day.replace("-", "")})
    req=urllib.request.Request(uri,headers={"User-Agent":"WOW-V17-research-consensus/1.0"})
    with urllib.request.urlopen(req,timeout=15) as response:
        data=response.read(6_000_000+1)
    if len(data)>6_000_000:raise RuntimeError("NFL_ESPN_SOURCE_OVERSIZED")
    return json.loads(data),sha256(data).hexdigest()


def run_grade(db: Any, *, now: datetime, dry_run: bool = False) -> dict[str,Any]:
    if not dry_run:_require_migration(db)
    at=_ts(now)
    pending=paginate(db.table(TABLE).select("*").eq("challenger_id",CHALLENGER_ID).eq("lifecycle_state","FORWARD_SHADOW"))
    eligible=[r for r in pending if (t:=_ts(r.get("event_start_time_utc"))) is not None and t+timedelta(hours=3)<at]
    consensus=0;held=0;paired=[];cache={}
    # Independent raw-source replay, never trust the training-game score alone.
    snapshot,raw_events=_schedule(db)
    raw_by_id={}
    for raw in raw_events:
        event_id=str(raw.get("game_id") or "")
        if event_id in raw_by_id:
            raise RuntimeError("NFL_SETTLEMENT_CANONICAL_SOURCE_DUPLICATE")
        raw_by_id[event_id]=raw
    for frozen in eligible:
        event_id=frozen["official_event_id"]
        result=db.table("wow_nfl_training_games").select(
             "game_id,home_team,away_team,home_score,away_score,schedule_content_sha256").eq("game_id",event_id).limit(2).execute()
        games=result.data or []
        if len(games)!=1:held+=1;continue
        official=verify_nflverse_final(frozen,raw_by_id.get(event_id) or {},games[0],snapshot)
        if official is None:held+=1;continue
        kickoff=_ts(frozen["event_start_time_utc"])
        day=kickoff.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        if day not in cache:
            try:cache[day]=_espn_scoreboard(day)
            except (OSError,ValueError,RuntimeError):
                cache[day]=None
        if cache[day] is None:held+=1;continue
        payload,digest=cache[day]
        espn=extract_espn_event(payload,frozen,raw_sha256=digest)
        if not espn:held+=1;continue
        grade=reconcile_two_sources(frozen,official,espn,at)
        if grade is None:held+=1;continue
        if not dry_run:
            updated=(db.table(TABLE).update(grade).eq("shadow_id",frozen["shadow_id"])
                     .eq("lifecycle_state","FORWARD_SHADOW").execute().data or [])
            if len(updated)!=1 or updated[0].get("outcome")!=grade["outcome"]:
                raise RuntimeError("NFL_GRADE_ATOMIC_UPDATE_FAILED")
        consensus+=1
        incumbent=(frozen.get("research_metadata") or {}).get("incumbent")
        if incumbent and 0<=float(incumbent.get("calibrated_home_probability",-1))<=1:
            home_win=int(int(games[0]["home_score"])>int(games[0]["away_score"]))
            p=float(incumbent["calibrated_home_probability"])
            # Same event/outcome, frozen incumbent as captured before kickoff.
            challenger_home=(float(frozen["calibrated_probability"]) if frozen["selected_participant"]==frozen["home_team"]
                             else 1-float(frozen["calibrated_probability"]))
            paired.append((challenger_home-home_win)**2-(p-home_win)**2)
    return {"status":"POSTGAME_TWO_SOURCE_RESEARCH_ONLY", "eligible":len(eligible),
            "newly_graded":consensus,"held_unverified":held,"matched_paired":len(paired),
            "paired_brier_delta_mean":sum(paired)/len(paired) if paired else None,
            "can_execute":False,"probability_publishable":False,"certification_eligible":False,
            "terminal_authority":TERMINAL_AUTHORITY,"dry_run":bool(dry_run)}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("capture","grade"))
    p.add_argument("--dry-run",action="store_true")
    p.add_argument("--out",type=Path)
    args=p.parse_args()
    from supabase import create_client
    key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    url=os.getenv("SUPABASE_URL")
    if not key or not url:raise SystemExit("NFL_SHADOW_SUPABASE_CREDENTIAL_UNAVAILABLE")
    db=create_client(url,key)
    now=datetime.now(timezone.utc)
    report=run_capture(db,now=now,dry_run=args.dry_run) if args.mode=="capture" else run_grade(db,now=now,dry_run=args.dry_run)
    text=json.dumps(report,sort_keys=True,allow_nan=False)
    if args.out:args.out.write_text(text+"\n",encoding="utf-8")
    print(text)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
