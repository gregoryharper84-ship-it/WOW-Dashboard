"""Authenticated internal routes for the WOW Continuous Engineering Auditor."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from v17.engineering_auditor import AuditEvent, EngineeringAuditStore
from v17.engineering_auditor_auth import engineering_auditor_auth


class EngineeringAuditEventRequest(BaseModel):
    event_name: str = Field(min_length=1, max_length=80)
    action: str = Field(default="", max_length=80)
    repository: str = Field(min_length=1, max_length=250)
    source_kind: str = Field(min_length=1, max_length=40)
    source_ref: str = Field(min_length=1, max_length=250)
    title: str = Field(default="", max_length=500)
    state: str = Field(default="OPEN", max_length=80)
    labels: list[str] = Field(default_factory=list, max_length=100)
    draft: bool = False
    actor: str | None = Field(default=None, max_length=250)
    updated_at: str | None = None
    head_sha: str | None = Field(default=None, max_length=80)
    conclusion: str | None = Field(default=None, max_length=80)
    details: dict[str, Any] = Field(default_factory=dict)


def install_engineering_auditor_routes(app: FastAPI, *, db_client_fn: Callable[[], Any]) -> None:
    auth = Depends(engineering_auditor_auth)

    @app.post(
        "/internal/v17/engineering-auditor/event",
        dependencies=[auth],
        operation_id="recordWowV17EngineeringAuditorEvent",
    )
    def record_engineering_auditor_event(req: EngineeringAuditEventRequest):
        try:
            event = AuditEvent.from_mapping(req.model_dump())
            receipt = EngineeringAuditStore(db_client_fn()).ingest_event(event)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "ENGINEERING_AUDITOR_EVENT_INVALID", "message": str(exc), "can_execute": False},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ENGINEERING_AUDITOR_PERSISTENCE_FAILURE",
                    "error_type": type(exc).__name__,
                    "can_execute": False,
                },
            ) from exc
        return {
            "ok": True,
            "audit_receipt": receipt,
            "probability_behavior_change": False,
            "can_execute": False,
        }

    @app.get(
        "/internal/v17/engineering-auditor/health",
        dependencies=[auth],
        operation_id="getWowV17EngineeringAuditorHealth",
    )
    def get_engineering_auditor_health():
        try:
            return EngineeringAuditStore(db_client_fn()).health()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ENGINEERING_AUDITOR_HEALTH_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                    "can_execute": False,
                },
            ) from exc
