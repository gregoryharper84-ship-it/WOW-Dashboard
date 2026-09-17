"""Autonomous candidate-only Fantasy Score forward scoring scheduler.

The loop converts already-frozen immutable pregame Fantasy Score snapshots into
raw candidate prediction rows so the future calibration/certification cohort can
mature.  It never fits a calibrator, certifies, promotes, publishes, ranks, or
executes a wager.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from v17.fantasy_score_forward_cohort_runtime import (
    FantasyScoreForwardCohortRequest,
    run_fantasy_score_forward_cohort,
)

CAN_EXECUTE = False


async def run_fantasy_score_forward_cohort_loop(
    *,
    db_client_fn: Callable[[], Any],
    market_api: Any,
    logger: logging.Logger,
    interval_seconds: int = 900,
    max_snapshots_per_lane: int = 100,
    initial_delay_seconds: int = 30,
) -> None:
    interval_seconds = max(300, int(interval_seconds))
    max_snapshots_per_lane = max(1, min(int(max_snapshots_per_lane), 200))
    initial_delay_seconds = max(0, int(initial_delay_seconds))

    if initial_delay_seconds:
        await asyncio.sleep(initial_delay_seconds)

    while True:
        try:
            result = await asyncio.to_thread(
                run_fantasy_score_forward_cohort,
                FantasyScoreForwardCohortRequest(
                    lanes=["MLB_PITCHER"],
                    max_snapshots_per_lane=max_snapshots_per_lane,
                ),
                db=db_client_fn(),
                market_api=market_api,
            )
            lane = next(
                (item for item in (result.get("lanes") or []) if item.get("lane") == "MLB_PITCHER"),
                {},
            )
            readiness = lane.get("calibration_readiness") or {}
            logger.warning(
                "WOW_FANTASY_SCORE_FORWARD_COHORT status=%s lane=MLB_PITCHER snapshots=%s captured=%s skipped=%s held=%s forward_n=%s settled_n=%s phase=%s probability_publishable=false rank_eligible=false can_execute=false",
                result.get("run_status"),
                lane.get("snapshots_considered"),
                lane.get("captured_forward_predictions"),
                lane.get("skipped_already_captured"),
                lane.get("held"),
                readiness.get("forward_prediction_n"),
                readiness.get("settled_prediction_n"),
                readiness.get("phase"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "WOW_FANTASY_SCORE_FORWARD_COHORT status=FAILED lane=MLB_PITCHER error_type=%s probability_publishable=false rank_eligible=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)


__all__ = ["CAN_EXECUTE", "run_fantasy_score_forward_cohort_loop"]
