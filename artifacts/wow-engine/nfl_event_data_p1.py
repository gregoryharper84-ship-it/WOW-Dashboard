"""WOW v16 NFL moneyline P1: immutable historical source acquisition.

This module downloads public nflverse/nfldata CSV assets, validates a minimal
schema contract, hashes the exact bytes, and emits an immutable manifest record.
It does not fit a model, publish a probability, or change runtime capability.

Historical sources are research/training evidence. Current-week publication
must still pass the normal WOW provenance/freshness/status gates; an nflverse
snapshot never substitutes for a required current official status source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import hashlib
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import httpx


SOURCE_FAMILY = "NFLVERSE_PUBLIC_DATA"
ALLOWED_INITIAL_HOSTS = frozenset({"github.com", "raw.githubusercontent.com"})
ALLOWED_REDIRECT_HOSTS = frozenset({
    "github.com",
    "raw.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
})

DATASET_SCHEDULES = "SCHEDULES"
DATASET_PBP = "PLAY_BY_PLAY"
DATASET_WEEKLY_ROSTERS = "WEEKLY_ROSTERS"
DATASET_INJURIES = "INJURIES"
DATASETS = frozenset({
    DATASET_SCHEDULES,
    DATASET_PBP,
    DATASET_WEEKLY_ROSTERS,
    DATASET_INJURIES,
})

# Minimal columns only. Downstream feature builders are responsible for stricter
# feature-specific contracts. Keeping the acquisition contract narrow protects
# against silently accepting a source that cannot even identify the event/team.
REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    DATASET_SCHEDULES: frozenset({
        "game_id", "season", "game_type", "week", "gameday",
        "away_team", "away_score", "home_team", "home_score",
    }),
    DATASET_PBP: frozenset({
        "play_id", "game_id", "home_team", "away_team", "season",
        "season_type", "week", "posteam", "defteam", "epa",
    }),
    DATASET_WEEKLY_ROSTERS: frozenset({
        "season", "week", "team", "position", "gsis_id",
    }),
    DATASET_INJURIES: frozenset({
        "season", "season_type", "team", "week", "gsis_id", "position",
        "report_status", "practice_status", "date_modified",
    }),
}


class SourceAcquisitionError(RuntimeError):
    """Base class for fail-closed NFL source acquisition failures."""


class SourceSchemaChanged(SourceAcquisitionError):
    """The source downloaded but no longer satisfies its typed contract."""


class SourceHostRejected(SourceAcquisitionError):
    """A source or redirect escaped the nflverse/GitHub allowlist."""


@dataclass(frozen=True)
class SourceAsset:
    dataset_name: str
    source_url: str
    filename: str
    season: int | None = None

    def __post_init__(self) -> None:
        if self.dataset_name not in DATASETS:
            raise ValueError(f"unsupported dataset: {self.dataset_name}")
        host = (urlparse(self.source_url).hostname or "").lower()
        if host not in ALLOWED_INITIAL_HOSTS:
            raise SourceHostRejected(f"initial source host rejected: {host}")
        if self.season is not None and not (1999 <= int(self.season) <= 2100):
            raise ValueError("season outside supported acquisition range")


@dataclass(frozen=True)
class CapturedAsset:
    dataset_name: str
    season: int | None
    requested_url: str
    resolved_url: str
    local_path: str
    content_sha256: str
    byte_count: int
    row_count: int
    column_names: tuple[str, ...]
    fetched_at: str
    etag: str | None
    last_modified: str | None
    source_status: str


def schedules_asset() -> SourceAsset:
    return SourceAsset(
        dataset_name=DATASET_SCHEDULES,
        source_url="https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
        filename="games.csv",
        season=None,
    )


def season_assets(season: int) -> tuple[SourceAsset, ...]:
    season = int(season)
    if not 2009 <= season <= 2100:
        # Injuries are only expected from 2009 onward; using one shared lower
        # bound prevents a partial P1 bundle from masquerading as complete.
        raise ValueError("P1 complete source bundle requires season >= 2009")
    base = "https://github.com/nflverse/nflverse-data/releases/download"
    return (
        SourceAsset(
            DATASET_PBP,
            f"{base}/pbp/play_by_play_{season}.csv",
            f"play_by_play_{season}.csv",
            season,
        ),
        SourceAsset(
            DATASET_WEEKLY_ROSTERS,
            f"{base}/weekly_rosters/roster_weekly_{season}.csv",
            f"roster_weekly_{season}.csv",
            season,
        ),
        SourceAsset(
            DATASET_INJURIES,
            f"{base}/injuries/injuries_{season}.csv",
            f"injuries_{season}.csv",
            season,
        ),
    )


def source_bundle(seasons: Iterable[int]) -> tuple[SourceAsset, ...]:
    normalized = sorted({int(season) for season in seasons})
    if not normalized:
        raise ValueError("at least one NFL season is required")
    assets: list[SourceAsset] = [schedules_asset()]
    for season in normalized:
        assets.extend(season_assets(season))
    return tuple(assets)


def _validate_resolved_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_REDIRECT_HOSTS:
        raise SourceHostRejected(f"resolved source host rejected: {host}")


def _scan_csv(path: Path, dataset_name: str) -> tuple[tuple[str, ...], int]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise SourceSchemaChanged(f"{dataset_name}: empty file") from exc
        columns = tuple(str(column).strip() for column in header)
        column_set = set(columns)
        missing = sorted(REQUIRED_COLUMNS[dataset_name] - column_set)
        if missing:
            raise SourceSchemaChanged(
                f"{dataset_name}: missing required columns: {','.join(missing)}"
            )
        row_count = sum(1 for _ in reader)
    return columns, row_count


def download_asset(
    asset: SourceAsset,
    destination_dir: str | Path,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 120.0,
    chunk_size: int = 1024 * 1024,
) -> CapturedAsset:
    """Download, hash, schema-check, and atomically retain one CSV asset.

    The returned file name is content-addressed. Re-running the same source
    therefore cannot silently overwrite bytes already used by a training run.
    """
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    temp_path = destination / f".{asset.filename}.part"
    if temp_path.exists():
        temp_path.unlink()

    owns_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=timeout_seconds)
    digest = hashlib.sha256()
    byte_count = 0
    response = None
    try:
        with http.stream("GET", asset.source_url) as response:
            response.raise_for_status()
            resolved_url = str(response.url)
            _validate_resolved_host(resolved_url)
            with temp_path.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    digest.update(chunk)
                    byte_count += len(chunk)
                    handle.write(chunk)
        if byte_count <= 0:
            raise SourceAcquisitionError(f"{asset.dataset_name}: zero-byte response")

        columns, row_count = _scan_csv(temp_path, asset.dataset_name)
        content_sha256 = digest.hexdigest()
        suffix = f"-{asset.season}" if asset.season is not None else ""
        final_path = destination / (
            f"{asset.dataset_name.lower()}{suffix}-{content_sha256[:16]}.csv"
        )
        if final_path.exists():
            # Content-addressed duplicate: preserve the original immutable file.
            temp_path.unlink()
        else:
            temp_path.replace(final_path)

        headers = response.headers if response is not None else {}
        return CapturedAsset(
            dataset_name=asset.dataset_name,
            season=asset.season,
            requested_url=asset.source_url,
            resolved_url=str(response.url),
            local_path=str(final_path),
            content_sha256=content_sha256,
            byte_count=byte_count,
            row_count=row_count,
            column_names=columns,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            etag=headers.get("etag"),
            last_modified=headers.get("last-modified"),
            source_status="CAPTURED" if row_count > 0 else "CAPTURED_EMPTY",
        )
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
    finally:
        if owns_client:
            http.close()


def manifest_record(capture: CapturedAsset, *, object_uri: str | None = None) -> dict:
    """Build the row written to the immutable Supabase source manifest."""
    return {
        "source_family": SOURCE_FAMILY,
        "dataset_name": capture.dataset_name,
        "season": capture.season,
        "source_url": capture.requested_url,
        "resolved_url": capture.resolved_url,
        "content_sha256": capture.content_sha256,
        "byte_count": capture.byte_count,
        "row_count": capture.row_count,
        "column_names": list(capture.column_names),
        "source_etag": capture.etag,
        "source_last_modified": capture.last_modified,
        "fetched_at": capture.fetched_at,
        "raw_object_uri": object_uri,
        "source_status": capture.source_status,
        "probability_publishable": False,
        "can_execute": False,
    }
