"""Authenticated read-only WNBA historical hydration boundary.

Fetches player-game rows from the allowlisted official WNBA Stats client and
persists them into the raw bronze history table. It does not infer starter/role,
materialize model-ready training rows, train/register/certify models, publish
probabilities, or enable execution.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from wnba_history_ingestion import WNBAHistoryIngestionError, persist_raw_game_logs
from wnba_stats_client import WNBAStatsClient, WNBAStatsUnavailable


def install_wnba_history_routes(
    app: Any,
    *,
    auth_dependency: Any,
    db_client_fn: Callable[[], Any],
    stats_client_factory: Callable[[], WNBAStatsClient] = WNBAStatsClient,
) -> None:
    @app.post(
        "/internal/wnba/hydrate-history",
        dependencies=[auth_dependency],
        operation_id="hydrateWnbaPropHistory",
    )
    def hydrate_wnba_history(season: int, season_type: str = "Regular Season"):
        try:
            response = stats_client_factory().player_game_logs(
                season=season,
                season_type=season_type,
            )
            result = persist_raw_game_logs(
                db_client_fn(),
                response.rows,
                season=response.season,
                season_type=response.season_type,
                source_retrieved_at=response.retrieved_at,
            )
        except WNBAStatsUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": exc.code,
                    "runtime_model_status": "MODEL_UNAVAILABLE",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            ) from exc
        except WNBAHistoryIngestionError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": exc.code,
                    "runtime_model_status": "MODEL_UNAVAILABLE",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            ) from exc

        if result.rejected_n:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "WNBA_HISTORY_SOURCE_ROWS_REJECTED",
                    "fetched_n": result.fetched_n,
                    "accepted_n": result.accepted_n,
                    "persisted_n": 0,
                    "rejected_n": result.rejected_n,
                    "rejected_codes": list(result.rejected_codes),
                    "role_evidence_status": "UNRESOLVED",
                    "training_materialization_status": "BLOCKED_ROLE_EVIDENCE",
                    "model_training_status": "NOT_ATTEMPTED",
                    "runtime_model_status": "MODEL_UNAVAILABLE",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )

        return {
            "ok": True,
            "code": "WNBA_RAW_HISTORY_PERSISTED",
            "season": season,
            "season_type": season_type,
            "fetched_n": result.fetched_n,
            "accepted_n": result.accepted_n,
            "persisted_n": result.persisted_n,
            "rejected_n": 0,
            "source_identity": "WNBA_STATS_LEAGUE_GAME_LOG",
            "role_evidence_status": result.role_evidence_status,
            "training_materialization_status": result.training_materialization_status,
            "model_training_status": "NOT_ATTEMPTED",
            "artifact_registration_status": "NOT_ATTEMPTED",
            "artifact_certification_status": "NOT_ATTEMPTED",
            "runtime_model_status": result.runtime_model_status,
            "probability_publishable": False,
            "can_execute": False,
        }
