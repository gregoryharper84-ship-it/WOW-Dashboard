"""Off-by-default one-shot startup runner for MLB Game Winner shadow research.

This is intentionally not a scoring route. When explicitly enabled by the
Render environment it starts a daemon research thread, reuses the existing WOW
Supabase client, emits aggregate evaluation evidence, and never changes serving
state or execution authority.
"""
from __future__ import annotations

import logging
import os
import threading
import time

_FLAG = "WOW_MLB_GAME_WINNER_SHADOW_EVAL_ON_START"
_DELAY_SECONDS = 8.0
_LOGGER = logging.getLogger("wow.v17.mlb_game_winner_shadow")
_started = False
_lock = threading.Lock()


def _run() -> None:
    time.sleep(_DELAY_SECONDS)
    try:
        import api_prod_market_acceptance as base
        from v17.mlb_game_winner_shadow_db_runner import log_shadow_evaluation

        db = base.market_api.prod.get_client()
        log_shadow_evaluation(db, _LOGGER)
    except Exception as exc:  # research failure must never take serving down
        _LOGGER.exception(
            "WOW_MLB_GAME_WINNER_SHADOW_EVALUATION status=FAILED error_type=%s "
            "serving_mode=SHADOW_ONLY automatic_promotion=false can_execute=false",
            type(exc).__name__,
        )


def schedule_if_enabled() -> bool:
    global _started
    if os.getenv(_FLAG, "0") != "1":
        return False
    with _lock:
        if _started:
            return False
        _started = True
        thread = threading.Thread(
            target=_run,
            name="wow-mlb-game-winner-shadow-eval",
            daemon=True,
        )
        thread.start()
        _LOGGER.warning(
            "WOW_MLB_GAME_WINNER_SHADOW_EVALUATION status=SCHEDULED "
            "serving_mode=SHADOW_ONLY automatic_promotion=false can_execute=false"
        )
        return True


__all__ = ["schedule_if_enabled"]
