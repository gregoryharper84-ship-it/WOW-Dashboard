"""Startup certification hook for the governed NFL fitted-model bundle."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from nfl_event_model_v17 import ensure_champion_model

LOGGER = logging.getLogger("wow.nfl.model")
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def install_nfl_model_startup(app: Any, *, db_client_fn: Any) -> None:
    if getattr(app.state, "wow_nfl_model_hook_installed", False):
        return
    app.state.wow_nfl_model_hook_installed = True

    @app.on_event("startup")
    async def _schedule_nfl_model_certification() -> None:
        if os.getenv("WOW_NFL_MODEL_CERTIFY_ON_STARTUP", "1") != "1":
            return

        async def _run() -> None:
            LOGGER.warning("WOW_NFL_MODEL_CERTIFICATION status=STARTED model_family=NFL_OUTRIGHT_WIN_LOGREG_V1 probability_publishable=false can_execute=false")
            try:
                result = await asyncio.to_thread(ensure_champion_model, db_client_fn())
                LOGGER.warning(
                    "WOW_NFL_MODEL_CERTIFICATION status=%s model_version=%s probability_publishable=false can_execute=false",
                    result.get("status"),
                    result.get("model_artifact_version"),
                )
                metrics = result.get("validation_metrics") or {}
                if metrics:
                    LOGGER.warning(
                        "WOW_NFL_MODEL_VALIDATION brier=%s baseline_brier=%s log_loss=%s baseline_log_loss=%s auc=%s ece=%s checks=%s probability_publishable=false can_execute=false",
                        metrics.get("validation_brier_score"),
                        metrics.get("baseline_brier_score"),
                        metrics.get("validation_log_loss"),
                        metrics.get("baseline_log_loss"),
                        metrics.get("validation_auc"),
                        metrics.get("validation_ece_10"),
                        metrics.get("certification_checks"),
                    )
            except Exception as exc:
                LOGGER.exception(
                    "WOW_NFL_MODEL_CERTIFICATION status=FAILED error_type=%s probability_publishable=false can_execute=false",
                    type(exc).__name__,
                )

        task = asyncio.create_task(_run())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
