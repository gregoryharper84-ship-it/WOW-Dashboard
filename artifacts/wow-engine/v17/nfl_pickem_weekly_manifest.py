"""Immutable candidate NFL weekly manifest from preserved NFLVerse schedule bytes.

This is a *read-only* Class B preparation step for #1530. It verifies the
captured source checksum, event identities, pre-kickoff freeze and cardinality.
NFLVerse is not an independently authenticated official final-result provider.
No DB write, source certification, or report publication occurs here.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from nflverse_historical_adapter import (
    NFLVerseHistoricalAdapterError,
    parse_nflverse_kickoff,
)
from v17.nfl_pickem_weekly_evidence import WeeklyEvidenceError, _time

CAN_EXECUTE = False
MANIFEST_VERSION = "NFL_PICKEM_SOURCE_FROZEN_CANDIDATE_V1"


def _nonempty(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeeklyEvidenceError(code)
    return value.strip()


def freeze_nflverse_week_candidate(
    *,
    snapshot: Mapping[str, Any],
    compressed_raw_csv: bytes,
    season: int,
    week: int,
    expected_game_count: int,
    manifest_frozen_at: str,
) -> dict[str, Any]:
    """Reject any manifest that cannot be proven from captured pregame bytes."""
    if type(season) is not int or not 2020 <= season <= 2100 or (
        type(week) is not int or not 1 <= week <= 22
    ):
        raise WeeklyEvidenceError("PICKEM_FROZEN_WEEK_INVALID")
    if type(expected_game_count) is not int or not 1 <= expected_game_count <= 20:
        raise WeeklyEvidenceError("PICKEM_FROZEN_WEEK_COUNT_INVALID")
    if not isinstance(snapshot, Mapping):
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_SNAPSHOT_MISSING")
    if snapshot.get("dataset_name") != "SCHEDULES" or (
        snapshot.get("source_family") != "NFLVERSE_PUBLIC_DATA" or
        snapshot.get("source_status") != "CAPTURED"
    ):
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_IDENTITY_NOT_ALLOWED")
    source_id = _nonempty(
        snapshot.get("snapshot_id"), "PICKEM_FROZEN_SOURCE_ID_MISSING"
    )
    declared_sha = _nonempty(
        snapshot.get("content_sha256"), "PICKEM_FROZEN_SOURCE_HASH_MISSING"
    )
    if len(declared_sha) != 64 or any(c not in "0123456789abcdef" for c in declared_sha):
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_HASH_INVALID")
    freeze = _time(manifest_frozen_at, "PICKEM_FROZEN_AT_INVALID")
    source_time = _time(snapshot.get("fetched_at"), "PICKEM_FROZEN_SOURCE_FETCHED_AT_INVALID")
    if source_time > freeze:
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_CAPTURED_AFTER_FREEZE")
    if not isinstance(compressed_raw_csv, bytes) or len(compressed_raw_csv) > 12_000_000:
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_BYTES_INVALID")
    # Bound gzip expansion *during* decompression. A post-hoc size check
    # permits a hostile small compressed object to exhaust the 512-MiB worker.
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed_raw_csv)) as stream:
            raw = stream.read(50_000_001)
    except (OSError, EOFError) as exc:
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_DECOMPRESSION_FAILED") from exc
    if len(raw) > 50_000_000:
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_DECOMPRESSED_TOO_LARGE")
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != declared_sha:
        raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_CHECKSUM_MISMATCH")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="strict")))
        if not reader.fieldnames or not {
            "game_id", "season", "week", "game_type", "gameday", "gametime",
            "home_team", "away_team",
        }.issubset(reader.fieldnames):
            raise WeeklyEvidenceError("PICKEM_FROZEN_SCHEDULE_COLUMNS_MISSING")
        rows = [dict(row) for row in reader]
    except UnicodeError as exc:
        raise WeeklyEvidenceError("PICKEM_FROZEN_SCHEDULE_ENCODING_INVALID") from exc
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        try:
            candidate_season = int(row.get("season", ""))
            candidate_week = int(row.get("week", ""))
        except (TypeError, ValueError):
            continue  # Other-year aggregate rows are irrelevant until selected.
        if candidate_season != season or candidate_week != week:
            continue
        game_type = str(row.get("game_type") or "").upper().strip()
        if game_type not in {"REG", "REGULAR", "REGULAR_SEASON"}:
            continue
        event = _nonempty(row.get("game_id"), "PICKEM_FROZEN_EVENT_ID_MISSING")
        home = _nonempty(row.get("home_team"), "PICKEM_FROZEN_HOME_TEAM_MISSING").upper()
        away = _nonempty(row.get("away_team"), "PICKEM_FROZEN_AWAY_TEAM_MISSING").upper()
        if home == away:
            raise WeeklyEvidenceError("PICKEM_FROZEN_HOME_AWAY_COLLISION")
        if event in seen:
            raise WeeklyEvidenceError("PICKEM_FROZEN_DUPLICATE_EVENT")
        seen.add(event)
        try:
            kickoff = parse_nflverse_kickoff(row)
        except NFLVerseHistoricalAdapterError as exc:
            raise WeeklyEvidenceError("PICKEM_FROZEN_KICKOFF_INVALID") from exc
        if freeze >= kickoff:
            raise WeeklyEvidenceError("PICKEM_FROZEN_AFTER_EARLIEST_KICKOFF")
        # A valid pregame freeze may never be synthesized from a dataset already
        # carrying finals. Zero scores are real, not empty.
        if str(row.get("home_score") or "").strip() or str(row.get("away_score") or "").strip():
            raise WeeklyEvidenceError("PICKEM_FROZEN_SOURCE_CONTAINS_GAME_SCORE")
        events.append({
            "official_event_id": event,
            "home_team": home,
            "away_team": away,
            "season": season,
            "week": week,
            "game_type": "REG",
            "schedule_status": "SCHEDULED",
            "event_start_time_utc": kickoff.astimezone(timezone.utc).isoformat(),
        })

    if len(events) != expected_game_count:
        raise WeeklyEvidenceError("PICKEM_FROZEN_FULL_WEEK_COUNT_UNVERIFIED")
    events.sort(key=lambda x: (x["event_start_time_utc"], x["official_event_id"]))
    manifest = {
        "season": season,
        "week": week,
        "expected_game_count": expected_game_count,
        "manifest_status": "FROZEN",
        "manifest_frozen_at": freeze.isoformat(),
        "schedule_source_receipt_id": f"nflverse-source-{source_id}-{declared_sha}",
        "schedule_snapshot_id": source_id,
        "source_system": "NFLVERSE_PUBLIC_DATA",
        "source_content_sha256": declared_sha,
        "events": events,
        "source_authenticity_verified": False,
        "publication_allowed": False,
        "can_execute": False,
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["manifest_receipt_id"] = (
        f"nfl-pickem-source-manifest-{hashlib.sha256(canonical.encode()).hexdigest()}"
    )
    manifest["manifest_version"] = MANIFEST_VERSION
    return manifest


__all__ = ["CAN_EXECUTE", "MANIFEST_VERSION", "freeze_nflverse_week_candidate"]
