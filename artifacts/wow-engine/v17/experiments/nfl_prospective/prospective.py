"""Prospective NFL candidate freeze and two-provider settlement research.

The opponent-aware model is the Nov 17 L2 + Platt retrospective research challenger,
refitted only on years strictly prior to any future target. No published picks,
model promotion, orders or implicit certification. Consumers supply canonical
source bytes and truthful invocation time; the persistence layer enforces clocks.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import exp, isfinite, log, sqrt
from random import Random
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo
import json

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CHALLENGER_ID = "NFL_PROSPECTIVE_PRIOR_OPPONENT_LOGIT_PLATT_2021_2025_V1"
FEATURE_SCHEMA_VERSION = "NFL_EVENT_PREGAME_PRIOR_V1"
_MODEL_SPLIT = (2024, 2025)


def _ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if not isinstance(value, str):
        return None
    try:
        x = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return x.astimezone(timezone.utc) if x.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (float, int)):
        return None
    try: v = float(x)
    except (ValueError, OverflowError): return None
    return v if isfinite(v) else None


def _sha(x: Any) -> bool:
    return isinstance(x, str) and len(x) == 64 and all(c in "0123456789abcdef" for c in x)


def _digest(v: Any) -> str:
    return sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sigmoid(v: float) -> float:
    return 1 / (1 + exp(-min(35., max(-35., v))))


def _clip(p: float) -> float:
    return min(1-1e-6, max(1e-6, p))


def canonical_source_bytes(raw: bytes, claimed_sha256: str) -> bytes:
    """Raw historical source is a gzip-wrapped exact CSV; hash the original CSV."""
    if not isinstance(raw, bytes) or not _sha(claimed_sha256) or sha256(raw).hexdigest() != claimed_sha256:
        raise ValueError("NFL_SCHEDULE_SOURCE_HASH_MISMATCH")
    return raw


def parse_fixture(row: Mapping[str, Any], now: datetime, *, horizon_hours: int = 36) -> dict[str, Any] | None:
    """Accept only unstarted canonical regular-season games with exact ET kickoff."""
    if not isinstance(row, Mapping) or now.tzinfo is None or horizon_hours < 1:
        return None
    try:
        if str(row.get("game_type") or "REG").upper() != "REG": return None
        if any(str(row.get(x) or "").strip() for x in ("home_score", "away_score")): return None
        event_id = str(row["game_id"]).strip()
        home, away = str(row["home_team"]).upper().strip(), str(row["away_team"]).upper().strip()
        day = date.fromisoformat(str(row["gameday"])[:10]); season = int(row["season"]); week = int(row["week"])
        time_str = str(row["gametime"])
        hours, minutes = (int(x) for x in time_str.split(":"))
        if not (0 <= hours <= 23 and 0 <= minutes <= 59) or not home or not away or home == away:
            return None
        if event_id != f"{season}_{week:02d}_{away}_{home}" and event_id != f"{season}_{week}_{away}_{home}":
            return None
        kickoff = datetime(day.year,day.month,day.day,hours,minutes,tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        if not (now.astimezone(timezone.utc) < kickoff <= now.astimezone(timezone.utc)+timedelta(hours=horizon_hours)):
            return None
        return dict(official_event_id=event_id,event_start_time_utc=kickoff.isoformat(),
                    home_team=home,away_team=away,season=season,week=week,gameday=day.isoformat())
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def _fit_rows(rows: Sequence[Mapping[str, Any]], *, min_fit: int, min_cal: int):
    if not isinstance(rows,(tuple,list)) or min_fit < 1 or min_cal < 1: raise ValueError("NFL_TRAINING_ROWS_INVALID")
    names=None;out=[];seen=set()
    for r in rows:
        if not isinstance(r,Mapping):raise ValueError("NFL_FEATURE_ROW_INVALID")
        season=r.get("season");ident=r.get("game_id"); vector=r.get("feature_vector"); order=r.get("feature_order")
        if type(season) is not int or not isinstance(ident,str) or not ident or ident in seen:
            raise ValueError("NFL_FEATURE_DUPLICATE_OR_IDENTITY_INVALID")
        seen.add(ident)
        if season>_MODEL_SPLIT[1]:continue  # Never train or calibrate on 2026 outcome labels.
        if r.get("target_outcome") not in ("HOME_WIN","AWAY_WIN") or not isinstance(order,list) or not order:
            raise ValueError("NFL_FEATURE_LABEL_OR_ORDER_INVALID")
        if names is None:names=order
        if names!=order or not isinstance(vector,list) or len(vector)!=len(order) or any(_num(x) is None for x in vector):
            raise ValueError("NFL_FEATURE_SCHEMA_INVALID")
        try:
            target=date.fromisoformat(str(r["gameday"])); prior=date.fromisoformat(str(r["max_prior_gameday"]));cutoff=date.fromisoformat(str(r["feature_cutoff_date"]))
        except (KeyError,ValueError,TypeError):raise ValueError("NFL_FEATURE_CHRONOLOGY_INVALID") from None
        if prior>=target or cutoff!=target:raise ValueError("NFL_FEATURE_CHRONOLOGY_INVALID")
        hashes=r.get("source_content_sha256s")
        if not isinstance(hashes,list) or not hashes or any(not _sha(h) for h in hashes) or not _sha(r.get("row_inputs_hash")):
            raise ValueError("NFL_FEATURE_SOURCE_MANIFEST_INVALID")
        out.append(dict(id=ident,year=season,x=[float(v) for v in vector],y=int(r["target_outcome"]=="HOME_WIN"),
                        hash=r["row_inputs_hash"],source_hashes=hashes))
    fit=[r for r in out if r["year"]<=_MODEL_SPLIT[0]]
    cal=[r for r in out if r["year"]==_MODEL_SPLIT[1]]
    if len(fit)<min_fit or len(cal)<min_cal:
        raise ValueError("NFL_TRAIN_OR_CALIBRATION_THIN")
    return names,fit,cal


def fit_research_challenger(rows: Sequence[Mapping[str, Any]], *, min_fit: int=500,min_cal: int=100,
                            fit_steps: int=1800,cal_steps: int=1200) -> dict[str, Any]:
    if not (1<=fit_steps<=1800 and 1<=cal_steps<=1200):raise ValueError("NFL_FIT_ITERATION_INVALID")
    names,fit,cal = _fit_rows(rows,min_fit=min_fit,min_cal=min_cal)
    d=len(names)
    mu=[sum(r["x"][j] for r in fit)/len(fit) for j in range(d)]
    sd=[sqrt(sum((r["x"][j]-mu[j])**2 for r in fit)/len(fit)) or 1. for j in range(d)]
    if not all(isfinite(v) for v in mu+sd):raise ValueError("NFL_FIT_NUMERICAL_FAILURE")
    def norm(r):return [max(-7.,min(7.,(r["x"][j]-mu[j])/sd[j])) for j in range(d)]
    for r in fit+cal:r["z"]=norm(r)
    weights=[0.0]*(d+1)
    for step in range(fit_steps):
        grad=[0.0]*(d+1)
        for r in fit:
            err=_sigmoid(weights[0]+sum(weights[j+1]*r["z"][j] for j in range(d)))-r["y"]
            grad[0]+=err
            for j in range(d):grad[j+1]+=err*r["z"][j]
        rate=.13/(1+step/300)
        for j in range(d+1):weights[j]-=rate*(grad[j]/len(fit)+(.016*weights[j] if j else 0))
    def raw(r):return _clip(_sigmoid(weights[0]+sum(weights[j+1]*r["z"][j] for j in range(d))))
    a,b=1.,0.
    for step in range(cal_steps):
        ga=gb=0.0
        for r in cal:
            p=raw(r);v=log(p/(1-p));err=_sigmoid(a*v+b)-r["y"]
            ga+=err*v;gb+=err
        rate=.025/(1+step/400)
        a-=rate*(ga/len(cal)+.015*(a-1));b-=rate*(gb/len(cal)+.015*b)
    if not all(isfinite(v) for v in weights+mu+sd+[a,b]):raise ValueError("NFL_FIT_NUMERICAL_FAILURE")
    artifact={"feature_order":names,"mu":mu,"sd":sd,"weights":weights,"platt_a":a,"platt_b":b,
              "train_seasons":"2021-2024","calibration_season":2025,"fit_steps":fit_steps,"cal_steps":cal_steps,
              "train_hashes":sorted([r["hash"] for r in fit]),"calibration_hashes":sorted([r["hash"] for r in cal])}
    return {"model":artifact,"artifact_sha256":_digest(artifact),"training_rows":len(fit),"calibration_rows":len(cal),
            "calibration_decisions":[(max(p,1-p),int((p>=.5)==bool(r["y"]))) for r in cal for p in [_clip(_sigmoid(a*log(raw(r)/(1-raw(r)))+b))]],
            "can_execute":False,"probability_publishable":False,"automatic_promotion":False,
            "terminal_authority":TERMINAL_AUTHORITY}


def _model_predict(fit: Mapping[str, Any],vector: Sequence[float]) -> float:
    m=fit["model"]
    if len(vector)!=len(m["feature_order"]) or any(_num(v) is None for v in vector):
        raise ValueError("NFL_TARGET_FEATURE_VECTOR_INVALID")
    z=[max(-7.,min(7.,(v-mu)/sd)) for v,mu,sd in zip(vector,m["mu"],m["sd"])]
    p=_clip(_sigmoid(m["weights"][0]+sum(w*x for w,x in zip(m["weights"][1:],z))))
    return _clip(_sigmoid(m["platt_a"]*log(p/(1-p))+m["platt_b"]))


def _calibration_lowers(p: float,fit: Mapping[str, Any], *, k: int=50,repetitions: int=160) -> tuple[float,float,float]:
    """Research heuristic only: Wilson k=50 and contiguous-block calibration bootstrap.

    These bounds are not independently certified, so must never be published.
    """
    decisions=fit["calibration_decisions"]
    if len(decisions)<k:raise ValueError("NFL_LOCAL_CALIBRATION_TOO_THIN")
    near=sorted(decisions,key=lambda z:abs(z[0]-p))[:k]
    hits=sum(hit for _,hit in near)
    z=1.645;ph=hits/k;z2=z*z
    wil=(ph+z2/(2*k)-z*sqrt((ph*(1-ph)+z2/(4*k))/k))/(1+z2/k)
    wil=max(0.,min(p,wil))
    rng=Random(int(fit["artifact_sha256"][:12],16)^int(round(p*1_000_000)))
    values=[];n=len(decisions)
    for _ in range(repetitions):
        sampled=[]
        while len(sampled)<n:
            start=rng.randrange(n)
            sampled.extend(decisions[(start+i)%n] for i in range(32))
        local=sorted(sampled[:n],key=lambda z:abs(z[0]-p))[:k]
        values.append(min(p,sum(v[1] for v in local)/k))
    values.sort();bootstrap=values[int(.10*(len(values)-1))]
    return wil,bootstrap,min(p,wil,bootstrap)


def score_future_event(fit: Mapping[str, Any],fixture: Mapping[str, Any],feature: Mapping[str, Any],now: datetime,
                       *, schedule_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    start=_ts(fixture.get("event_start_time_utc"));ts=_ts(now)
    if start is None or ts is None or start<=ts:raise ValueError("NFL_PREGAME_CUTOFF_INVALID")
    fetched=_ts(schedule_snapshot.get("fetched_at"))
    if fetched is None or fetched>ts or ts-fetched>timedelta(hours=96) or not _sha(schedule_snapshot.get("content_sha256")):
        raise ValueError("NFL_SOURCE_SNAPSHOT_STALE_OR_INVALID")
    if feature.get("game_id")!=fixture.get("official_event_id") or feature.get("home_team")!=fixture.get("home_team") or feature.get("away_team")!=fixture.get("away_team") or feature.get("training_eligible") is not True:
        raise ValueError("NFL_TARGET_EVENT_FEATURE_MISMATCH")
    try:
        prior=date.fromisoformat(str(feature["max_prior_gameday"]));target=date.fromisoformat(str(feature["gameday"]))
    except (KeyError,ValueError,TypeError):raise ValueError("NFL_FEATURE_CHRONOLOGY_INVALID") from None
    if prior>=target or feature.get("feature_order")!=fit["model"]["feature_order"] or feature.get("gameday")!=fixture.get("gameday"):
        raise ValueError("NFL_FEATURE_CHRONOLOGY_INVALID")
    if not _sha(feature.get("row_inputs_hash")) or not isinstance(feature.get("source_content_sha256s"),list) or not all(_sha(x) for x in feature["source_content_sha256s"]):
        raise ValueError("NFL_FEATURE_SOURCE_MANIFEST_INVALID")
    home_p=_model_predict(fit,feature["feature_vector"])
    selected_home=home_p>=.5;selected=fixture["home_team"] if selected_home else fixture["away_team"]
    p=max(home_p,1-home_p)
    low,boot,composite=_calibration_lowers(p,fit,k=min(50,fit["calibration_rows"]))
    metadata={"status":"PREGAME_SHADOW_UNCERTIFIED","source_snapshot_id":str(schedule_snapshot.get("snapshot_id") or ""),
              "source_snapshot_fetched_at":fetched.isoformat(),"source_snapshot_sha256":schedule_snapshot["content_sha256"],
              "feature_row_inputs_hash":feature["row_inputs_hash"],"feature_source_sha256s":feature["source_content_sha256s"],
              "model_artifact_sha256":fit["artifact_sha256"],"model_training_n":fit["training_rows"],
              "model_calibration_n":fit["calibration_rows"],"calibrated_home_probability":home_p,
              "lower_bound_scientifically_certified":False}
    # The isolated capture runner emits the exact existing forward-shadow table
    # contract. It never imports production inference or modifies the champion.
    return {"challenger_id":CHALLENGER_ID,"official_event_id":fixture["official_event_id"],
            "provider_event_id":fixture["official_event_id"],
            "event_start_time_utc":start.isoformat(),"prediction_created_at":ts.isoformat(),
            "home_team":fixture["home_team"],"away_team":fixture["away_team"],
            "selected_participant":selected,"calibrated_probability":p,
            "local_wilson_lower":low,"bootstrap_q10_lower":boot,
            "composite_lower_bound":composite,
            "lower_bound_method":"CALIBRATION_2025_LOCAL_WILSON90_K50_BLOCK32_Q10_RESEARCH_V1",
            "feature_schema_version":FEATURE_SCHEMA_VERSION,"model_family":CHALLENGER_ID,
            "calibration_method":"PLATT_TIME_SPLIT_V1","training_cutoff":"2024-12-31",
            "calibration_season":2025,"source_feature_hash":feature["row_inputs_hash"],
            "research_metadata":metadata,"lifecycle_state":"FORWARD_SHADOW",
            "probability_publishable":False,"promotion_authorized":False,
            "can_execute":False,"terminal_authority":TERMINAL_AUTHORITY}


def choose_incumbent(rows: Sequence[Mapping[str, Any]],fixture: Mapping[str, Any],now: datetime) -> Mapping[str, Any] | None:
    start=_ts(fixture.get("event_start_time_utc"));at=_ts(now)
    if start is None or at is None:return None
    valid=[]
    for r in rows:
        p=_num(r.get("calibrated_home_probability"));when=_ts(r.get("created_at"))
        if (r.get("official_event_id")!=fixture.get("official_event_id") or _ts(r.get("event_start_time_utc"))!=start
                or p is None or p<0 or p>1 or when is None or when>=start or when>at):continue
        valid.append(r)
    return max(valid,key=lambda r:_ts(r["created_at"])) if valid else None


def immutable_grade_fields() -> set[str]:
    return {"outcome","brier","log_loss","hit","graded_at","lifecycle_state","grade_source_manifest"}


def reconcile_two_sources(frozen: Mapping[str, Any], nflverse: Mapping[str, Any], espn: Mapping[str, Any],now: datetime) -> dict[str, Any] | None:
    """NFLverse+ESPN score agreement is independent reconciliation, NOT NFL-official attestation."""
    start=_ts(frozen.get("event_start_time_utc"));at=_ts(now)
    if start is None or at is None or at<=start or frozen.get("lifecycle_state")!="FORWARD_SHADOW":return None
    created=_ts(frozen.get("created_at"))
    if (created is None or created>=start or not _ts(frozen.get("prediction_created_at"))
            or _ts(frozen["prediction_created_at"])>=start):return None
    if nflverse.get("game_id")!=frozen.get("official_event_id") or espn.get("completed") is not True:
        return None
    if not isinstance(espn.get("event_id"),str) or not espn["event_id"] or not _sha(espn.get("source_sha256")) or not _sha(nflverse.get("schedule_content_sha256")):
        return None
    if _ts(espn.get("date"))!=start:return None
    for source in (nflverse,espn):
        if source.get("home_team")!=frozen.get("home_team") or source.get("away_team")!=frozen.get("away_team"):
            return None
    try:
        h=int(nflverse["home_score"]);a=int(nflverse["away_score"]);eh=int(espn["home_score"]);ea=int(espn["away_score"])
    except (ValueError,TypeError,KeyError):return None
    if h<0 or a<0 or (h,a)!=(eh,ea) or h==a:return None
    side=frozen.get("selected_participant")
    if side not in (frozen.get("home_team"),frozen.get("away_team")):return None
    y=int((h>a and side==frozen["home_team"]) or (a>h and side==frozen["away_team"]))
    p=_num(frozen.get("calibrated_probability"))
    if p is None or p<0 or p>1:return None
    from math import log
    p2=_clip(p)
    return {"outcome":y,"brier":(p-y)**2,"log_loss":-(y*log(p2)+(1-y)*log(1-p2)),
            "hit":bool(y),"graded_at":at.isoformat(),"lifecycle_state":"GRADED",
            "grade_source_manifest":{"status":"TWO_PROVIDER_RECONCILED_NOT_LEAGUE_CERTIFIED",
                "sources":["ESPN","NFLVERSE"],"nflverse_content_sha256":nflverse["schedule_content_sha256"],
                "espn_raw_sha256":espn["source_sha256"],"espn_event_id":espn["event_id"],
                "home_score":h,"away_score":a}}
