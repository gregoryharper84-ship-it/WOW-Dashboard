"""Autonomous immutable evidence acquisition for fitted WNBA core prop routes.

The WNBA component models already have reviewed hydration and exact settlement
adapters, but the scheduled forward-evidence producer only seeded MLB snapshots.
That left WNBA POINTS/REBOUNDS/ASSISTS/THREE_POINTERS_MADE with zero forward
cohort observations despite registered runtime adapters.

This module fills only that acquisition gap. It discovers future official WNBA
games and current roster players, hydrates through the existing governed WNBA
evidence adapter, derives a deterministic *discovery-only* half-point line from
the prior-ten median, and persists the same immutable snapshot contract consumed
by the universal forward cohort.

No market line, sporting probability, calibration, certification, ranking,
publication authority, promotion, or execution behavior is created here.
``can_execute`` remains false unconditionally.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import httpx

from pick_request_runtime_core import PickRequestRow, RawPropEvidence, _snapshot_payload, _validate_evidence
from prop_auto_hydration import PropAutoHydrationError
from prop_auto_hydration_router import auto_hydrate_prop_evidence
import wnba_prop_auto_hydration as wnba

SPORT = "WNBA"
SOURCE_TYPE = "AUTONOMOUS_DISCOVERY"
PLATFORM = "WNBA_OFFICIAL_STATS_CDN_FORWARD_V1"
CAN_EXECUTE = False
CORE_STATS = ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE")


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _request_schedule(*, http_get: Callable[..., Any] = httpx.get) -> dict[str, Any]:
    payload = wnba._request(
        wnba.WNBA_SCHEDULE_URL,
        http_get=http_get,
        headers=wnba._cdn_headers(),
    )
    if not isinstance(payload, Mapping):
        raise TypeError("WNBA schedule response was not an object")
    return dict(payload)


def _team_label(node: Mapping[str, Any]) -> str | None:
    tricode = str(node.get("teamTricode") or "").strip().upper()
    if tricode:
        return tricode
    name = " ".join(
        [
            str(node.get("teamCity") or "").strip(),
            str(node.get("teamName") or "").strip(),
        ]
    ).strip()
    return name or None


def _schedule_players(
    payload: Mapping[str, Any],
    *,
    requested_date: str,
    requested_timezone: str,
    now: datetime,
    http_get: Callable[..., Any] = httpx.get,
) -> list[dict[str, Any]]:
    """Return deterministic future player/event identities.

    Event identity remains official WNBA schedule data. Player/team roster
    identity uses CommonTeamRoster first and the typed ESPN identity-only
    fallback only when the primary transport is unavailable.
    """
    try:
        zone = ZoneInfo(requested_timezone)
        date.fromisoformat(requested_date)
    except Exception:
        return []
    league = payload.get("leagueSchedule")
    blocks = league.get("gameDates") if isinstance(league, Mapping) else None
    if not isinstance(blocks, list):
        raise wnba.WNBAPropHydrationError(
            "WNBA_SCHEDULE_INVALID", "leagueSchedule.gameDates was missing"
        )

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        games = block.get("games") if isinstance(block, Mapping) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, Mapping):
                continue
            raw_start = game.get("gameDateTimeUTC") or game.get("gameDateUTC")
            event_start = _aware(raw_start)
            if (
                event_start is None
                or event_start <= now
                or event_start.astimezone(zone).date().isoformat() != requested_date
                or int(game.get("gameStatus") or 0) != 1
            ):
                continue
            game_id = str(game.get("gameId") or game.get("gameID") or "").strip()
            if not game_id:
                continue
            season = event_start.astimezone(zone).year
            for side, other_side in (("homeTeam", "awayTeam"), ("awayTeam", "homeTeam")):
                team = game.get(side) if isinstance(game.get(side), Mapping) else {}
                opponent = game.get(other_side) if isinstance(game.get(other_side), Mapping) else {}
                team_id = str(team.get("teamId") or "").strip()
                if not team_id:
                    continue
                roster = wnba._roster(team_id, season, team=team, http_get=http_get)
                for row in roster:
                    player = " ".join(str(row.get("PLAYER") or "").split())
                    player_id = str(row.get("PLAYER_ID") or row.get("PERSON_ID") or "").strip()
                    if not player:
                        continue
                    identity = (game_id, player_id or wnba._name_key(player))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    candidates.append(
                        {
                            "event_id": f"WNBA:{game_id}",
                            "official_game_id": game_id,
                            "event_start_time": event_start.isoformat(),
                            "player": player,
                            "player_id": player_id or None,
                            "team": _team_label(team),
                            "opponent": _team_label(opponent),
                        }
                    )
    candidates.sort(
        key=lambda row: (
            str(row["event_start_time"]),
            str(row.get("team") or ""),
            str(row["player"]),
        )
    )
    return candidates


def _candidate_line(game_log: list[float]) -> float:
    """Derive a deterministic half-point discovery line, never a market line."""
    values = [float(value) for value in game_log]
    if not values:
        raise ValueError("game_log cannot be empty")
    center = float(median(values))
    return float(int(center) + 0.5)


def _receipt(candidate: Mapping[str, Any], stat_type: str) -> dict[str, Any]:
    return {
        "event_id": candidate.get("event_id"),
        "official_game_id": candidate.get("official_game_id"),
        "event_start_time": candidate.get("event_start_time"),
        "sport": SPORT,
        "player": candidate.get("player"),
        "player_id": candidate.get("player_id"),
        "team": candidate.get("team"),
        "opponent": candidate.get("opponent"),
        "stat_type": stat_type,
        "line": None,
        "side": "MORE",
        "source_type": SOURCE_TYPE,
        "platform": PLATFORM,
        "source_snapshot_id": None,
        "write_status": "PREWRITE_PENDING",
        "terminal_reason": None,
        "can_execute": False,
    }


def acquire_wnba_prop_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    """Seed bounded WNBA component-prop snapshots for immutable forward evidence."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result: dict[str, Any] = {
        "status": "COMPLETED",
        "attempted": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "snapshot_write_succeeded": 0,
        "snapshot_write_failed": 0,
        "explicit_prewrite_exclusions": 0,
        "receipts": [],
        "blockers": [],
        "candidate_source": "WNBA_OFFICIAL_SCHEDULE_PLUS_GOVERNED_ROSTER_IDENTITY",
        "line_source": "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY",
        "stat_types": list(CORE_STATS),
        "probability_publishable": False,
        "can_execute": False,
    }
    if max_candidates <= 0:
        return result
    try:
        schedule = _request_schedule(http_get=http_get)
        players = _schedule_players(
            schedule,
            requested_date=requested_date,
            requested_timezone=requested_timezone,
            now=now,
            http_get=http_get,
        )
    except wnba.WNBAPropHydrationError as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [exc.code]
        return result
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [f"WNBA_PROP_SCHEDULE_ACQUISITION_FAILED:{type(exc).__name__}"]
        return result

    for candidate in players:
        for stat_type in CORE_STATS:
            if result["attempted"] >= max_candidates:
                break
            result["attempted"] += 1
            receipt = _receipt(candidate, stat_type)
            result["receipts"].append(receipt)
            try:
                raw = auto_hydrate_prop_evidence(
                    sport=SPORT,
                    player=str(candidate["player"]),
                    stat_type=stat_type,
                    event_start_time=str(candidate["event_start_time"]),
                    http_get=http_get,
                    now=now,
                    source_capture_timestamp=now.isoformat(),
                    source_label="V17_WNBA_AUTONOMOUS_FORWARD_DISCOVERY",
                    opponent=candidate.get("opponent"),
                    canonical_event_id=str(candidate["event_id"]),
                )
                evidence = RawPropEvidence.model_validate(raw)
                line = _candidate_line(evidence.game_log)
                row = PickRequestRow(
                    row_key=(
                        f"wnba-forward:{candidate['official_game_id']}:"
                        f"{candidate['player']}:{stat_type}:{line}"
                    ),
                    event_id=str(candidate["event_id"]),
                    event_start_time=str(candidate["event_start_time"]),
                    sport=SPORT,
                    player=str(candidate["player"]),
                    stat_type=stat_type,
                    line=line,
                    direction="MORE",
                    evidence=evidence,
                    source_type=SOURCE_TYPE,
                    platform=PLATFORM,
                    opponent=candidate.get("opponent"),
                    source_capture_timestamp=now.isoformat(),
                )
                normalized = _validate_evidence(row, stat_type)
                snapshot_id, _fingerprint, snapshot = _snapshot_payload(row, normalized)
                snapshot["source_snapshot_id"] = snapshot_id
                result["hydrated"] += 1
                receipt.update(
                    {
                        "line": line,
                        "source_snapshot_id": snapshot_id,
                        "write_status": "SNAPSHOT_WRITE_PENDING",
                    }
                )
                try:
                    db.table("wow_prop_evidence_snapshots").upsert(
                        snapshot, on_conflict="source_snapshot_id"
                    ).execute()
                except Exception as exc:
                    result["snapshot_write_failed"] += 1
                    result["held"] += 1
                    receipt["write_status"] = "SNAPSHOT_WRITE_FAILED"
                    receipt["terminal_reason"] = f"PROP_SNAPSHOT_WRITE_FAILED:{type(exc).__name__}"
                    result["blockers"].append(
                        f"{candidate['player']}:{stat_type}:{receipt['terminal_reason']}"
                    )
                    continue
                result["snapshot_write_succeeded"] += 1
                result["persisted"] += 1
                receipt["write_status"] = "SNAPSHOT_WRITE_SUCCEEDED"
                receipt["terminal_reason"] = "PERSISTED_AWAITING_CANONICAL_READBACK"
            except (PropAutoHydrationError, wnba.WNBAPropHydrationError) as exc:
                result["explicit_prewrite_exclusions"] += 1
                result["held"] += 1
                receipt["write_status"] = "PREWRITE_EXCLUDED"
                receipt["terminal_reason"] = getattr(exc, "code", type(exc).__name__)
                result["blockers"].append(
                    f"{candidate['player']}:{stat_type}:{receipt['terminal_reason']}"
                )
            except Exception as exc:
                result["explicit_prewrite_exclusions"] += 1
                result["held"] += 1
                receipt["write_status"] = "PREWRITE_EXCLUDED"
                receipt["terminal_reason"] = f"WNBA_PROP_ACQUISITION_ERROR:{type(exc).__name__}"
                result["blockers"].append(
                    f"{candidate['player']}:{stat_type}:{receipt['terminal_reason']}"
                )
        if result["attempted"] >= max_candidates:
            break

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] > 0 and result["snapshot_write_succeeded"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["persisted"] == 0 and result["attempted"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    elif result["held"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


__all__ = [
    "CAN_EXECUTE",
    "CORE_STATS",
    "acquire_wnba_prop_snapshots",
    "_candidate_line",
    "_schedule_players",
]
