"""Bounded credential-free NCAAF player-history release acquisition (research only).

An archived row is a postgame sporting outcome candidate, NEVER a historical
pregame prop offer. No fitted features, canonical player IDs, exact sportsbook
lines, probability, persistent DB write or betting execution is granted.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

CAN_EXECUTE = False
SCHEMA = "WOW_V17_NCAAF_SDV_PLAYER_HISTORY_RESEARCH_V1"
SOURCE_ID = "SPORTSDATAVERSE_NCAA_MFB_PLAYER_STATS"
MAX_BYTES = 150_000_000
MAX_ROWS = 1_000_000
RELEASE_HOSTS = frozenset((
    "github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
))
FIELDS = (
    "season", "contest_id", "espn_game_id", "team_id", "name", "category",
    "pass_yards", "completions", "pass_attempts", "yds_rush", "rush_attempts",
    "rec", "receiving_yards",
)
METRICS = {
    "passing": {"PASSING_YARDS": "pass_yards", "COMPLETIONS": "completions",
                "PASS_ATTEMPTS": "pass_attempts"},
    "rushing": {"RUSHING_YARDS": "yds_rush", "RUSH_ATTEMPTS": "rush_attempts"},
    "receiving": {"RECEPTIONS": "rec", "RECEIVING_YARDS": "receiving_yards"},
}
INTEGER = re.compile(r"-?\d+(?:\.0+)?\Z")


class NCAAFSDVSourceHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def release_url(season: int) -> str:
    if type(season) is not int or not 2010 <= season <= 2030:
        raise NCAAFSDVSourceHold("NCAAF_SDV_SEASON_INVALID")
    return (
        "https://github.com/sportsdataverse/sportsdataverse-data/releases/"
        f"download/ncaa_mfb_player_stats/ncaa_mfb_player_stats_{season}.parquet"
    )


def _stat(value: Any, *, allow_negative: bool = False) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise NCAAFSDVSourceHold("NCAAF_SDV_STAT_INVALID")
    raw = str(value).strip()
    if not INTEGER.fullmatch(raw):
        raise NCAAFSDVSourceHold("NCAAF_SDV_STAT_INVALID")
    parsed = int(float(raw))
    if parsed < 0 and not allow_negative:
        raise NCAAFSDVSourceHold("NCAAF_SDV_COUNT_NEGATIVE")
    return parsed


def profile_player_rows(rows: Iterable[Mapping[str, Any]], *, season: int,
                        source_sha256: str, retrieved_at: str) -> dict[str, Any]:
    """Validate schema and aggregate genuine player-game category rows.

    Upstream 'name' and 'team_id' are SOURCE ALIASES only. In particular,
    there is no verified unique player ID; never join by player name alone.
    """
    release_url(season)
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha256):
        raise NCAAFSDVSourceHold("NCAAF_SDV_HASH_INVALID")
    try:
        stamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise NCAAFSDVSourceHold("NCAAF_SDV_CAPTURE_TIMESTAMP_INVALID") from exc
    if stamp.utcoffset() is None:
        raise NCAAFSDVSourceHold("NCAAF_SDV_CAPTURE_TIMESTAMP_INVALID")
    counts: Counter[str] = Counter()
    holds: Counter[str] = Counter()
    rejected_stats: Counter[str] = Counter()
    missing_stats: Counter[str] = Counter()
    games: set[str] = set()
    input_rows = eligible_rows = partial_candidate_rows = 0
    for row in rows:
        input_rows += 1
        if input_rows > MAX_ROWS:
            raise NCAAFSDVSourceHold("NCAAF_SDV_ROW_LIMIT_EXCEEDED")
        if not isinstance(row, Mapping):
            holds["NCAAF_SDV_ROW_INVALID"] += 1
            continue
        if not set(FIELDS).issubset(row):
            raise NCAAFSDVSourceHold("NCAAF_SDV_SCHEMA_DRIFT")
        try:
            row_season = int(row["season"])
        except (TypeError, ValueError) as exc:
            raise NCAAFSDVSourceHold("NCAAF_SDV_ROW_SEASON_INVALID") from exc
        if row_season != season:
            holds["NCAAF_SDV_SEASON_MISMATCH"] += 1
            continue
        game = str(row["contest_id"] or "").strip()
        source_team = str(row["team_id"] or "").strip()
        source_name = str(row["name"] or "").strip()
        category = str(row["category"] or "").strip().lower()
        if not (game and source_team and source_name and category in METRICS):
            holds["NCAAF_SDV_SOURCE_IDENTITY_OR_CATEGORY_MISSING"] += 1
            continue
        values: dict[str, int] = {}
        rejected_in_row = 0
        for stat_type, key in METRICS[category].items():
            try:
                value = _stat(row[key], allow_negative=(stat_type == "RUSHING_YARDS"))
            except NCAAFSDVSourceHold as exc:
                # Never zero-fill or infer the bad value. A valid, independent
                # stat on the same source row must not be lost.
                rejected_stats[f"{stat_type}:{exc.code}"] += 1
                rejected_in_row += 1
                continue
            if value is None:
                missing_stats[stat_type] += 1
            else:
                values[stat_type] = value
        if not values:
            holds["NCAAF_SDV_ROW_STATS_EMPTY_OR_INVALID"] += 1
            continue
        if rejected_in_row:
            partial_candidate_rows += 1
        eligible_rows += 1
        games.add(game)
        counts.update(values.keys())
    if not eligible_rows:
        raise NCAAFSDVSourceHold("NCAAF_SDV_NO_USABLE_PLAYER_HISTORY")
    return {
        "schema": SCHEMA, "status": "SOURCE_OUTCOME_RESEARCH_ONLY",
        "source_id": SOURCE_ID, "source_url": release_url(season),
        "source_sha256": source_sha256, "source_license_claim": "CC-BY-4.0",
        "license_url": "https://github.com/sportsdataverse/sportsdataverse-data/blob/main/LICENSE",
        "source_retrieved_at": stamp.astimezone(timezone.utc).isoformat(),
        "season": season, "input_rows": input_rows, "candidate_rows": eligible_rows,
        "held_rows": input_rows - eligible_rows, "held_reasons": dict(sorted(holds.items())),
        "rejected_stat_cells": sum(rejected_stats.values()),
        "rejected_stat_reasons": dict(sorted(rejected_stats.items())),
        "missing_stat_cells": sum(missing_stats.values()),
        "missing_stat_counts": dict(sorted(missing_stats.items())),
        "partial_candidate_rows": partial_candidate_rows,
        "distinct_source_game_aliases": len(games),
        "stat_observation_counts": dict(sorted(counts.items())),
        "upstream_name_is_official_player_identity": False,
        "source_game_alias_is_official_event_identity": False,
        "first_publication_before_game_proven": False,
        "pregame_snapshot_verified": False,
        "historical_closing_line_verified": False,
        "candidate_source_license_requires_review": True,
        "official_result_verified": False, "fitted_training_authorized_by_probe": False,
        "source_rows_persisted_to_database": False, "model_consumption_receipts": [],
        "model_probability": None, "calibrated_lower_bound": None,
        "probability_publishable": False, "can_execute": False,
    }


def acquire_release(season: int, *, output_dir: str) -> dict[str, Any]:
    """Download a public season release once, hash it, and profile locally.

    A GitHub Actions artifact may retain the exact release bytes and manifest;
    this function never writes to Supabase or accesses any credential.
    """
    import requests

    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise NCAAFSDVSourceHold("NCAAF_SDV_PARQUET_READER_MISSING") from exc
    url = release_url(season)
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"ncaa_mfb_player_stats_{season}.parquet"
    try:
        with requests.get(url, timeout=(15, 90), stream=True, allow_redirects=True,
                          headers={"User-Agent": "WOW-V17-Open-Release-Research/1.0"}) as response:
            if response.status_code != 200:
                raise NCAAFSDVSourceHold(f"NCAAF_SDV_HTTP_{response.status_code}")
            final = urlsplit(response.url)
            if final.scheme != "https" or final.hostname not in RELEASE_HOSTS:
                raise NCAAFSDVSourceHold("NCAAF_SDV_UNAPPROVED_REDIRECT")
            total = 0
            digest = sha256()
            with target.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise NCAAFSDVSourceHold("NCAAF_SDV_ARCHIVE_TOO_LARGE")
                    digest.update(chunk)
                    stream.write(chunk)
        if not total:
            raise NCAAFSDVSourceHold("NCAAF_SDV_ARCHIVE_EMPTY")
        table = pq.read_table(str(target), columns=list(FIELDS))
        result = profile_player_rows(
            table.to_pylist(), season=season, source_sha256=digest.hexdigest(),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )
    except (requests.RequestException, OSError) as exc:
        target.unlink(missing_ok=True)
        raise NCAAFSDVSourceHold("NCAAF_SDV_ACQUISITION_FAILED") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise
    (folder / f"ncaa_mfb_player_stats_{season}.manifest.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NCAAF open-release history research; no model writes")
    parser.add_argument("--season", type=int, default=2024)
    parser.add_argument("--output-dir", default="ncaaf-sdv-research")
    args = parser.parse_args()
    receipt = acquire_release(args.season, output_dir=args.output_dir)
    print(json.dumps({
        "status": receipt["status"], "season": receipt["season"],
        "source_sha256": receipt["source_sha256"],
        "input_rows": receipt["input_rows"], "candidate_rows": receipt["candidate_rows"],
        "held_rows": receipt["held_rows"], "partial_candidate_rows": receipt["partial_candidate_rows"],
        "rejected_stat_cells": receipt["rejected_stat_cells"],
        "stat_observation_counts": receipt["stat_observation_counts"],
        "model_probability": None, "can_execute": False,
    }, sort_keys=True))
