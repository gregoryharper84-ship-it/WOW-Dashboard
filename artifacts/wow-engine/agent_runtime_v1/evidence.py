from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from .contracts import canonical_hash

REQUIRED_TOP_LEVEL=("candidate_identity","official_event","exact_market_identity","game_log","box_score_log","role_status","role_timestamp","source_attempts")

@dataclass(frozen=True)
class SealedEvidence:
    evidence_snapshot_id:str
    payload:dict[str,Any]
    payload_hash:str
    missing_required_fields:tuple[str,...]
    source_conflicts:tuple[str,...]

def validate_evidence_payload(payload:dict[str,Any])->tuple[list[str],list[str]]:
    missing=[k for k in REQUIRED_TOP_LEVEL if k not in payload]
    conflicts=list(payload.get("source_conflicts") or [])
    game_log=payload.get("game_log"); box_log=payload.get("box_score_log")
    if not isinstance(game_log,list) or any(isinstance(x,bool) or not isinstance(x,(int,float)) for x in (game_log or [])):
        missing.append("game_log:list[number]")
    if not isinstance(box_log,list) or any(not isinstance(x,dict) for x in (box_log or [])):
        missing.append("box_score_log:list[dict]")
    if game_log is box_log: conflicts.append("GAME_LOG_BOX_SCORE_ALIAS_PROHIBITED")
    attempts=payload.get("source_attempts")
    if not isinstance(attempts,list) or not attempts: missing.append("source_attempts:nonempty")
    try:
        dt=datetime.fromisoformat(str(payload.get("role_timestamp")).replace("Z","+00:00"))
        if dt.tzinfo is None: raise ValueError
    except Exception: missing.append("role_timestamp:aware_iso")
    return sorted(set(missing)),sorted(set(conflicts))

def seal_evidence(evidence_snapshot_id:str,payload:dict[str,Any])->SealedEvidence:
    missing,conflicts=validate_evidence_payload(payload)
    return SealedEvidence(evidence_snapshot_id,payload,canonical_hash(payload),tuple(missing),tuple(conflicts))
