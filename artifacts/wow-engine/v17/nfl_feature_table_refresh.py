"""Keep wow_nfl_pregame_feature_rows current for the active season.

Defect (2026-10-09): the training/replay feature table held only Week 1 of
2026. Full hydration rebuilds it only on an opt-in startup flag, and the
current-season summary refresh updates team summaries but never feature rows.
Live scoring was unaffected (it builds rows on the fly), but retraining,
replays and challenger evaluation silently lacked every later game.

This refresh runs after the daily forward-shadow settlement refresh:
1. refresh current-season PBP team summaries (existing, idempotent helper);
2. rebuild feature rows with the same builder full hydration uses
   (``build_prior_feature_rows``) over the full stored history;
3. upsert only the requested seasons' rows (idempotent on game_id).

Data plumbing only (Class B): no model, calibration or publication change.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

CAN_EXECUTE = False
FEATURE_TABLE = "wow_nfl_pregame_feature_rows"


def refresh_feature_rows(
    db: Any,
    *,
    seasons: Iterable[int],
    summary_refresh_fn: Callable[..., Any] | None = None,
    history_loader: Callable[[Any], tuple[list[dict[str, Any]], list[dict[str, Any]]]] | None = None,
    builder: Callable[..., list[dict[str, Any]]] | None = None,
    upsert: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    wanted = sorted({int(season) for season in seasons})
    if not wanted:
        raise ValueError("at least one NFL season is required")
    if summary_refresh_fn is None:
        from v17.nfl_current_season_summary_refresh import refresh_current_season_summaries as summary_refresh_fn
    if history_loader is None:
        from v17.nfl_team_event_specialist import _load_history as history_loader
    if builder is None:
        from nfl_event_features_p2 import build_prior_feature_rows as builder
    if upsert is None:
        from nfl_event_hydration_runtime import _upsert_batches as upsert

    summary_results = [summary_refresh_fn(db, season=season) for season in wanted]
    games, summaries = history_loader(db)
    rows = [row for row in builder(games, summaries) if int(row.get("season") or 0) in wanted]
    written = upsert(db, FEATURE_TABLE, rows, "game_id") if rows else 0
    labeled = sum(1 for row in rows if row.get("target_outcome") in ("HOME_WIN", "AWAY_WIN"))
    return {
        "status": "COMPLETED",
        "seasons": wanted,
        "summary_refresh": [
            {k: r.get(k) for k in ("season", "team_game_summaries_materialized", "rows_upserted")}
            for r in summary_results
        ],
        "feature_rows_built": len(rows),
        "feature_rows_labeled": labeled,
        "feature_rows_upserted": int(written or 0),
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "FEATURE_TABLE", "refresh_feature_rows"]
