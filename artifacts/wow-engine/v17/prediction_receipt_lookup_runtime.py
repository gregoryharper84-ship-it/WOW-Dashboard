"""Read-only lookup for exact immutable V17 pregame prediction receipts.

This endpoint exists for postmortem attribution and exact-once recovery. It never
creates, edits, upgrades, or repairs a prediction and never derives probability
from settlement.

Fail-closed rules:
- prediction_id is preferred;
- any identity supplied with prediction_id must agree with the persisted row;
- without prediction_id, exact event/player/stat/line/direction is required;
- ambiguous matches are never guessed;
- only rows proving a persisted pregame lock are marked immutable;
- if the prediction ledger has no receipt, an exact request_id + row_key may be
  reconciled against the durable pick-request row ledger;
- durable PENDING state permits resume only with the same request_id + row_key;
- durable terminal/model-complete state is reported but never fabricated into a
  prediction receipt;
- can_execute is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

import pick_request_runtime_core as pick_runtime


PREDICTION_TABLE = "wow_predictions"
ROW_STATE_TABLE = "wow_pick_request_row_states"
_REQUIRED_FALLBACK_IDENTITY = ("event_id", "player", "stat_type", "line", "direction")
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
_STATE_SELECT_FIELDS = ",".join(
    [
        "run_id",
        "row_key",
        "event_id",
        "event_start_time",
        "sport",
        "player",
        "stat_type",
        "exact_line",
        "direction",
        "current_stage",
        "stage_seq",
        "terminal_status",
        "terminal_code",
        "failure_domain",
        "durable_status",
        "model_evaluated",
        "probability_publishable",
        "rank_eligible",
        "prediction_id",
        "can_execute",
        "updated_at",
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


def _canonical_text(field: str, value: Any, *, sport: Any = None) -> str:
    text = _text(value)
    if field == "stat_type":
        normalized = "_".join(text.upper().replace("-", " ").split())
        sport_key = "_".join(_text(sport).upper().replace("-", " ").split())
        if sport_key:
            return pick_runtime._canonical_stat(sport_key, normalized)
        return normalized
    if field in {"sport", "direction"}:
        return "_".join(text.upper().replace("-", " ").split())
    return text


def _immutable_pregame(row: dict[str, Any]) -> bool:
    locked = _aware(row.get("locked_at"))
    created = _aware(row.get("created_at"))
    model_ts = _aware(row.get("model_timestamp"))
    event_start = _aware(row.get("event_start_time"))
    if locked is None or created is None or model_ts is None or event_start is None:
        return False
    return created < event_start and model_ts < event_start and locked <= event_start


def _has_value(request: PredictionReceiptLookupRow, field: str) -> bool:
    value = getattr(request, field)
    if field == "line":
        return value is not None
    return bool(_text(value))


def _validate_fallback_identity(request: PredictionReceiptLookupRow) -> None:
    missing = [field for field in _REQUIRED_FALLBACK_IDENTITY if not _has_value(request, field)]
    if missing:
        raise ValueError(
            "PREDICTION_RECEIPT_LOOKUP_IDENTITY_INSUFFICIENT:"
            + ",".join(missing)
        )


def _apply_optional_filters(query: Any, request: PredictionReceiptLookupRow) -> Any:
    if _text(request.event_id):
        query = query.eq("event_id", _text(request.event_id))
    if _text(request.sport):
        query = query.eq("sport", _text(request.sport).upper())
    if _text(request.player):
        query = query.eq("player", _text(request.player))
    if _text(request.team):
        query = query.eq("team", _text(request.team))
    if _text(request.opponent):
        query = query.eq("opponent", _text(request.opponent))
    if _text(request.stat_type):
        query = query.eq("stat_type", _text(request.stat_type).upper())
    if request.line is not None:
        query = query.eq("line", float(request.line))
    if _text(request.direction):
        query = query.eq("direction", _text(request.direction).upper())
    if _text(request.event_start_min):
        query = query.gte("event_start_time", _text(request.event_start_min))
    if _text(request.event_start_max):
        query = query.lt("event_start_time", _text(request.event_start_max))
    return query


def _query_for(db: Any, request: PredictionReceiptLookupRow) -> Any:
    query = db.table(PREDICTION_TABLE).select(_SELECT_FIELDS)

    if _text(request.prediction_id):
        return query.eq("prediction_id", _text(request.prediction_id)).limit(2)

    _validate_fallback_identity(request)
    return _apply_optional_filters(query, request).order("created_at", desc=True).limit(5)


def _identity_conflicts(
    row: dict[str, Any], request: PredictionReceiptLookupRow
) -> list[str]:
    conflicts: list[str] = []
    for field in (
        "event_id",
        "sport",
        "player",
        "team",
        "opponent",
        "stat_type",
        "line",
        "direction",
    ):
        if not _has_value(request, field):
            continue
        requested = getattr(request, field)
        persisted = row.get(field)
        if field == "line":
            try:
                agrees = float(persisted) == float(requested)
            except (TypeError, ValueError):
                agrees = False
        else:
            agrees = _canonical_text(field, persisted, sport=row.get("sport")) == _canonical_text(field, requested, sport=request.sport or row.get("sport"))
        if not agrees:
            conflicts.append(field)

    event_start = _aware(row.get("event_start_time"))
    event_start_min = _aware(request.event_start_min)
    event_start_max = _aware(request.event_start_max)
    if request.event_start_min and (event_start is None or event_start_min is None or event_start < event_start_min):
        conflicts.append("event_start_min")
    if request.event_start_max and (event_start is None or event_start_max is None or event_start >= event_start_max):
        conflicts.append("event_start_max")
    return conflicts


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


def _run_id(request_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wow-pick-request:{request_id}"))


def _durable_identity_conflicts(
    state_row: dict[str, Any], request: PredictionReceiptLookupRow
) -> list[str]:
    conflicts: list[str] = []
    field_map = {
        "event_id": "event_id",
        "sport": "sport",
        "player": "player",
        "stat_type": "stat_type",
        "line": "exact_line",
        "direction": "direction",
    }
    for request_field, state_field in field_map.items():
        if not _has_value(request, request_field):
            continue
        requested = getattr(request, request_field)
        persisted = state_row.get(state_field)
        if request_field == "line":
            try:
                agrees = float(persisted) == float(requested)
            except (TypeError, ValueError):
                agrees = False
        else:
            agrees = _canonical_text(request_field, persisted, sport=state_row.get("sport")) == _canonical_text(request_field, requested, sport=request.sport or state_row.get("sport"))
        if not agrees:
            conflicts.append(request_field)

    event_start = _aware(state_row.get("event_start_time"))
    event_start_min = _aware(request.event_start_min)
    event_start_max = _aware(request.event_start_max)
    if request.event_start_min and (event_start is None or event_start_min is None or event_start < event_start_min):
        conflicts.append("event_start_min")
    if request.event_start_max and (event_start is None or event_start_max is None or event_start >= event_start_max):
        conflicts.append("event_start_max")
    return conflicts


def _durable_state_for(
    db: Any,
    *,
    request_id: str | None,
    row_key: str,
) -> list[dict[str, Any]]:
    if not _text(request_id) or not _text(row_key):
        return []
    result = (
        db.table(ROW_STATE_TABLE)
        .select(_STATE_SELECT_FIELDS)
        .eq("run_id", _run_id(_text(request_id)))
        .eq("row_key", _text(row_key))
        .limit(2)
        .execute()
    )
    return [dict(row) for row in (getattr(result, "data", None) or []) if isinstance(row, dict)]


def _durable_recovery_outcome(
    db: Any,
    *,
    request_id: str | None,
    requested: PredictionReceiptLookupRow,
    row_key: str,
) -> dict[str, Any] | None:
    if not _text(request_id) or not _text(row_key):
        return None
    try:
        rows = _durable_state_for(db, request_id=request_id, row_key=row_key)
    except Exception as exc:
        return {
            "row_key": row_key,
            "status": "BLOCKED",
            "code": "DURABLE_ROW_LOOKUP_FAILED",
            "error_type": type(exc).__name__,
            "matches": [],
            "can_execute": False,
        }
    if not rows:
        return None
    if len(rows) > 1:
        return {
            "row_key": row_key,
            "status": "BLOCKED",
            "code": "DURABLE_ROW_STATE_AMBIGUOUS",
            "match_count": len(rows),
            "matches": [],
            "can_execute": False,
        }

    state_row = rows[0]
    conflicts = _durable_identity_conflicts(state_row, requested)
    if conflicts:
        return {
            "row_key": row_key,
            "status": "BLOCKED",
            "code": "DURABLE_ROW_IDENTITY_CONFLICT",
            "detail": {"conflicting_fields": conflicts},
            "matches": [],
            "can_execute": False,
        }

    if _text(state_row.get("prediction_id")):
        return {
            "row_key": row_key,
            "status": "BLOCKED",
            "code": "PREDICTION_RECEIPT_INTEGRITY_MISMATCH",
            "detail": {
                "durable_prediction_id": _text(state_row.get("prediction_id")),
                "durable_status": state_row.get("durable_status"),
            },
            "matches": [],
            "retry_allowed": False,
            "durable_row_state": state_row,
            "can_execute": False,
        }

    terminal_status = _text(state_row.get("terminal_status")).upper() or "PENDING"
    model_evaluated = state_row.get("model_evaluated") is True
    resume = {
        "request_id": _text(request_id),
        "row_key": row_key,
        "contract": "REUSE_EXACT_BOARD_REQUEST_ID_AND_ROW_KEY",
        "can_execute": False,
    }

    if terminal_status == "PENDING":
        return {
            "row_key": row_key,
            "status": "UNRESOLVED",
            "code": "DURABLE_ROW_PENDING_SAFE_TO_RESUME",
            "match_count": 0,
            "matches": [],
            "retry_allowed": True,
            "resume": resume,
            "durable_row_state": state_row,
            "can_execute": False,
        }

    if model_evaluated:
        return {
            "row_key": row_key,
            "status": "DURABLE_MODEL_COMPLETE",
            "code": "DURABLE_MODEL_COMPLETE_NO_PREDICTION_RECEIPT",
            "match_count": 0,
            "matches": [],
            "retry_allowed": False,
            "durable_row_state": state_row,
            "can_execute": False,
        }

    return {
        "row_key": row_key,
        "status": "DURABLE_TERMINAL",
        "code": "DURABLE_TERMINAL_NO_PREDICTION_RECEIPT",
        "match_count": 0,
        "matches": [],
        "retry_allowed": False,
        "durable_row_state": state_row,
        "can_execute": False,
    }


def lookup_prediction_receipts(
    db: Any, batch: PredictionReceiptLookupBatch
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    matched = ambiguous = not_found = blocked = unresolved = durable_terminal = 0

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
                    "code": str(exc).split(":", 1)[0],
                    "detail": {"missing_identity_fields": str(exc).split(":", 1)[1].split(",")}
                    if ":" in str(exc)
                    else {},
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

        if _text(requested.prediction_id) and len(rows) == 1:
            conflicts = _identity_conflicts(dict(rows[0]), requested)
            if conflicts:
                blocked += 1
                outcomes.append(
                    {
                        "row_key": row_key,
                        "status": "BLOCKED",
                        "code": "PREDICTION_RECEIPT_IDENTITY_CONFLICT",
                        "detail": {
                            "prediction_id": _text(requested.prediction_id),
                            "conflicting_fields": conflicts,
                        },
                        "match_count": 0,
                        "matches": [],
                        "can_execute": False,
                    }
                )
                continue

        receipts = [_receipt(dict(row)) for row in rows]
        if not receipts:
            durable = _durable_recovery_outcome(
                db,
                request_id=batch.request_id,
                requested=requested,
                row_key=row_key,
            )
            if durable is not None:
                status = durable.get("status")
                if status == "UNRESOLVED":
                    unresolved += 1
                elif status in {"DURABLE_MODEL_COMPLETE", "DURABLE_TERMINAL"}:
                    durable_terminal += 1
                else:
                    blocked += 1
                outcomes.append(durable)
                continue
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
            code = (
                "IMMUTABLE_PREGAME_PREDICTION_MATCHED"
                if receipts[0]["is_immutable_pregame"]
                else "PREDICTION_MATCHED_NOT_PROVEN_IMMUTABLE_PREGAME"
            )

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

    accounted = matched + ambiguous + not_found + blocked + unresolved + durable_terminal
    return {
        "request_id": batch.request_id,
        "rows_in": len(batch.rows),
        "rows_matched": matched,
        "rows_ambiguous": ambiguous,
        "rows_not_found": not_found,
        "rows_blocked": blocked,
        "rows_unresolved": unresolved,
        "rows_durable_terminal": durable_terminal,
        "reconciliation_pass": accounted == len(batch.rows),
        "rows": outcomes,
        "can_execute": False,
    }


def install_prediction_receipt_lookup_route(
    app: Any,
    *,
    db_client_fn: Any,
    auth_dependency: Any,
) -> None:
    if any(
        getattr(route, "path", None) == "/v17/prediction-receipts/lookup"
        for route in app.router.routes
    ):
        return

    @app.post("/v17/prediction-receipts/lookup", dependencies=[auth_dependency])
    def lookup_v17_prediction_receipts(
        batch: PredictionReceiptLookupBatch,
    ) -> dict[str, Any]:
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
