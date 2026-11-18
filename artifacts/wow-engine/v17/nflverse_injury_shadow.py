"""Read-only NFL injury report reconciliation from an already-captured nflverse CSV.

This does not retrieve NFL.com pages, certify report completeness, establish
game-day inactives, or authorize scoring. It consumes the hashed injury asset
already supported by nfl_event_data_p1 and keeps all player clearance held.
Attribution: nflverse/nflverse-data, CC BY 4.0 (dataset-specific rights apply).
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nfl_event_data_p1 import CapturedAsset, DATASET_INJURIES, SOURCE_FAMILY

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
MAX_REPORT_AGE = timedelta(hours=36)
REQUIRED = frozenset({
    "season", "team", "week", "gsis_id", "report_status",
    "practice_status", "date_modified",
})
OUT_DESIGNATIONS = frozenset({"OUT", "INACTIVE", "IR", "INJURED_RESERVE"})
RISK_DESIGNATIONS = frozenset({"QUESTIONABLE", "DOUBTFUL", "GAME_TIME_DECISION"})


def _datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _token(value: Any) -> str:
    return "_".join(str(value or "").strip().upper().replace("-", " ").split())


def inspect_captured_injuries(
    capture: CapturedAsset,
    *,
    gsis_id: str,
    team: str,
    season: int,
    week: int,
    season_type: str,
    official_event_id: str,
    event_start_utc: str,
    now_utc: str,
) -> dict[str, Any]:
    """Return a fail-closed, research-only event-scoped injury receipt.

    Only an exact GSIS ID is accepted: name matching or a roster match cannot
    clear a player. Neither a positive designation nor an absent row proves
    a complete *official* report. Consumers must not turn this into AVAILABLE.
    """
    identity = {
        "gsis_id": str(gsis_id or "").strip(),
        "team": _token(team),
        "season": season,
        "week": week,
        "season_type": _token(season_type),
        "official_event_id": str(official_event_id or "").strip(),
    }
    receipt: dict[str, Any] = {
        **identity,
        "status": "UNVERIFIED",
        "blocker": "NFLVERSE_INJURY_EVIDENCE_UNVERIFIED",
        "reported_designation": None,
        "practice_status": None,
        "reported_at": None,
        "report_complete": False,
        "available_for_scoring": False,
        "requires_final_pregame_recheck": True,
        "dataset_name": DATASET_INJURIES,
        "source_family": SOURCE_FAMILY,
        "source_url": getattr(capture, "requested_url", None),
        "source_sha256": getattr(capture, "content_sha256", None),
        "source_fetched_at": getattr(capture, "fetched_at", None),
        "license": "CC-BY-4.0: attribution to nflverse; verify upstream rights",
        "can_execute": False,
        "probability_publishable": False,
    }

    def hold(code: str) -> dict[str, Any]:
        receipt["blocker"] = code
        return receipt

    now, kickoff = _datetime(now_utc), _datetime(event_start_utc)
    fetched = _datetime(getattr(capture, "fetched_at", None))
    if (not now or not kickoff or now >= kickoff or not fetched
            or fetched > now or fetched >= kickoff):
        return hold("NFLVERSE_INJURY_EVENT_OR_CAPTURE_TIME_UNVERIFIED")
    if (not identity["gsis_id"] or not identity["team"] or not identity["official_event_id"]
            or identity["season_type"] not in {"REG", "POST"}
            or isinstance(season, bool) or not isinstance(season, int) or season < 2009
            or isinstance(week, bool) or not isinstance(week, int) or week < 1):
        return hold("NFLVERSE_INJURY_IDENTITY_UNRESOLVED")
    if getattr(capture, "dataset_name", None) != DATASET_INJURIES or getattr(capture, "season", None) != season:
        return hold("NFLVERSE_INJURY_CAPTURE_SCOPE_MISMATCH")

    try:
        raw = Path(capture.local_path).read_bytes()
    except (OSError, TypeError, ValueError):
        return hold("NFLVERSE_INJURY_CAPTURE_UNREADABLE")
    if hashlib.sha256(raw).hexdigest() != capture.content_sha256:
        return hold("NFLVERSE_INJURY_CAPTURE_HASH_MISMATCH")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        fieldnames = reader.fieldnames or []
        columns = set(fieldnames)
        if (not REQUIRED.issubset(columns)
                or not ({"season_type", "game_type"} & columns)
                or len(fieldnames) != len(columns)):
            return hold("NFLVERSE_INJURY_SCHEMA_INVALID")
        rows = list(reader)
        if any(row.get(None) is not None for row in rows):
            return hold("NFLVERSE_INJURY_SCHEMA_INVALID")
    except (UnicodeError, csv.Error):
        return hold("NFLVERSE_INJURY_SCHEMA_INVALID")

    same_id = [r for r in rows if str(r.get("gsis_id") or "").strip() == identity["gsis_id"]]
    same_scope = [
        r for r in same_id
        if str(r.get("team") or "").strip().upper() == identity["team"]
        and str(r.get("season") or "").strip() == str(season)
        and str(r.get("week") or "").strip() == str(week)
        and _token(r.get("season_type") or r.get("game_type")) == identity["season_type"]
    ]
    if not same_scope:
        return hold("NFLVERSE_INJURY_PLAYER_TEAM_OR_WEEK_MISMATCH" if same_id else "NFLVERSE_INJURY_TARGET_ROW_MISSING")

    parsed: list[tuple[datetime, str, str]] = []
    for row in same_scope:
        date = _datetime(row.get("date_modified"))
        if date is None or date > now or date >= kickoff:
            return hold("NFLVERSE_INJURY_REPORT_TIMESTAMP_UNVERIFIED")
        parsed.append((date, _token(row.get("report_status")), _token(row.get("practice_status"))))
    latest = max(record[0] for record in parsed)
    latest_entries = {(game, practice) for dt, game, practice in parsed if dt == latest}
    if len(latest_entries) != 1:
        return hold("NFLVERSE_INJURY_REPORT_CONFLICT")
    designation, practice = next(iter(latest_entries))
    receipt.update({
        "reported_designation": designation or None,
        "practice_status": practice or None,
        "reported_at": latest.isoformat(),
        "source_row_n": len(parsed),
    })
    if now - latest > MAX_REPORT_AGE:
        return hold("NFLVERSE_INJURY_REPORT_STALE")
    if designation in OUT_DESIGNATIONS:
        return hold("NFLVERSE_INJURY_REPORTED_OUT_RECHECK_REQUIRED")
    if designation in RISK_DESIGNATIONS:
        return hold("NFLVERSE_INJURY_RISK_RECHECK_REQUIRED")
    return hold("NFLVERSE_INJURY_REPORT_NOT_OFFICIAL_CLEARANCE")


__all__ = ["inspect_captured_injuries", "CAN_EXECUTE", "PROBABILITY_PUBLISHABLE"]
