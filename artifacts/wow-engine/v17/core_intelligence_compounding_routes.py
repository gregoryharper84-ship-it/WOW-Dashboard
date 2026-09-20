"""Safe route mounting for WOW V17 compounding intelligence.

The mutation-capable full-cycle remains POST-only. The summary GET reads already
persisted intelligence snapshots and never invokes the compounding write cycle.
This keeps HTTP read semantics honest while preserving the existing governed
learning workflow.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI, Query

from v17.core_intelligence import AUTHORITY, CAN_EXECUTE
from v17.core_intelligence_compounding_runtime import run_full_intelligence_cycle

DEFAULT_SUMMARY_LIMIT = 100


class CompoundingSummaryBoundaryError(RuntimeError):
    def __init__(self, table: str, error: BaseException) -> None:
        super().__init__(f"{table}.read_summary: {type(error).__name__}")
        self.table = table
        self.error_type = type(error).__name__
        self.__cause__ = error


def _read_recent(db: Any, table: str, *, limit: int) -> list[dict[str, Any]]:
    try:
        rows = (
            db.table(table)
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute().data
            or []
        )
    except Exception as exc:
        raise CompoundingSummaryBoundaryError(table, exc) from exc
    return [dict(row) for row in rows]


def read_compounding_summary(
    db: Any,
    *,
    limit: int = DEFAULT_SUMMARY_LIMIT,
) -> dict[str, Any]:
    """Read persisted compounding intelligence without creating new evidence."""
    if limit < 1:
        raise ValueError("INVALID_SUMMARY_LIMIT")

    market_scorecards = _read_recent(
        db, "wow_intelligence_market_scorecards", limit=limit
    )
    signal_scorecards = _read_recent(
        db, "wow_intelligence_signal_scorecards", limit=limit
    )
    specialist_scorecards = _read_recent(
        db, "wow_intelligence_specialist_scorecards", limit=limit
    )
    challenger_proposals = _read_recent(
        db, "wow_intelligence_challenger_proposals", limit=limit
    )
    promotion_reviews = _read_recent(
        db, "wow_intelligence_promotion_reviews", limit=limit
    )

    return {
        "status": "PASS",
        "authority": AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "read_only": True,
        "window_limit": limit,
        "market_scorecard_n": len(market_scorecards),
        "signal_scorecard_n": len(signal_scorecards),
        "specialist_scorecard_n": len(specialist_scorecards),
        "challenger_proposal_n": len(challenger_proposals),
        "promotion_review_n": len(promotion_reviews),
        "promotion_eligible_n": sum(
            1
            for row in promotion_reviews
            if row.get("eligible_for_governed_review") is True
        ),
        "market_scorecards": market_scorecards,
        "signal_scorecards": signal_scorecards,
        "specialist_scorecards": specialist_scorecards,
        "challenger_proposals": challenger_proposals,
        "promotion_reviews": promotion_reviews,
    }


def install_compounding_intelligence_routes_read_only(
    app: FastAPI,
    *,
    get_client_fn: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> bool:
    if getattr(app.state, "v17_compounding_intelligence_routes_installed", False):
        return True

    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.post(
        "/v17/intelligence/full-cycle",
        operation_id="runWowV17FullIntelligenceCycle",
        dependencies=dependencies,
    )
    def full_cycle(
        max_outcomes: int = Query(default=1000, ge=1, le=5000),
        max_observations: int = Query(default=5000, ge=1, le=20000),
        min_samples: int = Query(default=30, ge=10, le=1000),
    ):
        return run_full_intelligence_cycle(
            get_client_fn(),
            max_outcomes=max_outcomes,
            max_observations=max_observations,
            min_samples=min_samples,
        )

    @app.get(
        "/v17/intelligence/compounding-summary",
        operation_id="getWowV17CompoundingIntelligenceSummary",
        dependencies=dependencies,
    )
    def compounding_summary(
        limit: int = Query(default=DEFAULT_SUMMARY_LIMIT, ge=1, le=1000),
    ):
        return read_compounding_summary(get_client_fn(), limit=limit)

    app.state.v17_compounding_intelligence_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "CompoundingSummaryBoundaryError",
    "read_compounding_summary",
    "install_compounding_intelligence_routes_read_only",
]
