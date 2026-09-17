"""Refresh settled NFL outcomes immediately before prospective shadow grading.

The forward-grade consumer joins immutable pregame predictions to
``wow_nfl_training_games``.  Historically that table was refreshed only by the
full hydration path, so a healthy grading workflow could repeatedly report zero
grades after new games settled.  This module performs the narrow producer step
needed by grading: acquire the authoritative nflverse schedules snapshot,
preserve the exact source bytes through the existing hydration provenance path,
normalize completed games, and upsert only the requested seasons.

It does not build features, fit a model, publish a probability, certify a model,
or authorize execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any, Iterable

from nfl_event_data_p1 import schedules_asset
from nfl_event_hydration_runtime import _capture_and_preserve, _read_csv, _upsert_batches
from nfl_event_training_p1 import build_training_games

CAN_EXECUTE = False


def default_refresh_seasons(now: datetime | None = None) -> tuple[int, ...]:
    """Cover the current calendar year plus the prior NFL season year.

    The two-year window handles January/February postseason grading without
    special-casing the NFL season boundary.  The schedules asset is acquired
    once, so this does not multiply provider requests.
    """
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        current = current.replace(tzinfo=timezone.utc)
    year = current.astimezone(timezone.utc).year
    return (year - 1, year)


def refresh_recent_settled_outcomes(
    db: Any,
    *,
    seasons: Iterable[int] | None = None,
) -> dict[str, Any]:
    normalized_seasons = tuple(sorted({int(value) for value in (seasons or default_refresh_seasons())}))
    if not normalized_seasons:
        raise ValueError("at least one NFL settlement season is required")
    if any(season < 2009 or season > 2100 for season in normalized_seasons):
        raise ValueError("NFL settlement season outside supported range")

    with tempfile.TemporaryDirectory(prefix="wow-nfl-settlement-refresh-") as temp_dir:
        capture, snapshot_id = _capture_and_preserve(db, schedules_asset(), Path(temp_dir))
        handle, reader = _read_csv(capture.local_path)
        try:
            schedule_rows = [
                row
                for row in reader
                if str(row.get("season") or "").isdigit()
                and int(row["season"]) in normalized_seasons
            ]
        finally:
            handle.close()

        completed = build_training_games(
            schedule_rows,
            schedule_snapshot_id=snapshot_id,
            schedule_content_sha256=capture.content_sha256,
        )
        written = _upsert_batches(db, "wow_nfl_training_games", completed, "game_id")
        Path(capture.local_path).unlink(missing_ok=True)

    latest_gameday = max((str(row.get("gameday") or "") for row in completed), default=None)
    return {
        "status": "COMPLETED",
        "seasons": list(normalized_seasons),
        "schedule_snapshot_id": snapshot_id,
        "schedule_content_sha256": capture.content_sha256,
        "schedule_rows_considered": len(schedule_rows),
        "completed_games_materialized": len(completed),
        "rows_upserted": int(written),
        "latest_gameday": latest_gameday,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "default_refresh_seasons",
    "refresh_recent_settled_outcomes",
]
