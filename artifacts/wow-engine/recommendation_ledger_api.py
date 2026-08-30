"""Write-before-display recommendation and linked settlement routes.

These routes provide cross-sport traceability. They never create model
probabilities, approve positions, or place orders. Generic recommendation
records remain separate from governed calibration ledgers.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

_NAMESPACE = uuid.UUID("3f750180-e938-4cb0-af6f-2a58e32facb5")
_ALLOWED_RESULTS = {"WIN", "LOSS", "PUSH", "VOID"}


def _aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be ISO 8601") from exc
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class RecommendationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: str
    sport: str
    league: Optional[str] = None
    event_id: str
    event_start_time: str
    participant: str
    opponent: Optional[str] = None
    market_family: str = "OUTRIGHT_WINNER"
    selection: str
    terminal_label: str
    probability_publishable: bool = False
    model_probability: Optional[float] = None
    calibrated_probability: Optional[float] = None
    calibrated_probability_lower_bound: Optional[float] = None
    governed_prediction_table: Optional[str] = None
    governed_prediction_id: Optional[str] = None
    source_snapshot_id: Optional[str] = None
    evidence_fingerprint: Optional[str] = None
    blockers: list[str] = Field(default_factory=list)
    display_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_row(self):
        if _aware(self.event_start_time) <= datetime.now(timezone.utc):
            raise ValueError("recommendations must be recorded before event start")
        for name in ("model_probability", "calibrated_probability", "calibrated_probability_lower_bound"):
            value = getattr(self, name)
            if value is not None and not 0 < value < 1:
                raise ValueError(f"{name} must satisfy 0<p<1")
        if self.probability_publishable and not self.governed_prediction_id:
            raise ValueError("publishable recommendation requires governed_prediction_id")
        for value in (self.governed_prediction_id, self.source_snapshot_id):
            if value:
                uuid.UUID(value)
        return self


class RecommendationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_run_id: str
    request_id: Optional[str] = None
    host_identity: str
    model_identity: str
    source_type: str
    source_conversation_ref: Optional[str] = None
    rows: list[RecommendationRow] = Field(min_length=1, max_length=50)


class SettlementRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_record_id: str
    settled_at: str
    settled_result: str
    official_result: Optional[str] = None
    settlement_source: str
    settlement_evidence_ref: Optional[str] = None
    position_reference: Optional[str] = None
    position_structure: Optional[str] = None
    underlying_market_count: Optional[int] = None
    entry_cost: Optional[float] = None
    payout: Optional[float] = None
    profit_loss: Optional[float] = None
    displayed_roi: Optional[float] = None

    @model_validator(mode="after")
    def validate_settlement(self):
        uuid.UUID(self.recommendation_record_id)
        _aware(self.settled_at)
        if self.settled_result not in _ALLOWED_RESULTS:
            raise ValueError("settled_result must be WIN, LOSS, PUSH, or VOID")
        if self.underlying_market_count is not None and self.underlying_market_count < 1:
            raise ValueError("underlying_market_count must be positive")
        if self.entry_cost is not None and self.entry_cost < 0:
            raise ValueError("entry_cost cannot be negative")
        if self.payout is not None and self.payout < 0:
            raise ValueError("payout cannot be negative")
        return self


class SettlementBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[SettlementRow] = Field(min_length=1, max_length=50)


def install_recommendation_ledger_routes(
    app: FastAPI,
    *,
    auth_dependency: Depends,
    get_client_fn: Callable[[], Any],
) -> None:
    @app.post("/record-recommendations", dependencies=[auth_dependency])
    def record_recommendations(batch: RecommendationBatch):
        recorded_at = datetime.now(timezone.utc)
        payloads = []
        for row in batch.rows:
            identity = f"{batch.research_run_id}|{row.row_key}|{row.event_id}|{row.selection}"
            record_id = uuid.uuid5(_NAMESPACE, identity)
            row_payload = row.model_dump()
            row_payload.update(
                {
                    "recommendation_record_id": str(record_id),
                    "recorded_at": recorded_at.isoformat(),
                    "idempotency_key": _fingerprint({"identity": identity}),
                    "research_run_id": batch.research_run_id,
                    "request_id": batch.request_id,
                    "host_identity": batch.host_identity,
                    "model_identity": batch.model_identity,
                    "source_type": batch.source_type,
                    "source_conversation_ref": batch.source_conversation_ref,
                    "capture_timing": "PREGAME",
                    "calibration_eligible": bool(
                        row.probability_publishable and row.governed_prediction_id
                    ),
                    "can_execute": False,
                }
            )
            payloads.append(row_payload)

        try:
            result = get_client_fn().table("wow_recommendation_records").insert(payloads).execute()
            persisted = result.data or []
        except Exception as exc:
            # A deterministic duplicate is a successful retry only when every
            # expected immutable row already exists.
            ids = [item["recommendation_record_id"] for item in payloads]
            try:
                existing = (
                    get_client_fn()
                    .table("wow_recommendation_records")
                    .select("recommendation_record_id,idempotency_key")
                    .in_("recommendation_record_id", ids)
                    .execute()
                ).data or []
            except Exception:
                existing = []
            if {item["recommendation_record_id"] for item in existing} != set(ids):
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "RECOMMENDATION_LEDGER_WRITE_FAILED",
                        "display_authorized": False,
                        "can_execute": False,
                    },
                ) from exc
            persisted = existing

        persisted_ids = [item["recommendation_record_id"] for item in persisted]
        if set(persisted_ids) != {item["recommendation_record_id"] for item in payloads}:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "RECOMMENDATION_LEDGER_WRITE_UNPROVEN",
                    "display_authorized": False,
                    "can_execute": False,
                },
            )
        return {
            "code": "RECOMMENDATION_LEDGER_WRITE_PASS",
            "rows_in": len(payloads),
            "rows_persisted": len(persisted_ids),
            "recommendation_record_ids": persisted_ids,
            "display_authorized": True,
            "recorded_at": recorded_at.isoformat(),
            "can_execute": False,
        }

    @app.post("/settle-recommendations", dependencies=[auth_dependency])
    def settle_recommendations(batch: SettlementBatch):
        client = get_client_fn()
        ids = [row.recommendation_record_id for row in batch.rows]
        existing = (
            client.table("wow_recommendation_records")
            .select("recommendation_record_id,capture_timing")
            .in_("recommendation_record_id", ids)
            .execute()
        ).data or []
        existing_by_id = {row["recommendation_record_id"]: row for row in existing}
        missing = sorted(set(ids) - set(existing_by_id))
        if missing:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "RECOMMENDATION_RECORD_NOT_FOUND",
                    "missing_recommendation_record_ids": missing,
                    "can_execute": False,
                },
            )

        payloads = []
        for row in batch.rows:
            payload = row.model_dump()
            payload["excluded_from_calibration"] = (
                existing_by_id[row.recommendation_record_id]["capture_timing"]
                != "PREGAME"
            )
            payload["attribution_status"] = (
                "MATCHED_PREGAME_RECORD"
                if not payload["excluded_from_calibration"]
                else "RETROSPECTIVE_UNVERIFIED"
            )
            payload["can_execute"] = False
            payloads.append(payload)
        try:
            result = client.table("wow_recommendation_outcomes").insert(payloads).execute()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "RECOMMENDATION_SETTLEMENT_WRITE_FAILED",
                    "can_execute": False,
                },
            ) from exc
        persisted = result.data or []
        return {
            "code": "RECOMMENDATION_SETTLEMENT_WRITE_PASS",
            "rows_in": len(payloads),
            "rows_persisted": len(persisted),
            "reconciliation_pass": len(persisted) == len(payloads),
            "can_execute": False,
        }
