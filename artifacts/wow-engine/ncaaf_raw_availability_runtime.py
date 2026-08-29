"""Authenticated runtime boundary for normalized raw NCAAF availability evidence.

Only raw official-conference PLAYER_AVAILABILITY_REPORT rows may enter here.
No derived role evidence, model scoring, probability publication, or execution is exposed.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from ncaaf_evidence_ingestion import NCAAFAcquisitionUnavailable, persist_normalized_evidence
from ncaaf_official_availability import NCAAvailabilityUnavailable, normalize_report_rows

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


def install_raw_availability_routes(app: Any, *, auth_dependency: Any, db_client_fn: Callable[[], Any]) -> None:
    @app.post(
        "/internal/ncaaf/ingest-availability-report",
        dependencies=[auth_dependency],
        operation_id="ingestNcaafAvailabilityReport",
    )
    def ingest_ncaaf_availability_report(payload: dict[str, Any]):
        required = (
            "conference", "official_event_id", "event_start_time", "report_timestamp",
            "report_phase", "team", "players",
        )
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "NCAAF_AVAILABILITY_REQUEST_INCOMPLETE",
                    "missing": missing,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        players = payload.get("players")
        if not isinstance(players, list) or not players:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "NCAAF_AVAILABILITY_PLAYERS_INVALID",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        try:
            rows = normalize_report_rows(
                conference=str(payload["conference"]),
                official_event_id=str(payload["official_event_id"]),
                event_start_time=str(payload["event_start_time"]),
                report_timestamp=str(payload["report_timestamp"]),
                report_phase=str(payload["report_phase"]),
                team=str(payload["team"]),
                players=players,
                source_record_id=str(payload["source_record_id"]) if payload.get("source_record_id") is not None else None,
                source_uri=str(payload["source_uri"]) if payload.get("source_uri") is not None else None,
            )
            persisted_n = persist_normalized_evidence(db_client_fn(), rows)
        except NCAAvailabilityUnavailable as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "probability_publishable": False, "can_execute": False},
            ) from exc
        except NCAAFAcquisitionUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": exc.code, "probability_publishable": False, "can_execute": False},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "NCAAF_AVAILABILITY_INGESTION_FAILED",
                    "error_type": type(exc).__name__,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            ) from exc

        return {
            "ok": True,
            "code": "NCAAF_RAW_AVAILABILITY_PERSISTED",
            "normalized_n": len(rows),
            "persisted_n": persisted_n,
            "evidence_kind": "PLAYER_AVAILABILITY_REPORT",
            "derived_role_evidence_status": "NOT_PRODUCED",
            "model_scoring_status": "NOT_ATTEMPTED",
            "probability_publishable": False,
            "can_execute": False,
        }
