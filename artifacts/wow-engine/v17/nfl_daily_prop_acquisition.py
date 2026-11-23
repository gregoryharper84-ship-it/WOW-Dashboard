"""Autonomous NFL Daily prop discovery for the V17 forward board.

Daily could only *score* supplied NFL candidates: its only autonomous producer
was the MLB probable-pitcher adapter, so an NFL slate was reported as zero new
candidates with stale rows reused. The NFL evidence control plane already
discovers pregame ESPN events, route-eligible offensive players and the four
certified stat families, but it was reachable only through an internal OIDC
route.

This adapter reuses that already-reviewed discovery (same event identity, same
roster/position route map, same governed hydration) and emits the exact
acquisition receipt contract the Daily props lane already reconciles. It keeps
*newly acquired* candidates separate from *already captured* snapshots, so
"zero new candidates" can never be confused with "reused snapshot rows".

No sportsbook line is invented: the discovery line remains the prior-ten
official stat median half-point and stays explicitly
``source_type=AUTONOMOUS_DISCOVERY``. No probability, calibration,
certification, promotion, publication, ranking, or execution authority is
created. ``can_execute`` is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

import nfl_prop_auto_hydration as nfl
from pick_request_runtime import (
    PickRequestRow,
    RawPropEvidence,
    _snapshot_payload,
    _validate_evidence,
)
from prop_auto_hydration import PropAutoHydrationError
from prop_auto_hydration_router import auto_hydrate_prop_evidence
from v17.nfl_prop_evidence_control_plane import (
    POSITION_STATS,
    STAT_TYPES,
    _discover_candidates,
)

CAN_EXECUTE = False
SPORT = "NFL"
SOURCE_TYPE = "AUTONOMOUS_DISCOVERY"
PLATFORM = "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1"
CANDIDATE_SOURCE = "NFL_ESPN_PREGAME_EVENT_ROSTER_POSITION"
LINE_SOURCE = "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY"


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _on_requested_slate(value: Any, requested_date: str, requested_timezone: str) -> bool:
    """Keep only candidates whose kickoff falls on the requested local date.

    ESPN groups games by the league slate date, which is not always the caller's
    local date. A resolved zone filters strictly; an unusable zone must not
    silently widen the slate, so it fails closed.
    """
    start = _aware(value)
    if start is None:
        return False
    try:
        zone = ZoneInfo(requested_timezone)
    except Exception:
        return False
    return start.astimezone(zone).date().isoformat() == requested_date


def _candidate_line(game_log: list[float], stat_type: str) -> float:
    values = [float(value) for value in game_log]
    if not values:
        raise ValueError("game_log cannot be empty")
    if str(stat_type).upper() == "ANYTIME_TD":
        return 0.5
    return float(int(float(median(values))) + 0.5)


def _base_receipt(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "sport": SPORT,
        "event_id": candidate.get("canonical_event_id"),
        "provider_event_id": candidate.get("provider_event_id"),
        "event_start_time": candidate.get("event_start_time"),
        "player": candidate.get("player"),
        "position": candidate.get("position"),
        "opponent": candidate.get("opponent"),
        "stat_type": candidate.get("stat_type"),
        "line": None,
        "side": "MORE",
        "source_type": SOURCE_TYPE,
        "platform": PLATFORM,
        "source_snapshot_id": None,
        "write_status": "PREWRITE_PENDING",
        "terminal_reason": None,
        "can_execute": False,
    }


def _existing_snapshot(db: Any, candidate: dict[str, Any]) -> bool:
    result = (
        db.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id")
        .eq("event_id", str(candidate["canonical_event_id"]))
        .eq("sport", SPORT)
        .eq("player", str(candidate["player"]))
        .eq("stat_type", str(candidate["stat_type"]))
        .limit(1)
        .execute()
    )
    return bool(result.data or [])


def acquire_daily_nfl_prop_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
    discover_candidates: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Persist a bounded set of autonomous NFL prop discovery snapshots.

    ``max_candidates`` is a hard row-attempt ceiling, so wiring NFL into Daily
    cannot multiply an existing Daily or scheduler budget.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result: dict[str, Any] = {
        "status": "COMPLETED",
        "sport": SPORT,
        "source_type": SOURCE_TYPE,
        "attempted": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "snapshot_write_succeeded": 0,
        "snapshot_write_failed": 0,
        "explicit_prewrite_exclusions": 0,
        "already_captured": 0,
        "new_candidates_acquired": 0,
        "reused_snapshot_rows": 0,
        "discovered_candidates": 0,
        "receipts": [],
        "blockers": [],
        "candidate_source": CANDIDATE_SOURCE,
        "line_source": LINE_SOURCE,
        "stat_types": list(STAT_TYPES),
        "position_route_map": {position: list(stats) for position, stats in POSITION_STATS.items()},
        "probability_publishable": False,
        "can_execute": False,
    }
    if max_candidates <= 0:
        return result

    discover = discover_candidates or _discover_candidates
    try:
        candidates = [
            candidate
            for candidate in discover(requested_date, now=now, http_get=http_get)
            if _on_requested_slate(
                candidate.get("event_start_time"), requested_date, requested_timezone
            )
        ]
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [
            getattr(exc, "code", f"NFL_PROP_DISCOVERY_FAILED:{type(exc).__name__}")
        ]
        return result

    result["discovered_candidates"] = len(candidates)
    for candidate in candidates[:max_candidates]:
        result["attempted"] += 1
        receipt = _base_receipt(candidate)
        result["receipts"].append(receipt)

        try:
            if _existing_snapshot(db, candidate):
                # Already canonical: a reused snapshot is not a new candidate.
                result["already_captured"] += 1
                result["reused_snapshot_rows"] += 1
                receipt["write_status"] = "EXISTING_SNAPSHOT_REUSED"
                receipt["terminal_reason"] = "CANONICAL_SNAPSHOT_ALREADY_PRESENT"
                continue
        except Exception as exc:
            result["held"] += 1
            receipt["write_status"] = "PREWRITE_EXCLUDED"
            receipt["terminal_reason"] = (
                f"NFL_EXISTING_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}"
            )
            result["blockers"].append(
                f"{candidate.get('player')}:{candidate.get('stat_type')}:{receipt['terminal_reason']}"
            )
            continue

        stat_type = str(candidate.get("stat_type") or "").strip().upper()
        try:
            raw = auto_hydrate_prop_evidence(
                sport=SPORT,
                player=str(candidate["player"]),
                stat_type=stat_type,
                event_start_time=str(candidate["event_start_time"]),
                http_get=http_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_DAILY_AUTONOMOUS_DISCOVERY_NFL",
                opponent=str(candidate.get("opponent")),
                canonical_event_id=str(candidate["canonical_event_id"]),
            )
            evidence = RawPropEvidence.model_validate(raw)
            line = _candidate_line(evidence.game_log, stat_type)
            row = PickRequestRow(
                row_key=(
                    f"daily:{candidate['canonical_event_id']}:{candidate['player']}:"
                    f"{stat_type}:{line}"
                ),
                event_id=str(candidate["canonical_event_id"]),
                event_start_time=str(candidate["event_start_time"]),
                sport=SPORT,
                player=str(candidate["player"]),
                stat_type=stat_type,
                line=line,
                direction="MORE",
                evidence=evidence,
                source_type=SOURCE_TYPE,
                platform=PLATFORM,
                opponent=str(candidate.get("opponent")),
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
                    f"{candidate.get('player')}:{stat_type}:{receipt['terminal_reason']}"
                )
                continue
            result["snapshot_write_succeeded"] += 1
            result["persisted"] += 1
            result["new_candidates_acquired"] += 1
            receipt["write_status"] = "SNAPSHOT_WRITE_SUCCEEDED"
            receipt["terminal_reason"] = "PERSISTED_AWAITING_CANONICAL_READBACK"
        except PropAutoHydrationError as exc:
            result["explicit_prewrite_exclusions"] += 1
            result["held"] += 1
            receipt["write_status"] = "PREWRITE_EXCLUDED"
            receipt["terminal_reason"] = exc.code
            result["blockers"].append(f"{candidate.get('player')}:{stat_type}:{exc.code}")
        except Exception as exc:
            result["explicit_prewrite_exclusions"] += 1
            result["held"] += 1
            receipt["write_status"] = "PREWRITE_EXCLUDED"
            receipt["terminal_reason"] = (
                f"PROP_DAILY_NFL_ACQUISITION_ERROR:{type(exc).__name__}"
            )
            result["blockers"].append(
                f"{candidate.get('player')}:{stat_type}:{receipt['terminal_reason']}"
            )

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] > 0 and result["snapshot_write_succeeded"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["held"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


__all__ = [
    "CAN_EXECUTE",
    "CANDIDATE_SOURCE",
    "LINE_SOURCE",
    "PLATFORM",
    "POSITION_STATS",
    "SPORT",
    "STAT_TYPES",
    "acquire_daily_nfl_prop_snapshots",
]
