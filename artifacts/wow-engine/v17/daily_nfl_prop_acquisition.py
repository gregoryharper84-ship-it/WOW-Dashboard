"""Daily autonomous NFL core-prop candidate acquisition (discovery only).

Reuses the reviewed NFL forward-evidence producer pieces (ESPN pregame
scoreboard + roster discovery, governed NFL evidence hydration, immutable
snapshot payloads) and emits the same receipt/accounting shape as the MLB Daily
producer, so ``prewrite`` and ``persisted -> canonical`` reconciliation holds:

    attempted = snapshot_write_succeeded + snapshot_write_failed
                + explicit_prewrite_exclusions

Rows are source_type=AUTONOMOUS_DISCOVERY with prior-ten-median half-point
discovery lines (ANYTIME_TD uses 0.5). The line is NOT a market line. No
probability, calibration, certification, ranking or execution authority is
created here; scoring stays with the governed NFL specialist and its gates, so
a missing/failed specialist still fails closed downstream.
can_execute=false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from pick_request_runtime import PickRequestRow, RawPropEvidence, _snapshot_payload, _validate_evidence
from prop_auto_hydration import PropAutoHydrationError
from prop_auto_hydration_router import auto_hydrate_prop_evidence
from v17 import nfl_prop_evidence_control_plane as _cp

SPORT = "NFL"
SOURCE_TYPE = "AUTONOMOUS_DISCOVERY"
PLATFORM = "ESPN_NFL_IDENTITY_NFLVERSE_STATS_DAILY_V1"
CANDIDATE_SOURCE = "NFL_ESPN_PREGAME_SCOREBOARD_ROSTER"
STAT_TYPES = _cp.STAT_TYPES


def _local_date_matches(candidate: dict[str, Any], requested_date: str, requested_timezone: str) -> bool:
    start = _cp._aware(candidate.get("event_start_time"))
    if start is None:
        return False
    try:
        return start.astimezone(ZoneInfo(requested_timezone)).date().isoformat() == requested_date
    except Exception:
        return False


def _interleave_by_event(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round-robin across events so a row ceiling cannot starve later games."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(str(candidate["canonical_event_id"]), []).append(candidate)
    ordered = [groups[key] for key in sorted(groups, key=lambda k: (groups[k][0]["event_start_time"], k))]
    output: list[dict[str, Any]] = []
    depth = 0
    while any(depth < len(group) for group in ordered):
        for group in ordered:
            if depth < len(group):
                output.append(group[depth])
        depth += 1
    return output


def _receipt(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": candidate.get("canonical_event_id"),
        "event_start_time": candidate.get("event_start_time"),
        "sport": SPORT,
        "player": candidate.get("player"),
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


def acquire_daily_nfl_prop_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
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
        "already_captured_candidates": 0,
        "receipts": [],
        "blockers": [],
        "candidate_source": CANDIDATE_SOURCE,
        "line_source": "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY",
        "stat_types": list(STAT_TYPES),
        "discovered_candidates": 0,
        "unattempted_due_to_row_ceiling": 0,
        "probability_publishable": False,
        "can_execute": False,
    }
    if max_candidates <= 0:
        # No NFL discovery call was made. Unknown candidate counts are not zero.
        result["status"] = "NOT_ATTEMPTED_ROW_CEILING"
        result["discovered_candidates"] = None
        result["unattempted_due_to_row_ceiling"] = None
        result["blockers"] = ["NFL_PROP_ROW_CEILING_EXHAUSTED"]
        return result
    cached_get = _cp._cached_http_get(http_get)
    try:
        discovered = _cp._discover_candidates(requested_date, now=now, http_get=cached_get)
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["discovered_candidates"] = None
        result["unattempted_due_to_row_ceiling"] = None
        result["blockers"] = [getattr(exc, "code", None) or f"NFL_PROP_DISCOVERY_FAILED:{type(exc).__name__}"]
        return result

    candidates = _interleave_by_event(
        [c for c in discovered if _local_date_matches(c, requested_date, requested_timezone)]
    )
    result["discovered_candidates"] = len(candidates)
    pending: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            captured = _cp._existing_snapshot(db, candidate)
        except Exception as exc:
            result["status"] = "DATA_UNOBTAINABLE"
            result["unattempted_due_to_row_ceiling"] = None
            result["blockers"] = [f"NFL_PROP_SNAPSHOT_PREFLIGHT_FAILED:{type(exc).__name__}"]
            return result
        if captured:
            result["already_captured_candidates"] += 1
        else:
            pending.append(candidate)
    result["unattempted_due_to_row_ceiling"] = max(len(pending) - max_candidates, 0)

    for candidate in pending[:max_candidates]:
        stat_type = str(candidate["stat_type"])
        label = f"{candidate['player']}:{stat_type}"
        result["attempted"] += 1
        receipt = _receipt(candidate)
        result["receipts"].append(receipt)
        try:
            raw = auto_hydrate_prop_evidence(
                sport=SPORT,
                player=str(candidate["player"]),
                stat_type=stat_type,
                event_start_time=str(candidate["event_start_time"]),
                http_get=cached_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_DAILY_AUTONOMOUS_DISCOVERY",
                opponent=str(candidate["opponent"]),
                canonical_event_id=str(candidate["canonical_event_id"]),
            )
            evidence = RawPropEvidence.model_validate(raw)
            line = _cp._candidate_line(evidence.game_log, stat_type)
            row = PickRequestRow(
                row_key=f"daily:{candidate['canonical_event_id']}:{candidate['player']}:{stat_type}:{line}",
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
                opponent=str(candidate["opponent"]),
                source_capture_timestamp=now.isoformat(),
            )
            normalized = _validate_evidence(row, stat_type)
            snapshot_id, _fingerprint, snapshot = _snapshot_payload(row, normalized)
            snapshot["source_snapshot_id"] = snapshot_id
            result["hydrated"] += 1
            receipt.update({"line": line, "source_snapshot_id": snapshot_id, "write_status": "SNAPSHOT_WRITE_PENDING"})
            try:
                db.table("wow_prop_evidence_snapshots").upsert(snapshot, on_conflict="source_snapshot_id").execute()
            except Exception as exc:
                result["snapshot_write_failed"] += 1
                result["held"] += 1
                receipt["write_status"] = "SNAPSHOT_WRITE_FAILED"
                receipt["terminal_reason"] = f"PROP_SNAPSHOT_WRITE_FAILED:{type(exc).__name__}"
                result["blockers"].append(f"{label}:{receipt['terminal_reason']}")
                continue
            result["snapshot_write_succeeded"] += 1
            result["persisted"] += 1
            receipt["write_status"] = "SNAPSHOT_WRITE_SUCCEEDED"
            receipt["terminal_reason"] = "PERSISTED_AWAITING_CANONICAL_READBACK"
        except PropAutoHydrationError as exc:
            result["explicit_prewrite_exclusions"] += 1
            result["held"] += 1
            receipt["write_status"] = "PREWRITE_EXCLUDED"
            receipt["terminal_reason"] = exc.code
            result["blockers"].append(f"{label}:{exc.code}")
        except Exception as exc:
            result["explicit_prewrite_exclusions"] += 1
            result["held"] += 1
            receipt["write_status"] = "PREWRITE_EXCLUDED"
            receipt["terminal_reason"] = f"NFL_DAILY_ACQUISITION_ERROR:{type(exc).__name__}"
            result["blockers"].append(f"{label}:{receipt['terminal_reason']}")

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] > 0 and result["snapshot_write_succeeded"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["held"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


__all__ = ["CANDIDATE_SOURCE", "PLATFORM", "acquire_daily_nfl_prop_snapshots"]
