"""Bounded self-repair for stale current-season NFL team-game summaries.

The canonical NFL team/event scorer derives prior-game features from
``wow_nfl_game_team_summaries``.  Schedules can refresh more frequently than the
season play-by-play asset, leaving final scores visible while the PBP-derived
summaries remain stale.  This module repairs only that producer gap: capture the
current season PBP asset through the existing immutable provenance path,
rebuild deterministic team-game summaries from the already materialized
training-game ledger, and upsert those summaries by ``game_id,team``.

It does not fit or calibrate a model, alter feature math, publish probability,
change terminal governance, or authorize execution.
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock
import tempfile
from typing import Any

from nfl_event_data_p1 import DATASET_PBP, season_assets
from nfl_event_hydration_runtime import _capture_and_preserve, _read_csv, _upsert_batches
from nfl_event_training_p1 import build_game_team_summaries

CAN_EXECUTE = False

_LOCKS_GUARD = Lock()
_SEASON_LOCKS: dict[int, Lock] = {}


def _season_lock(season: int) -> Lock:
    with _LOCKS_GUARD:
        return _SEASON_LOCKS.setdefault(season, Lock())


def _load_season_training_games(db: Any, season: int) -> list[dict[str, Any]]:
    result = (
        db.table("wow_nfl_training_games")
        .select("*")
        .eq("season", season)
        .order("week")
        .order("gameday")
        .order("game_id")
        .execute()
    )
    rows = result.data if isinstance(result.data, list) else []
    games = [dict(row) for row in rows if isinstance(row, dict)]
    if not games:
        raise RuntimeError("NFL_CURRENT_SEASON_TRAINING_GAMES_UNAVAILABLE")
    return games


def _pbp_asset(season: int):
    for asset in season_assets(season):
        if asset.dataset_name == DATASET_PBP:
            return asset
    raise RuntimeError("NFL_CURRENT_SEASON_PBP_ASSET_UNAVAILABLE")


def refresh_current_season_summaries(db: Any, *, season: int) -> dict[str, Any]:
    """Refresh one season's PBP-derived summaries without changing model behavior.

    A per-process season lock prevents two Scout objectives for the same NFL
    event from starting duplicate provider downloads at the same time.  The
    persisted source bytes remain content-addressed and the summary upsert is
    idempotent, so a repeat after process restart is also safe.
    """
    season = int(season)
    if season < 2009 or season > 2100:
        raise ValueError("NFL current-season refresh outside supported range")

    with _season_lock(season):
        games = _load_season_training_games(db, season)
        with tempfile.TemporaryDirectory(prefix=f"wow-nfl-pbp-refresh-{season}-") as temp_dir:
            capture, snapshot_id = _capture_and_preserve(db, _pbp_asset(season), Path(temp_dir))
            handle, reader = _read_csv(capture.local_path)
            try:
                built = build_game_team_summaries(
                    reader,
                    training_games=games,
                    pbp_snapshot_id=snapshot_id,
                    pbp_content_sha256=capture.content_sha256,
                )
            finally:
                handle.close()

            # Preserve the existing fail-closed rule: a final-score game whose
            # PBP is not yet present must not become a fabricated all-zero row.
            summaries = [
                row for row in built
                if int(row.get("offensive_plays") or 0) > 0
            ]
            written = _upsert_batches(
                db,
                "wow_nfl_game_team_summaries",
                summaries,
                "game_id,team",
            )
            Path(capture.local_path).unlink(missing_ok=True)

    return {
        "status": "COMPLETED",
        "season": season,
        "pbp_snapshot_id": str(snapshot_id),
        "pbp_content_sha256": str(capture.content_sha256),
        "pbp_rows": int(capture.row_count),
        "team_game_summaries_materialized": len(summaries),
        "rows_upserted": int(written),
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "refresh_current_season_summaries",
]
