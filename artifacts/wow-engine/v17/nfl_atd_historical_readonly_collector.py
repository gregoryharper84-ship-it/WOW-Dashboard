"""Protected-runtime preflight for exact historical NFL touchdown grades.

This collector reads only the ten pinned retrospective prediction IDs and
their frozen source snapshots, independently fetches the two official PDFs,
then returns a SHA-pinned review-required outcome packet. No DB write path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from v17.nfl_atd_historical_grade_packet import (
    PINNED, HistoricalGradeHold, prepare_historical_packet,
)
from v17.nfl_atd_historical_source_binding import (
    prepare_exact_nfl_td_official_outcome,
)

CAN_EXECUTE = False
PREDICTION_COLUMNS = (
    "prediction_id,event_id,player,sport,stat_type,line,direction,"
    "model_provider_identity,evidence_source_kind,calibrated_probability,"
    "event_start_time,created_at,model_timestamp,locked_at,source_snapshot_id"
)
SNAPSHOT_COLUMNS = (
    "source_snapshot_id,event_id,player,captured_at,role_status,can_execute"
)

def _read(db: Any, table: str, columns: str, column: str, ids: list[str]) -> list[dict[str,Any]]:
    # The caller supplies the protected service's configured read-only DB
    # connection. No service credentials or elevated writer are read here.
    response=db.table(table).select(columns).in_(column,ids).execute()
    return [dict(r) for r in (getattr(response,"data",None) or [])]

def collect_historical_nfl_td_packet(
    *, db: Any,
    http_get: Callable[...,Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str,Any]:
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ids=sorted(PINNED)
    predictions=_read(db,"wow_predictions",PREDICTION_COLUMNS,"prediction_id",ids)
    if len(predictions)!=10:
        raise HistoricalGradeHold("PINNED_HISTORICAL_PREDICTIONS_MISSING")
    existing=_read(db,"wow_outcomes","prediction_id","prediction_id",ids)
    if existing:
        raise HistoricalGradeHold("EXISTING_OUTCOME_REQUIRES_IDEMPOTENT_RECONCILIATION")
    snapshot_ids=sorted({str(p.get("source_snapshot_id") or "") for p in predictions})
    if "" in snapshot_ids or len(snapshot_ids)!=5:
        raise HistoricalGradeHold("FIVE_FROZEN_PLAYER_MARKETS_REQUIRED")
    snapshots_list=_read(db,"wow_prop_evidence_snapshots",SNAPSHOT_COLUMNS,
                         "source_snapshot_id",snapshot_ids)
    snapshots={str(r.get("source_snapshot_id")):r for r in snapshots_list}
    if set(snapshots)!=set(snapshot_ids):
        raise HistoricalGradeHold("FROZEN_SOURCE_SNAPSHOT_MISSING")

    # Cache only source response objects within this read-only analysis call;
    # source verifier still checks each exact event and player independently.
    cache={}
    def cached_get(url: str, **kwargs: Any) -> Any:
        if url not in cache:
            cache[url]=http_get(url,**kwargs)
        return cache[url]

    verified={}
    for p in predictions:
        prediction_id=str(p["prediction_id"])
        snap=snapshots[str(p["source_snapshot_id"])]
        result=prepare_exact_nfl_td_official_outcome(
            p,snap,http_get=cached_get,now=now)
        if result.get("status")!="HOLD_PENDING_PROTECTED_SETTLEMENT_ACCEPTANCE":
            raise HistoricalGradeHold(
                "OFFICIAL_EVIDENCE_NOT_RECONCILED:"
                +prediction_id+":"+str(result.get("blocker") or result.get("status")))
        proposal=result.get("candidate_official_outcome")
        if not isinstance(proposal,dict):
            raise HistoricalGradeHold("OFFICIAL_CANDIDATE_MISSING:"+prediction_id)
        verified[prediction_id]=proposal
    packet=prepare_historical_packet(predictions,snapshots,verified,now=now)
    packet["collector"]="READ_ONLY_PROTECTED_EXACT_TEN_RECONCILIATION"
    packet["distinct_official_documents_fetched"]=len(cache)
    packet["source_record_rows_read"]=len(predictions)
    packet["historical_outcomes_persisted"]=False
    packet["can_execute"]=False
    return packet
