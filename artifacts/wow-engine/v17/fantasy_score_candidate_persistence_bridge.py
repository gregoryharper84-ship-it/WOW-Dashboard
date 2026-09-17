"""Persist immutable Fantasy Score candidate predictions during research scoring.

The base candidate scorer remains the sole fitted-model owner. This wrapper only
turns an already-frozen pregame snapshot plus a valid research candidate output
into the existing forward-evidence ledger row. It does not calibrate, certify,
promote, rank, publish, or execute anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from v17.fantasy_score_candidate_bridge import (
    is_fantasy_score_request,
    score_fantasy_candidate_research as _score_fantasy_candidate_research,
)
from v17.fantasy_score_forward_cohort_runtime import (
    LANE_SPECS,
    _build_prediction_payload,
    _persist_prediction,
)


def _lane_for_request(req: Any) -> str | None:
    sport = str(getattr(req, "sport", "") or "").strip().upper()
    stat = str(getattr(req, "stat_type", "") or "").strip().upper()
    for lane, spec in LANE_SPECS.items():
        if spec.sport == sport and spec.stat_type == stat:
            return lane
    return None


def _snapshot_for_prediction(db: Any, source_snapshot_id: str) -> dict[str, Any] | None:
    result = (
        db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,"
            "stat_type,line,hydration_status,blockers"
        )
        .eq("source_snapshot_id", source_snapshot_id)
        .limit(2)
        .execute()
    )
    rows = [dict(row) for row in (result.data or [])]
    return rows[0] if len(rows) == 1 else None


def score_fantasy_candidate_research(
    market_api: Any,
    req: Any,
    *,
    model_identity: str,
) -> dict[str, Any]:
    """Score through the frozen candidate and persist forward evidence exactly once."""
    scored = dict(
        _score_fantasy_candidate_research(
            market_api,
            req,
            model_identity=model_identity,
        )
    )
    # Candidate research is never publishable/rank-eligible/executable, even
    # when a mocked or future scorer omits the repeated top-level envelope.
    scored["probability_publishable"] = False
    scored["rank_eligible"] = False
    scored["can_execute"] = False

    traversal = dict(scored.get("backend_traversal") or {})
    source_snapshot_id = str(getattr(req, "source_snapshot_id", "") or "").strip()
    lane = _lane_for_request(req)

    if lane is None or not source_snapshot_id:
        traversal["prediction_ledger_write"] = "NOT_ATTEMPTED_SOURCE_SNAPSHOT_REQUIRED"
        scored["backend_traversal"] = traversal
        scored.setdefault("forward_evidence", {})
        scored["forward_evidence"].update(
            {
                "status": "HELD_SOURCE_SNAPSHOT_REQUIRED",
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        )
        return scored

    db = market_api.prod.get_client()
    try:
        snapshot = _snapshot_for_prediction(db, source_snapshot_id)
    except Exception as exc:
        traversal["prediction_ledger_write"] = "FAILED"
        scored["backend_traversal"] = traversal
        scored["forward_evidence"] = {
            "status": "HELD_FORWARD_SNAPSHOT_LOOKUP_FAILED",
            "error_type": type(exc).__name__,
            "blockers": ["FANTASY_SCORE_FORWARD_SNAPSHOT_LOOKUP_FAILED"],
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
        return scored

    if snapshot is None:
        traversal["prediction_ledger_write"] = "HELD_SOURCE_SNAPSHOT_NOT_FOUND"
        scored["backend_traversal"] = traversal
        scored["forward_evidence"] = {
            "status": "HELD_SOURCE_SNAPSHOT_NOT_FOUND",
            "blockers": ["FANTASY_SCORE_SOURCE_SNAPSHOT_NOT_FOUND"],
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
        return scored

    now = datetime.now(timezone.utc)
    spec = LANE_SPECS[lane]
    payload, blockers = _build_prediction_payload(
        spec=spec,
        snapshot=snapshot,
        direction=str(getattr(req, "direction", "") or "").strip().upper(),
        scored=scored,
        now=now,
    )
    if payload is None:
        traversal["prediction_ledger_write"] = "HELD_INVALID_FORWARD_PACKAGE"
        scored["backend_traversal"] = traversal
        scored["forward_evidence"] = {
            "status": "HELD_INVALID_FORWARD_PACKAGE",
            "blockers": list(blockers),
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
        return scored

    try:
        _persist_prediction(db, payload)
    except Exception as exc:
        traversal["prediction_ledger_write"] = "FAILED"
        scored["backend_traversal"] = traversal
        scored["forward_evidence"] = {
            "status": "HELD_FORWARD_PERSISTENCE_FAILED",
            "error_type": type(exc).__name__,
            "blockers": ["FANTASY_SCORE_FORWARD_PERSISTENCE_FAILED"],
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
        return scored

    traversal["prediction_ledger_write"] = "PASS"
    scored["backend_traversal"] = traversal
    scored["forward_evidence"] = {
        "status": "CAPTURED_FORWARD",
        "prediction_id": payload["prediction_id"],
        "source_snapshot_id": source_snapshot_id,
        "evidence_source_kind": payload["evidence_source_kind"],
        "calibration_status": payload["calibration_status"],
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    return scored


__all__ = [
    "is_fantasy_score_request",
    "score_fantasy_candidate_research",
]
