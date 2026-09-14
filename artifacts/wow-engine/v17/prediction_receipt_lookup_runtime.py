"""Read-only lookup for exact immutable V17 pregame prediction receipts.

Postmortem grading must be able to recover the same persisted governed record
that existed before the event.  This module exposes a narrow authenticated
lookup over ``wow_predictions``.  It never creates, edits, upgrades, or repairs
a prediction and never derives a probability from settlement results.

The lookup is intentionally fail-closed:
- exact ``prediction_id`` is preferred when available;
- otherwise callers provide exact thesis identity fields;
- ambiguous matches are returned as ambiguous rather than guessed;
- only rows that prove a persisted pregame lock are marked immutable;
- ``can_execute`` is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


PREDICTION_TABLE = "wow_predictions"
_SELECT_FIELDS = ",".join(
    [
        "prediction_id",
        "created_at",
        "event_id",
        "event_start_time",
        "model_timestamp",
        "locked_at",
        "player",
        "team",
        "opponent",
        "sport",
        "market_type",
        "stat_type",
        "line",
        "direction",
        "source_snapshot_id",
        "raw_model_probability",
        "independent_model_probability",
        "calibrated_probability",
        "calibrated_probability_lower_bound",
        "calibrated_probability_upper_bound",
        "calibration_status",
        "calibration_method",
        "calibration_version",
        "probability_publishable",
        "probability_ceiling",
        "money_lane_status",
        "data_gaps",
        "blockers",
    ]
)


class PredictionReceiptLookupRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: Optional[str] = None
    prediction_id: Optional[str] = None
    event_id: Optional[str] = None
    sport: Optional[str] = None
    player: Optional[str] = None
    team: Optional[str] = None
    opponent: Optional[str] = None
    stat_type: Optional[str] = None
    line: Optional[float] = None
    direction: Optional[str] = None
    event_start_min: Optional[str] = None
    event_start_max: Optional[str] = None


class PredictionReceiptLookupBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Optional[str] = None
    rows: list[PredictionReceiptLookupRow] = Field(min_length=1, max_length=50)


def _aware(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _immutable_pregame(row: dict[str, Any]) -> bool:
    locked = _aware(row.get("locked_at"))
    created = _aware(row.get("created_at"))
    model_ts = _aware(row.get("model_timestamp"))
    event_start = _aware(row.get("event_start_time"))
    if locked is None or created is None or model_ts is None or event_start is None:
        return False
    return created < event_start and model_ts < event_start and locked <= event_start


def _query_for(db: Any, request: PredictionReceiptLookupRow) -> Any:
    query = db.table(PREDICTION_TABLE).select(_SELECT_FIELDS)

    if _text(request.prediction_id):
        return query.eq("prediction_id", _text(request.prediction_id)).limit(2)

    exact_filters = 0
    if _text(request.event_id):
        query = query.eq("event_id", _text(request.event_id))
        exact_filters += 1
    if _text(request.sport):
        query = query.eq("sport", _text(request.sport).upper())
        exact_filters += 1
    if _text(request.player):
        query = query.eq("player", _text(request.player))
        exact_filters += 1
    if _text(request.team):
        query = query.eq("team", _text(request.team))
        exact_filters += 1
    if _text(request.opponent):
        query = query.eq("opponent", _text(request.opponent))
        exact_filters += 1
    if _text(request.stat_type):
        query = query.eq("stat_type", _text(request.stat_type).upper())
        exact_filters += 1
    if request.line is not None:
        query = query.eq("line", float(request.line))
        exact_filters += 1
    if _text(request.direction):
        query = query.eq("direction", _text(request.direction).upper())
        exact_filters += 1
    if _text(request.event_start_min):
        query = query.gte("event_start_time", _text(request.event_start_min))
    if _text(request.event_start_max):
        query = query.lt("event_start_time", _text(request.event_start_max))

    # Prevent broad table reads.  A postmortem lookup must identify a thesis,
    # not scan the historical ledger.
    if exact_filters < 3:
        raise ValueError("PREDICTION_RECEIPT_LOOKUP_IDENTITY_INSUFFICIENT")

    return query.order("created_at", desc=True).limit(5)


def _receipt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "governed_prediction_id": row.get("prediction_id"),
        "governed_prediction_table": PREDICTION_TABLE,
        "is_immutable_pregame": _immutable_pregame(row),
        "prediction": row,
        "calibrated_probability": row.get("calibrated_probability"),
        "calibrated_lower_bound": row.get("calibrated_probability_lower_bound"),
        "calibrated_upper_bound": row.get("calibrated_probability_upper_bound"),
        "probability_publishable": bool(row.get("probability_publishable")),
        "terminal_label": row.get("probability_ceiling"),
        "can_execute": False,
    }


def lookup_prediction_receipts(db: Any, batch: PredictionReceiptLookupBatch) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    matched = ambiguous = not_found = blocked = 0

    for index, requested in enumerate(batch.rows):
        row_key = requested.row_key or f"row-{index + 1}"
        try:
            result = _query_for(db, requested).execute()
            rows = list(getattr(result, "data", None) or [])
        except ValueError as exc:
            blocked += 1
            outcomes.append(
                {
                    "row_key": row_key,
                    "status": "BLOCKED",
                    "code": str(exc),
                    "matches": [],
                    "can_execute": False,
                }
            )
            continue
        except Exception as exc:
            blocked += 1
            outcomes.append(
                {
                    "row_key": row_key,
                    "status": "BLOCKED",
                    "code": "PREDICTION_RECEIPT_LOOKUP_FAILED",
                    "error_type": type(exc).__name__,
                    "matches": [],
                    "can_execute": False,
                }
            )
            continue

        receipts = [_receipt(dict(row)) for row in rows]
        if not receipts:
            not_found += 1
            status = "NOT_FOUND"
            code = "IMMUTABLE_PREGAME_PREDICTION_NOT_FOUND"
        elif len(receipts) > 1:
            ambiguous += 1
            status = "AMBIGUOUS"
            code = "MULTIPLE_PREDICTION_RECEIPTS_MATCH"
        else:
            matched += 1
            status = "MATCHED"
            code = "IMMUTABLE_PREGAME_PREDICTION_MATCHED" if receipts[0]["is_immutable_pregame"] else "PREDICTION_MATCHED_NOT_PROVEN_IMMUTABLE_PREGAME"

        outcomes.append(
            {
                "row_key": row_key,
                "status": status,
                "code": code,
                "match_count": len(receipts),
                "matches": receipts,
                "can_execute": False,
            }
        )

    return {
        "request_id": batch.request_id,
        "rows_in": len(batch.rows),
        "rows_matched": matched,
        "rows_ambiguous": ambiguous,
        "rows_not_found": not_found,
        "rows_blocked": blocked,
        "reconciliation_pass": matched + ambiguous + not_found + blocked == len(batch.rows),
        "rows": outcomes,
        "can_execute": False,
    }


def install_prediction_receipt_lookup_route(
    app: Any,
    *,
    db_client_fn: Any,
    auth_dependency: Any,
) -> None:
    if any(getattr(route, "path", None) == "/v17/prediction-receipts/lookup" for route in app.router.routes):
        return

    @app.post("/v17/prediction-receipts/lookup", dependencies=[auth_dependency])
    def lookup_v17_prediction_receipts(batch: PredictionReceiptLookupBatch) -> dict[str, Any]:
        try:
            db = db_client_fn()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PREDICTION_LEDGER_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                    "can_execute": False,
                },
            ) from exc
        return lookup_prediction_receipts(db, batch)
