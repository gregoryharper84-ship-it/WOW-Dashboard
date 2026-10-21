"""Issue #1933 — NFL read-only historical-source pilot (draft/backlog only).

Reuses the governed NFLVerse schedule/player-stat normalizer. The shared source
manifest currently keeps NFLVERSE at LICENSE_REVIEW_REQUIRED; this pilot is
RESEARCH_ONLY and cannot write, train, score, certify, or deploy.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from historical_data_backbone import EvidenceDomain, SourceRightsState, load_source_manifest
from nflverse_historical_adapter import (
    build_schedule_index,
    normalize_player_stats_row,
    require_allowed_dataset,
)
from v17.multi_source_historical_collector import CollectionBlocked, download_public_source

CAN_EXECUTE = False
MANIFEST = Path(__file__).resolve().parents[1] / "historical_source_manifest_v1.json"
PLAYER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)
SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ROUTES = (
    "PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS",
    "PASSING_TDS", "RUSHING_TDS", "RECEIVING_TDS",
)
REQUIRED_PLAYER = frozenset({
    "season", "week", "season_type", "game_id", "player_id", "team",
    "opponent_team", "passing_yards", "rushing_yards",
    "receiving_yards", "passing_tds", "rushing_tds", "receiving_tds",
})
REQUIRED_SCHEDULE = frozenset({
    "season", "week", "game_id", "game_type", "gameday", "gametime",
    "home_team", "away_team", "home_score", "away_score",
})


def _read_csv(payload: bytes, *, required: frozenset[str], label: str) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), strict=True)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)):
            raise CollectionBlocked("NFL_PILOT_DUPLICATE_CSV_COLUMNS", label)
        missing = sorted(required - set(columns))
        if missing:
            raise CollectionBlocked("NFL_PILOT_REQUIRED_COLUMNS_MISSING", f"{label}:{','.join(missing)}")
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise CollectionBlocked("NFL_PILOT_CSV_INVALID", label) from exc
    if not rows or any(None in row for row in rows):
        raise CollectionBlocked("NFL_PILOT_CSV_INVALID", label)
    return rows


def _rights(manifest_path: Path) -> tuple[str, str]:
    entries = [entry for entry in load_source_manifest(manifest_path)
               if entry.sport == "NFL" and entry.provider == "NFLVERSE"]
    if len(entries) != 1:
        raise CollectionBlocked("HISTORICAL_SOURCE_UNREGISTERED", "NFL/NFLVERSE")
    entry = entries[0]
    if entry.evidence_domain is not EvidenceDomain.SPORTING:
        raise CollectionBlocked("MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL", "NFLVERSE")
    # The actual manifest is currently pending license review. Not a reason to
    # falsely upgrade this source's rights or production training readiness.
    if entry.rights_state not in {
        SourceRightsState.LICENSE_REVIEW_REQUIRED, SourceRightsState.V17_APPROVED,
    }:
        raise CollectionBlocked("NFL_PILOT_SOURCE_NOT_ELIGIBLE_FOR_RESEARCH", entry.rights_state.value)
    player = require_allowed_dataset("player_stats")
    schedule = require_allowed_dataset("schedules")
    if player.license != "CC-BY-4.0" or schedule.license != "CC-BY-4.0":
        raise CollectionBlocked("NFL_PILOT_DATASET_LICENSE_REVIEW_REQUIRED")
    return entry.rights_state.value, player.license


def _identity_matches(schedule: Mapping[str, Any], espn: Mapping[str, Any]) -> bool:
    from nfl_prop_auto_hydration import _nflverse_team_code
    try:
        if str(espn.get("status") or "").upper() not in {"FINAL", "CLOSED"}:
            return False
        if int(espn["season"]) != schedule.season or int(espn["week"]) != schedule.week:
            return False
        home = _nflverse_team_code(espn["home_team"])
        away = _nflverse_team_code(espn["away_team"])
        if home != schedule.home_team or away != schedule.away_team:
            return False
        candidate_time = datetime.fromisoformat(str(espn["event_start_time"]).replace("Z", "+00:00"))
        if candidate_time.tzinfo is None:
            return False
        return abs((candidate_time - schedule.event_start_time).total_seconds()) <= 90 * 60
    except (TypeError, ValueError, KeyError):
        return False


def analyze_nfl_pilot(
    *,
    season: int,
    schedule_csv: bytes,
    player_csv: bytes,
    as_of: datetime | None = None,
    espn_final_events: Sequence[Mapping[str, Any]] | None = None,
    manifest_path: Path = MANIFEST,
) -> dict[str, Any]:
    """Analyze immutable bytes in memory; no storage or operational side effects."""
    if type(season) is not int or season < 1999 or season > 2099:
        raise CollectionBlocked("NFL_PILOT_SEASON_INVALID")
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise CollectionBlocked("NFL_PILOT_AS_OF_TIMEZONE_REQUIRED")
    rights_state, license_id = _rights(manifest_path)
    schedules = _read_csv(schedule_csv, required=REQUIRED_SCHEDULE, label="schedules")
    players = _read_csv(player_csv, required=REQUIRED_PLAYER, label="player_stats")
    finalized = []
    raw_season_schedules = 0
    for row in schedules:
        if str(row["season"]).strip() != str(season) or row["game_type"] != "REG":
            continue
        raw_season_schedules += 1
        if not str(row["home_score"] or "").strip() or not str(row["away_score"] or "").strip():
            continue
        # A finalized-looking score before kickoff may be a bad snapshot.
        finalized.append(row)
    schedule_index = build_schedule_index(finalized)
    schedule_index = {
        game_id: game for game_id, game in schedule_index.items()
        if game.event_start_time < now
    }
    if not schedule_index:
        raise CollectionBlocked("NFL_PILOT_NO_FINISHED_GAMES", str(season))
    normalized = 0
    player_rows_in_season = 0
    held_unsettled_or_unjoined = 0
    quarantine_missing_player_id = 0
    quarantine_missing_game_id = 0
    seen: set[tuple[str, str]] = set()
    by_stat: dict[str, int] = {stat: 0 for stat in ROUTES}
    for row in players:
        if str(row["season"]).strip() != str(season) or row["season_type"].upper() != "REG":
            continue
        player_rows_in_season += 1
        player_id, game_id = str(row["player_id"] or "").strip(), str(row["game_id"] or "").strip()
        # Preserve corpus-level diagnostics rather than losing the entire source
        # snapshot to one unidentifiable row. None of these rows enter outcomes.
        # Training remains held whenever any source row is quarantined.
        if not player_id or not game_id:
            if not player_id:
                quarantine_missing_player_id += 1
            if not game_id:
                quarantine_missing_game_id += 1
            continue
        key = (game_id, player_id)
        if key in seen:
            raise CollectionBlocked("NFL_PILOT_DUPLICATE_PLAYER_GAME", f"{game_id}:{player_id}")
        seen.add(key)
        if game_id not in schedule_index:
            held_unsettled_or_unjoined += 1
            continue
        observations = normalize_player_stats_row(
            row,
            schedule_index=schedule_index,
            source_retrieved_at=now,
            source_payload_hash=hashlib.sha256(player_csv).hexdigest(),
            stat_types=ROUTES,
        )
        normalized += 1
        for observation in observations:
            by_stat[observation.stat_type] += 1

    if normalized == 0:
        raise CollectionBlocked("NFL_PILOT_NO_JOINED_PLAYER_ROWS")
    comparisons: dict[str, Any] = {"status": "NOT_CHECKED", "matched": 0, "unresolved": 0}
    if espn_final_events is not None:
        if not espn_final_events:
            raise CollectionBlocked("NFL_PILOT_INDEPENDENT_SOURCE_EMPTY")
        matches, unresolved = 0, 0
        for game in schedule_index.values():
            found = [event for event in espn_final_events if _identity_matches(game, event)]
            if len(found) > 1:
                raise CollectionBlocked("NFL_PILOT_INDEPENDENT_EVENT_AMBIGUOUS", game.game_id)
            if found:
                matches += 1
            else:
                unresolved += 1
        comparisons = {
            "status": "MATCHED" if unresolved == 0 else "PARTIAL_HOLD",
            "matched": matches, "unresolved": unresolved,
        }

    return {
        "pilot": "NFL_HISTORICAL_RESEARCH_V1",
        "season": season,
        "status": "RESEARCH_ONLY",
        "source_rights_state": rights_state,
        "license_id": license_id,
        "attribution_required": True,
        "attribution": "Data from nflverse, CC-BY-4.0; raw snapshot hashes retained.",
        "source_retrieved_at": now.astimezone(timezone.utc).isoformat(),
        "sources": {
            "nflverse_stats_player": {
                "url": PLAYER_URL.format(season=season),
                "sha256": hashlib.sha256(player_csv).hexdigest(),
                "rows": len(players),
            },
            "nflverse_schedule": {
                "url": SCHEDULE_URL,
                "sha256": hashlib.sha256(schedule_csv).hexdigest(),
                "rows": len(schedules),
            },
        },
        "counts": {
            "schedule_games_for_season": raw_season_schedules,
            "finalized_games_in_scope": len(schedule_index),
            "player_rows_in_season": player_rows_in_season,
            "joined_player_rows": normalized,
            "held_unsettled_or_unjoined": held_unsettled_or_unjoined,
            "quarantine_missing_player_id": quarantine_missing_player_id,
            "quarantine_missing_game_id": quarantine_missing_game_id,
            "quarantined_identity_rows": quarantine_missing_player_id + quarantine_missing_game_id
                - sum(1 for row in players if str(row.get("season", "")).strip() == str(season)
                      and str(row.get("season_type", "")).upper() == "REG"
                      and not str(row.get("player_id") or "").strip()
                      and not str(row.get("game_id") or "").strip()),
            "normalized_stat_observations": by_stat,
        },
        "independent_espn_identity": comparisons,
        "ready_for_offline_training": False,
        "production_certified": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def fetch_nfl_pilot(
    season: int,
    *,
    download=download_public_source,
    espn_final_events: Sequence[Mapping[str, Any]] | None = None,
    manifest_path: Path = MANIFEST,
) -> dict[str, Any]:
    """Manual-only fetch of two allowlisted NFL historical research sources."""
    _rights(manifest_path)
    if type(season) is not int or not 1999 <= season <= 2099:
        raise CollectionBlocked("NFL_PILOT_SEASON_INVALID")
    schedules = download(SCHEDULE_URL)
    players = download(PLAYER_URL.format(season=season))
    return analyze_nfl_pilot(
        season=season, schedule_csv=schedules, player_csv=players,
        espn_final_events=espn_final_events, manifest_path=manifest_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="NFL research-only historical pilot; no DB or scoring")
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--schedule-csv", type=Path)
    parser.add_argument("--player-csv", type=Path)
    args = parser.parse_args()
    if bool(args.schedule_csv) != bool(args.player_csv):
        parser.error("provide both --schedule-csv and --player-csv, or neither")
    if args.schedule_csv:
        result = analyze_nfl_pilot(
            season=args.season,
            schedule_csv=args.schedule_csv.read_bytes(),
            player_csv=args.player_csv.read_bytes(),
        )
    else:
        result = fetch_nfl_pilot(args.season)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
