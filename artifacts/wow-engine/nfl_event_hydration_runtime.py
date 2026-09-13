"""Idempotent NFL P1/P2 hydration for the governed WOW V17 research pipeline.

This module is deliberately non-publishing. It acquires immutable nflverse
historical assets, preserves exact source bytes in private Supabase Storage,
materializes deterministic training rows, and builds leakage-safe P2 feature
rows. It does not fit/certify a model or enable execution.
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable

from nfl_event_data_p1 import (
    DATASET_PBP,
    download_asset,
    manifest_record,
    schedules_asset,
    season_assets,
)
from nfl_event_features_p2 import build_prior_feature_rows
from nfl_event_training_p1 import build_game_team_summaries, build_training_games

LOGGER = logging.getLogger("wow.nfl.hydration")
RAW_BUCKET = "wow-nfl-raw"
DEFAULT_SEASONS = (2021, 2022, 2023, 2024, 2025, 2026)
BATCH_SIZE = 200
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def parse_seasons(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return DEFAULT_SEASONS
    values = sorted({int(part.strip()) for part in raw.split(",") if part.strip()})
    if not values or any(season < 2009 or season > 2100 for season in values):
        raise ValueError("WOW_NFL_HYDRATE_SEASONS contains an unsupported season")
    return tuple(values)


def _batch(rows: list[dict[str, Any]], size: int = BATCH_SIZE) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _upsert_batches(db: Any, table: str, rows: list[dict[str, Any]], conflict: str) -> int:
    written = 0
    for chunk in _batch(rows):
        result = db.table(table).upsert(chunk, on_conflict=conflict).execute()
        data = getattr(result, "data", None)
        written += len(data) if isinstance(data, list) else len(chunk)
    return written


def _read_csv(path: str | Path):
    handle = Path(path).open("r", encoding="utf-8-sig", errors="replace", newline="")
    return handle, csv.DictReader(handle)


def _gzip_exact_source(path: str | Path) -> Path:
    source = Path(path)
    target = source.with_suffix(source.suffix + ".gz")
    with source.open("rb") as src, target.open("wb") as raw_out:
        # mtime=0 keeps the archive deterministic; decompression reproduces the
        # exact source bytes whose SHA-256 is recorded in the source manifest.
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, mtime=0) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    return target


def _preserve_raw(db: Any, capture: Any) -> str:
    compressed = _gzip_exact_source(capture.local_path)
    season_key = str(capture.season) if capture.season is not None else "all"
    object_path = (
        f"{capture.dataset_name.lower()}/{season_key}/"
        f"{capture.content_sha256}.csv.gz"
    )
    try:
        with compressed.open("rb") as handle:
            db.storage.from_(RAW_BUCKET).upload(
                object_path,
                handle,
                {"content-type": "application/gzip", "upsert": "true"},
            )
    finally:
        compressed.unlink(missing_ok=True)
    return f"supabase://{RAW_BUCKET}/{object_path}"


def _snapshot_id(db: Any, capture: Any, raw_object_uri: str) -> str:
    existing = (
        db.table("wow_nfl_source_snapshots")
        .select("snapshot_id,raw_object_uri")
        .eq("dataset_name", capture.dataset_name)
        .eq("content_sha256", capture.content_sha256)
        .limit(1)
        .execute()
    )
    rows = existing.data if isinstance(existing.data, list) else []
    if rows:
        snapshot_id = str(rows[0]["snapshot_id"])
        if rows[0].get("raw_object_uri") != raw_object_uri:
            db.table("wow_nfl_source_snapshots").update(
                {"raw_object_uri": raw_object_uri}
            ).eq("snapshot_id", snapshot_id).execute()
        return snapshot_id

    payload = manifest_record(capture, object_uri=raw_object_uri)
    inserted = db.table("wow_nfl_source_snapshots").insert(payload).execute()
    data = inserted.data if isinstance(inserted.data, list) else []
    if not data or not data[0].get("snapshot_id"):
        raise RuntimeError("NFL_SOURCE_SNAPSHOT_INSERT_RETURNED_NO_ID")
    return str(data[0]["snapshot_id"])


def _capture_and_preserve(db: Any, asset: Any, workdir: Path) -> tuple[Any, str]:
    capture = download_asset(asset, workdir)
    raw_uri = _preserve_raw(db, capture)
    snapshot_id = _snapshot_id(db, capture, raw_uri)
    return capture, snapshot_id


def run_nfl_hydration(db: Any, *, seasons: Iterable[int]) -> dict[str, Any]:
    """Hydrate immutable P1 evidence and leakage-safe P2 rows.

    The operation is idempotent by content hash / primary-key upsert. Any source,
    storage, schema, or persistence error aborts the run instead of promoting a
    partial model capability.
    """
    normalized_seasons = tuple(sorted({int(season) for season in seasons}))
    if not normalized_seasons:
        raise ValueError("at least one NFL season is required")

    with tempfile.TemporaryDirectory(prefix="wow-nfl-hydrate-") as temp_dir:
        workdir = Path(temp_dir)
        schedule_capture, schedule_snapshot_id = _capture_and_preserve(
            db, schedules_asset(), workdir
        )

        schedule_handle, schedule_reader = _read_csv(schedule_capture.local_path)
        try:
            schedule_rows = [
                row for row in schedule_reader
                if str(row.get("season") or "").isdigit()
                and int(row["season"]) in normalized_seasons
            ]
        finally:
            schedule_handle.close()

        games = build_training_games(
            schedule_rows,
            schedule_snapshot_id=schedule_snapshot_id,
            schedule_content_sha256=schedule_capture.content_sha256,
        )
        _upsert_batches(db, "wow_nfl_training_games", games, "game_id")
        schedule_capture_path = Path(schedule_capture.local_path)
        schedule_capture_path.unlink(missing_ok=True)

        summaries: list[dict[str, Any]] = []
        source_counts = {"SCHEDULES": 1, "PLAY_BY_PLAY": 0, "WEEKLY_ROSTERS": 0, "INJURIES": 0}
        for season in normalized_seasons:
            season_games = [game for game in games if int(game["season"]) == season]
            for asset in season_assets(season):
                capture, snapshot_id = _capture_and_preserve(db, asset, workdir)
                source_counts[capture.dataset_name] = source_counts.get(capture.dataset_name, 0) + 1
                try:
                    if capture.dataset_name != DATASET_PBP:
                        continue
                    pbp_handle, pbp_reader = _read_csv(capture.local_path)
                    try:
                        built = build_game_team_summaries(
                            pbp_reader,
                            training_games=season_games,
                            pbp_snapshot_id=snapshot_id,
                            pbp_content_sha256=capture.content_sha256,
                        )
                    finally:
                        pbp_handle.close()
                    # A final-score row without PBP is not silently promoted into
                    # an all-zero team summary. Readiness remains fail-closed if
                    # the source is lagging or incomplete.
                    built = [row for row in built if int(row.get("offensive_plays") or 0) > 0]
                    _upsert_batches(
                        db,
                        "wow_nfl_game_team_summaries",
                        built,
                        "game_id,team",
                    )
                    summaries.extend(built)
                finally:
                    Path(capture.local_path).unlink(missing_ok=True)

        features = build_prior_feature_rows(games, summaries)
        _upsert_batches(db, "wow_nfl_pregame_feature_rows", features, "game_id")

        p1 = db.rpc("wow_nfl_event_p1_data_readiness").execute().data
        p2 = db.rpc("wow_nfl_event_p2_feature_readiness").execute().data
        return {
            "ok": True,
            "seasons": list(normalized_seasons),
            "source_counts": source_counts,
            "training_games_materialized": len(games),
            "team_game_summaries_materialized": len(summaries),
            "pregame_feature_rows_materialized": len(features),
            "p1": p1,
            "p2": p2,
            "probability_publishable": False,
            "can_execute": False,
        }


def install_nfl_hydration_startup(app: Any, *, db_client_fn: Any) -> None:
    """Install an explicit one-shot startup repair hook.

    Nothing runs unless WOW_NFL_HYDRATE_ON_STARTUP=1. The task runs after app
    startup so historical downloads cannot block the service health check.
    """
    if getattr(app.state, "wow_nfl_hydration_hook_installed", False):
        return
    app.state.wow_nfl_hydration_hook_installed = True

    @app.on_event("startup")
    async def _schedule_nfl_hydration() -> None:
        if os.getenv("WOW_NFL_HYDRATE_ON_STARTUP", "0") != "1":
            return
        seasons = parse_seasons(os.getenv("WOW_NFL_HYDRATE_SEASONS"))

        async def _run() -> None:
            LOGGER.warning(
                "WOW_NFL_HYDRATION status=STARTED seasons=%s probability_publishable=false can_execute=false",
                ",".join(str(season) for season in seasons),
            )
            try:
                result = await asyncio.to_thread(
                    run_nfl_hydration,
                    db_client_fn(),
                    seasons=seasons,
                )
                LOGGER.warning(
                    "WOW_NFL_HYDRATION status=COMPLETED training_games=%s summaries=%s features=%s p1_ready=%s p2_ready=%s probability_publishable=false can_execute=false",
                    result["training_games_materialized"],
                    result["team_game_summaries_materialized"],
                    result["pregame_feature_rows_materialized"],
                    bool((result.get("p1") or {}).get("historical_data_ready")),
                    bool((result.get("p2") or {}).get("feature_matrix_ready")),
                )
            except Exception as exc:
                LOGGER.exception(
                    "WOW_NFL_HYDRATION status=FAILED error_type=%s probability_publishable=false can_execute=false",
                    type(exc).__name__,
                )

        task = asyncio.create_task(_run())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
