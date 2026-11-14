"""Ten fixed historical NFL ANYTIME_TD receipts; never forward training.

Issue #1942. Ordinary pregame predictions have evidence_source_kind NULL.
Keep the forward-only collector intact. This module only creates reviewable
historical outcome proposals, not database writes or wagering instructions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
COHORT = "NFL_ATD_2026_W06_HISTORICAL_EXACT10_V1"
PINNED = {
 "6e69c948-7afb-570e-95f8-de24735fa0fd": ("2026_06_CAR_PHI","Bryce Young","LESS"),
 "7e2f92ba-bb59-5bc0-827e-f245c893f890": ("2026_06_CAR_PHI","Bryce Young","MORE"),
 "e73629da-ca2f-527d-8424-b7b10a54d64d": ("2026_06_CAR_PHI","David Moore","LESS"),
 "9002f2c2-f95f-52ea-9d91-d420fa4fb50a": ("2026_06_CAR_PHI","David Moore","MORE"),
 "f472237c-78f4-5c68-98ff-fc699d05e9cf": ("2026_06_CAR_PHI","Tetairoa McMillan","LESS"),
 "8762979f-1c25-591f-b5b6-6ec323ef76b4": ("2026_06_CAR_PHI","Tetairoa McMillan","MORE"),
 "dcd5a1ea-8c00-5529-8d4e-b5e0fa465937": ("2026_06_CHI_ATL","Austin Hooper","LESS"),
 "72dbc1a9-59ae-565f-97cd-0797b65a9738": ("2026_06_CHI_ATL","Austin Hooper","MORE"),
 "77cb462e-1b79-5a96-bf7b-1868aa56fdb0": ("2026_06_CHI_ATL","Kalif Raymond","LESS"),
 "e8a785a7-51ca-56e0-b7ef-cf20143628eb": ("2026_06_CHI_ATL","Kalif Raymond","MORE"),
}
SOURCES = {
 "2026_06_CAR_PHI":"https://www.nflgsis.com/2026/REG/06/60264/Gamebook.pdf",
 "2026_06_CHI_ATL":"https://static.www.nfl.com/image/upload/v1792409927/gamecenter/aab577eb-4feb-11f1-abca-2c54536568a9.pdf",
}

class HistoricalGradeHold(ValueError):
    pass

def require(predicate: bool, code: str) -> None:
    if not predicate:
        raise HistoricalGradeHold(code)

def utc(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError) as exc:
        raise HistoricalGradeHold("PREGAME_TIMESTAMP_INVALID") from exc
    require(dt.utcoffset() is not None, "PREGAME_TIMESTAMP_NAIVE")
    return dt.astimezone(timezone.utc)

def decimal(value: Any) -> Decimal:
    try:
        d=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc:
        raise HistoricalGradeHold("NUMBER_INVALID") from exc
    require(d.is_finite(), "NUMBER_NONFINITE")
    return d

def prepare_historical_packet(
    predictions: Sequence[Mapping[str,Any]],
    snapshots: Mapping[str,Mapping[str,Any]],
    official_candidates: Mapping[str,Mapping[str,Any]],
    *, now: datetime,
) -> dict[str,Any]:
    """Never reads/writes DB. Enforces all ten exact immutable receipt identities.

    Authoritative scorer/participation candidate source is the separately tested
    official NFL PDF verifier, not sportsbook implied probabilities or a narrative.
    """
    current=utc(now)
    require(len(predictions)==10, "EXACT_TEN_PREDICTIONS_REQUIRED")
    rows: dict[str,Mapping[str,Any]]={}
    for p in predictions:
        ident=str(p.get("prediction_id") or "")
        require(ident in PINNED and ident not in rows,"PINNED_PREDICTION_ID_INVALID")
        rows[ident]=p
    require(set(rows)==set(PINNED),"PINNED_PREDICTION_SET_INCOMPLETE")
    require(set(official_candidates)==set(PINNED),"EXACT_TEN_OFFICIAL_PROPOSALS_REQUIRED")
    paired=defaultdict(list)
    source_versions=defaultdict(set)
    snapshot_pairs=defaultdict(set)

    for ident,(event,player,side) in sorted(PINNED.items()):
        p=rows[ident]
        require(p.get("evidence_source_kind") is None,
                "NOT_HISTORICAL_DO_NOT_RECLASSIFY_FORWARD")
        require(p.get("can_execute") is not True, "EXECUTION_FORBIDDEN")
        require(p.get("model_provider_identity")=="WOW_PROP_FITTED_MODEL_V1",
                "MODEL_PROVIDER_INVALID")
        require(str(p.get("sport")).upper()=="NFL" and
                str(p.get("stat_type")).upper()=="ANYTIME_TD","STAT_ROUTE_INVALID")
        require((p.get("event_id"),p.get("player"),p.get("direction")) ==
                (event,player,side),"IMMUTABLE_PREDICTION_IDENTITY_INVALID")
        require(decimal(p.get("line"))==Decimal(".5"),"EXACT_HALF_TD_LINE_REQUIRED")
        prob=decimal(p.get("calibrated_probability"))
        require(Decimal(0)<prob<Decimal(1),"CALIBRATED_PROBABILITY_INVALID")
        start=utc(p.get("event_start_time"))
        require(start<current and
                all(utc(p.get(col))<start for col in
                    ("created_at","model_timestamp","locked_at")),
                "IMMUTABLE_PREGAME_TIME_INVALID")
        sid=str(p.get("source_snapshot_id") or "")
        s=snapshots.get(sid)
        require(bool(sid) and isinstance(s,Mapping),"FROZEN_SNAPSHOT_REQUIRED")
        role=s.get("role_status")
        require(isinstance(role,Mapping) and
                role.get("identity_binding_status")=="PASS" and
                role.get("verified_canonical_event_id")==event and
                role.get("player")==player and
                s.get("event_id")==event and s.get("player")==player,
                "PREGAME_ROLE_IDENTITY_CONFLICT")
        require(utc(s.get("captured_at"))<start,"FROZEN_SNAPSHOT_AFTER_KICKOFF")
        snapshot_pairs[(event,player)].add(sid)

        result=official_candidates[ident]
        require(result.get("status")==
                "PROPOSAL_REQUIRES_INDEPENDENT_SETTLEMENT_ACCEPTANCE",
                "OFFICIAL_PROPOSAL_NOT_ACCEPTABLE")
        require((result.get("prediction_id"),result.get("event_id"),
                 result.get("player"),result.get("direction"),
                 result.get("snapshot_id"))==(ident,event,player,side,sid),
                "OFFICIAL_PROPOSAL_RECEIPT_IDENTITY_CONFLICT")
        require(decimal(result.get("line"))==Decimal(".5") and
                result.get("actual_stat")==0 and
                result.get("hypothetical_official_result")==
                   ("HIT" if side=="LESS" else "MISS"),
                "OFFICIAL_SCORER_RESULT_CONTRADICTORY")
        require(result.get("source_url")==SOURCES[event] and
                result.get("participation_evidence")==
                  "POSITIVE_OFFICIAL_FINAL_GAME_STAT" and
                result.get("settlement_persisted") is False and
                result.get("can_execute") is False,
                "OFFICIAL_SOURCE_OR_PARTICIPATION_INVALID")
        digest=str(result.get("source_sha256") or "")
        require(len(digest)==64 and all(x in "0123456789abcdef" for x in digest),
                "OFFICIAL_GAMEBOOK_DIGEST_MISSING")
        source_versions[event].add(digest)
        paired[(event,player)].append((side,prob,ident,result))
    require(len(paired)==5 and all(len(items)==2 for items in paired.values()),
            "FIVE_UNIQUE_MARKETS_REQUIRED")
    require(all(len(values)==1 for values in source_versions.values()),
            "PAIR_GAMEBOOK_VERSION_CONFLICT")
    require(all(len(values)==1 for values in snapshot_pairs.values()),
            "PAIR_SNAPSHOT_CONFLICT")
    for event_player, pair in paired.items():
        require({x[0] for x in pair}=={"MORE","LESS"},"PAIR_DIRECTIONS_INVALID")
        require(abs(sum((x[1] for x in pair),Decimal(0))-Decimal(1))<=Decimal("0.00000001"),
                "PAIRED_CALIBRATION_INCONSISTENT")
    outcomes=[]
    for event_player,pair in sorted(paired.items()):
        for side,prob,ident,result in sorted(pair):
            hit=side=="LESS"
            outcomes.append({
                "prediction_id":ident,"official_result":"HIT" if hit else "MISS",
                "actual_stat":0,"hit":hit,"push":False,"void":False,
                "settlement_source":result["source_url"]+"#sha256="+result["source_sha256"],
            })
    fingerprints={event:next(iter(v)) for event,v in sorted(source_versions.items())}
    canonical=json.dumps({"cohort":COHORT,"fingerprints":fingerprints,"rows":outcomes},
                         separators=(",",":"),sort_keys=True)
    return {
        "status":"HISTORICAL_POSTMORTEM_OUTCOME_PACKET_REVIEW_REQUIRED",
        "cohort":COHORT,"immutable_packet_sha256":sha256(canonical.encode()).hexdigest(),
        "original_prediction_count":10,"underlying_player_markets":5,
        "source_digests":fingerprints,"candidate_outcome_rows":outcomes,
        "historical_outcomes_persisted":False,
        "forward_calibration_eligible":False,
        "relabel_evidence_source_kind":False,
        "model_probability_modified":False,
        "release_authorized":False,"can_execute":False,
    }
