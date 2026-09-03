"""Server-owned V17 Daily prop acquisition for certified MLB routes.

Daily must not treat an empty canonical prop snapshot table as proof that the
slate has no prop candidates.  For MLB pitcher strikeouts, the backend already
has an official probable-pitcher schedule source and an automatic evidence
hydrator.  This module uses those existing producing layers to seed exact,
immutable candidate snapshots before Daily selects rows.

No sportsbook line is invented.  When no exact external line feed is available,
we derive only half-point candidate lines from the player's prior-ten official
strikeout median for forward calibration/discovery.  Those rows are explicitly
source_type=AUTONOMOUS_DISCOVERY and remain non-executable.  User-supplied
screenshot/PDF rows continue to use /score-pick-request with their exact visible
line and are never replaced by these autonomous candidates.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import httpx

from pick_request_runtime import PickRequestRow, RawPropEvidence, _snapshot_payload, _validate_evidence
from prop_auto_hydration import (
    AUTO_HYDRATION_PROVIDER,
    MLB_STATS_API_BASE,
    PropAutoHydrationError,
    auto_hydrate_prop_evidence,
)

SPORT = "MLB"
STAT_TYPE = "PITCHER_STRIKEOUTS"
SOURCE_TYPE = "AUTONOMOUS_DISCOVERY"
PLATFORM = "MLB_STATS_API_OFFICIAL_V1"


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _request_schedule(slate_date: str, *, http_get: Any = httpx.get) -> dict[str, Any]:
    response = http_get(
        f"{MLB_STATS_API_BASE}/schedule",
        params={"sportId": "1", "date": slate_date, "hydrate": "probablePitcher,team,venue"},
        timeout=8.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("MLB schedule response was not an object")
    return payload


def _schedule_pitchers(payload: dict[str, Any], *, requested_date: str, requested_timezone: str, now: datetime) -> list[dict[str, Any]]:
    try:
        zone = ZoneInfo(requested_timezone)
        date.fromisoformat(requested_date)
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    for block in payload.get("dates") or []:
        if not isinstance(block, dict):
            continue
        for game in block.get("games") or []:
            if not isinstance(game, dict):
                continue
            event_start = _aware(game.get("gameDate"))
            if event_start is None or event_start <= now or event_start.astimezone(zone).date().isoformat() != requested_date:
                continue
            game_pk = str(game.get("gamePk") or "").strip()
            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            if not game_pk:
                continue
            for side in ("home", "away"):
                side_node = teams.get(side) if isinstance(teams.get(side), dict) else {}
                probable = side_node.get("probablePitcher") if isinstance(side_node.get("probablePitcher"), dict) else {}
                player = " ".join(str(probable.get("fullName") or "").split())
                if not player:
                    continue
                candidates.append({
                    "event_id": f"MLB:{game_pk}",
                    "event_start_time": event_start.isoformat(),
                    "player": player,
                })
    return candidates


def _candidate_line(game_log: list[float]) -> float:
    """Return a deterministic half-point discovery line; never a market line."""
    center = float(median([float(value) for value in game_log]))
    return float(int(center) + 0.5)


def acquire_daily_prop_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    now: datetime | None = None,
    http_get: Any = httpx.get,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result: dict[str, Any] = {
        "status": "COMPLETED",
        "attempted": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "blockers": [],
        "candidate_source": "MLB_OFFICIAL_PROBABLE_PITCHERS",
        "line_source": "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY",
        "can_execute": False,
    }
    if max_candidates <= 0:
        return result
    try:
        schedule = _request_schedule(requested_date, http_get=http_get)
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [f"MLB_PROP_SCHEDULE_ACQUISITION_FAILED:{type(exc).__name__}"]
        return result

    candidates = _schedule_pitchers(schedule, requested_date=requested_date, requested_timezone=requested_timezone, now=now)
    for candidate in candidates[:max_candidates]:
        result["attempted"] += 1
        try:
            raw = auto_hydrate_prop_evidence(
                sport=SPORT,
                player=candidate["player"],
                stat_type=STAT_TYPE,
                event_start_time=candidate["event_start_time"],
                http_get=http_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_DAILY_AUTONOMOUS_DISCOVERY",
            )
            evidence = RawPropEvidence.model_validate(raw)
            line = _candidate_line(evidence.game_log)
            row = PickRequestRow(
                row_key=f"daily:{candidate['event_id']}:{candidate['player']}:{line}",
                event_id=candidate["event_id"],
                event_start_time=candidate["event_start_time"],
                sport=SPORT,
                player=candidate["player"],
                stat_type=STAT_TYPE,
                line=line,
                direction="MORE",
                evidence=evidence,
                source_type=SOURCE_TYPE,
                platform=PLATFORM,
                source_capture_timestamp=now.isoformat(),
            )
            normalized = _validate_evidence(row, STAT_TYPE)
            snapshot_id, _fingerprint, snapshot = _snapshot_payload(row, normalized)
            snapshot["source_snapshot_id"] = snapshot_id
            db.table("wow_prop_evidence_snapshots").upsert(snapshot, on_conflict="source_snapshot_id").execute()
            result["hydrated"] += 1
            result["persisted"] += 1
        except PropAutoHydrationError as exc:
            result["held"] += 1
            result["blockers"].append(f"{candidate['player']}:{exc.code}")
        except Exception as exc:
            result["held"] += 1
            result["blockers"].append(f"{candidate['player']}:PROP_DAILY_ACQUISITION_ERROR:{type(exc).__name__}")

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["persisted"] == 0 and result["attempted"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result
