"""Research-only future NHL full-game totals feature hydration.

Exactly replicate the fitted prior-settled-games training features for one
real canonical FUT NHL event WITHOUT fabricating final scores or tomorrow's
results. Source snapshots must be committed and pre-kickoff; the caller must
separately verify authenticity, completeness, entitlements and starting goalies.
This component never produces/serves probabilities or places wagers.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Mapping, Sequence

from v17.nhl_total_goals_challenger import (
    _game, _timestamp, NHLTotalsResearchError,
    MIN_PRIOR, WINDOW, MODEL_FAMILY, FEATURES,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
SOURCE_ID = "NHL_PUBLIC_WEB_API"


class NHLTotalsPregameSourceError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _digest(value: Any) -> str:
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),
                             ensure_ascii=False,default=str).encode()).hexdigest()


def _is_sha(value: Any) -> bool:
    s=str(value or "").strip().lower()
    return len(s)==64 and all(c in "0123456789abcdef" for c in s)


def hydrate_nhl_total_goals_pregame(
    completed_season_games: Sequence[Mapping[str, Any]],
    official_future_event: Mapping[str, Any],
    *,
    evidence_as_of_utc: str,
) -> dict[str, Any]:
    """Calculate source-pinned research features; no prediction/qualification.

    For a genuine prospective capture, the target's first-party snapshot
    must have been obtained before the event. A synthetic replay target is
    valid only for deterministic feature parity tests, not source evidence.
    """
    if not isinstance(official_future_event, Mapping):
        raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_TARGET_INVALID","source row required")
    try:
        event_id=str(official_future_event["id"])
        season=official_future_event["season"]
        typ=official_future_event["gameType"]
        state=official_future_event["gameState"]
        home=official_future_event["homeTeam"]["abbrev"]
        away=official_future_event["awayTeam"]["abbrev"]
        kickoff=_timestamp(official_future_event["startTimeUTC"])
        as_of=_timestamp(evidence_as_of_utc)
        captured=_timestamp(official_future_event["source_captured_at_utc"])
        source_id=official_future_event["source_id"]
        snapshot_id=str(official_future_event["source_snapshot_id"]).strip()
        source_sha=official_future_event["source_payload_sha256"]
        if (len(event_id)!=10 or not event_id.isdecimal()
            or type(season) is not int or str(season)!=f"{event_id[:4]}{int(event_id[:4])+1}"
            or event_id[4:6]!="02" or typ!=2 or state!="FUT"
            or not isinstance(home,str) or not isinstance(away,str)
            or len(home)!=3 or len(away)!=3 or home==away):
            raise ValueError("official event identity/regular-season FUT state")
        if (captured>as_of or as_of>=kickoff
            or source_id!=SOURCE_ID or not snapshot_id or not _is_sha(source_sha)):
            raise ValueError("unfrozen, stale or post-start first-party source snapshot")
    except (KeyError, TypeError, ValueError, NHLTotalsResearchError, AttributeError) as exc:
        raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_TARGET_INVALID",str(exc)) from exc

    if not completed_season_games:
        raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_PRIOR_HISTORY_MISSING",event_id)
    parsed=[]
    ids=set()
    seen_time_team=set()
    for row in completed_season_games:
        try:
            g=_game(row)
        except (NHLTotalsResearchError, TypeError, AttributeError) as exc:
            raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_PRIOR_SOURCE_INVALID",str(exc)) from exc
        if g["id"] in ids or g["id"]==event_id:
            raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_DUPLICATE_EVENT",g["id"])
        ids.add(g["id"])
        if g["season"]!=int(event_id[:4]):
            raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_SEASON_CONFLICT",g["id"])
        if g["start"]>=kickoff or g["start"]>=as_of:
            raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_FUTURE_GAME_LEAKAGE",g["id"])
        for team in (g["home"],g["away"]):
            key=(g["start"],team)
            if key in seen_time_team:
                raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_OVERLAPPING_TEAMS",g["id"])
            seen_time_team.add(key)
        parsed.append(g)
    ordered=sorted(parsed,key=lambda x:(x["start"],x["id"]))
    history={home:deque(maxlen=WINDOW),away:deque(maxlen=WINDOW)}
    for g in ordered:
        if g["home"] in history:
            history[g["home"]].append((g["hgoals"],g["agoals"],g["start"],g["id"]))
        if g["away"] in history:
            history[g["away"]].append((g["agoals"],g["hgoals"],g["start"],g["id"]))
    h,a=history[home],history[away]
    if len(h)<MIN_PRIOR or len(a)<MIN_PRIOR:
        raise NHLTotalsPregameSourceError(
            "NHL_TOTALS_PREGAME_PRIOR_HISTORY_INSUFFICIENT",
            f"event={event_id};home={len(h)};away={len(a)};min={MIN_PRIOR}",
        )
    vec=(
        sum(r[0] for r in h)/len(h),sum(r[1] for r in h)/len(h),
        sum(r[0] for r in a)/len(a),sum(r[1] for r in a)/len(a),
        min(14.0,(kickoff-h[-1][2]).total_seconds()/86400),
        min(14.0,(kickoff-a[-1][2]).total_seconds()/86400),
    )
    if len(vec)!=len(FEATURES) or any(not isfinite(x) or x<0 for x in vec):
        raise NHLTotalsPregameSourceError("NHL_TOTALS_PREGAME_FEATURES_INVALID",event_id)
    audit={
        "target_official_event_id":event_id,
        "season":season,
        "home_team":home,
        "away_team":away,
        "start_time_utc":kickoff.isoformat(),
        "as_of_utc":as_of.isoformat(),
        "official_source_id":SOURCE_ID,
        "official_snapshot_id":snapshot_id,
        "official_payload_sha256":str(source_sha).lower(),
        "completed_source_games_supplied":len(parsed),
        "home_prior_games":len(h),
        "away_prior_games":len(a),
        "home_history_ids":[r[3] for r in h],
        "away_history_ids":[r[3] for r in a],
    }
    return {
        "status":"NHL_TOTALS_PREGAME_RESEARCH_FEATURES_READY_NOT_CERTIFIED",
        "model_family":MODEL_FAMILY,
        "feature_names":FEATURES,
        "features":vec,
        "feature_receipt_sha256":_digest({"features":vec,"audit":audit}),
        "evidence":audit,
        "completed_league_schedule_coverage_verified":False,
        "source_signature_independently_verified":False,
        "starting_goalies_confirmed":False,
        "fitted_model_invoked":False,
        "calibrated_lower_bound":None,
        "certified_probability_package":None,
        "model_registered_for_production":False,
        "probability_publishable":False,
        "rank_eligible":False,
        "can_execute":False,
        "terminal_authority":TERMINAL_AUTHORITY,
    }
