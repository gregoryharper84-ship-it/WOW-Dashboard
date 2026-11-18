"""Bounded, server-owned V17 Daily snapshot runner.

Daily first consumes canonical pregame snapshots. If the PROPS lane is empty,
it invokes the certified server-owned prop acquisition producer once, receipts
that producer's write outcomes, and then performs a paged canonical readback.
Persisted rows may not silently disappear between producer and canonical store.
It never invents a probability or executable wager.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from v17 import cross_sport_discovery_feed as discovery_feed
from v17 import free_core_source_policy as source_policy
from v17 import memory_admission
from v17 import cross_sport_winner_discovery as discovery
from v17 import team_event_bridge_runtime as bridge_runtime
from v17.daily_prop_acquisition import acquire_daily_prop_snapshots
from v17.daily_response_contract import (
    ACQUISITION_DETAIL_PAGE_DEFAULT_LIMIT,
    ACQUISITION_DETAIL_PAGE_MAX_LIMIT,
    DETAIL_PAGE_DEFAULT_LIMIT,
    DETAIL_PAGE_MAX_LIMIT,
    acquisition_detail_reference,
    compact_response,
    persist_acquisition_detail,
    persist_row_detail,
    read_acquisition_detail_page,
    read_row_detail_page,
    summarize_acquisition_by_family,
)
from v17.daily_terminal_reduction import (
    assert_no_terminal_upgrade,
    classify_stage_status,
    reduce_row_terminal,
)
from v17.detailed_evidence_install import install_v17_detailed_evidence
from v17.prop_evidence_acquisition_scheduler import run_prop_evidence_acquisition_loop
from v17.prop_forward_cohort_route import install_prop_forward_cohort_route
from v17.player_availability_gate import (
    PLAYER_AVAILABILITY_RECHECK_REQUIRED,
    evaluate_player_availability,
)
from v17.prop_canonical_identity import canonical_event_start_time, canonical_source_snapshot_ids, canonical_sporting_key, canonicalize_prop_manifest
from v17.team_event_official_publication_guard import evaluate_team_event_official_publication
from v17.team_event_request_runtime import TeamEventRequest, score_team_event_request


class DailySnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_slate_date: str
    requested_timezone: str = Field(min_length=1, max_length=64)
    # SPREAD is opt-in research only: it persists the ledger's exact line and a
    # typed hold. It never publishes a probability and is not a default lane.
    lanes: list[Literal["PROPS", "MONEYLINE", "SPREAD"]] = Field(
        default_factory=lambda: ["PROPS", "MONEYLINE"]
    )
    max_props: int = Field(default=6, ge=0, le=12)
    max_team_events: int = Field(default=6, ge=0, le=12)
    # COMPACT is the normal client contract: full per-row evidence is persisted
    # and read back through the paged retrieval route instead of being inlined.
    response_mode: Literal["COMPACT", "FULL"] = "COMPACT"


def _detail(exc: HTTPException) -> dict[str, Any]:
    return dict(exc.detail) if isinstance(exc.detail, dict) else {"code": "HTTP_EXCEPTION", "message": str(exc.detail)}


def _withhold_publication(value: Any) -> Any:
    """Copy an ineligible result, retaining original claims only as audit data.

    Full responses and durable detail share this projection. Nested direction,
    qualification and governance receipts cannot contradict the row ceiling.
    Numerical model evidence and blockers remain intact.
    """
    if isinstance(value, list):
        return [_withhold_publication(item) for item in value]
    if not isinstance(value, dict):
        return value
    flags = {key: value[key] for key in (
        "probability_publishable", "rank_eligible", "probability_rank_eligible",
        "official_leaderboard_eligible",
    ) if key in value}
    projected = {
        key: item if key == "prepublication_claim" else _withhold_publication(item)
        for key, item in value.items()
    }
    if flags:
        # An existing guard may already have captured the earlier scorer claim.
        projected.setdefault("prepublication_claim", flags)
        projected.update({key: False for key in flags})
    if "can_execute" in projected:
        projected["can_execute"] = False
    return projected


def _terminal_row(
    lane: str,
    identity: dict[str, Any],
    payload: dict[str, Any],
    reduction: dict[str, Any],
) -> dict[str, Any]:
    """Build one terminal row whose status is the reduced stage terminal.

    The row status is never computed independently of the stage ladder, so the
    wrapper cannot report a terminal softer than the stages it aggregated.
    """
    row_status = reduction["final_terminal"]
    publishable = bool(
        payload.get("probability_publishable") is True
        and payload.get("rank_eligible") is True
        and row_status == "COMPLETED"
    )
    if not publishable:
        payload = _withhold_publication(payload)
        payload = {**payload, "probability_publishable": False, "rank_eligible": False}
    return {
        "lane": lane,
        "identity": identity,
        "result": payload,
        "terminal": True,
        "row_status": row_status,
        "terminal_reduction": reduction,
        "probability_publishable": publishable,
        "rank_eligible": publishable,
        "can_execute": False,
    }


def _props_row_reduction(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce both direction assessments to one row terminal, lowest wins."""
    return assert_no_terminal_upgrade(
        reduce_row_terminal(outcome.get("status") for outcome in outcomes)
    )


def _reconcile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"COMPLETED": 0, "HELD": 0, "REJECTED": 0, "PURGED": 0, "INVALID": 0}
    unclassified = 0
    for row in rows:
        status = row.get("row_status")
        if status in counts:
            counts[status] += 1
        else:
            unclassified += 1
    rows_in = len(rows)
    terminal_total = sum(counts.values())
    return {
        "rows_in": rows_in,
        "rows_completed": counts["COMPLETED"],
        "rows_held": counts["HELD"],
        "rows_rejected": counts["REJECTED"],
        "rows_purged": counts["PURGED"],
        "rows_invalid": counts["INVALID"],
        "rows_unclassified": unclassified,
        "balanced": unclassified == 0 and terminal_total == rows_in,
    }


def _future(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.utcoffset() is not None and parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _matching_slate(row: dict[str, Any], requested_date: str, requested_timezone: str) -> bool:
    try:
        zone = ZoneInfo(requested_timezone)
        parsed = datetime.fromisoformat(str(row.get("event_start_time")).replace("Z", "+00:00"))
        return parsed.utcoffset() is not None and parsed.astimezone(zone).date().isoformat() == requested_date
    except (TypeError, ValueError, Exception):
        return False


def _snapshot_captured_at(row: dict[str, Any]) -> datetime:
    raw = str(row.get("captured_at") or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _read_side_availability_blocker(row: dict[str, Any]) -> str | None:
    """Re-evaluate persisted role_status so Daily does not trust blockers alone.

    When ``role_status`` is absent (older rows / unread column), fall back to
    persisted blockers. When it is present, official availability is authoritative
    even if ``blockers`` was left empty by a writer-trust gap.
    """
    role_status = row.get("role_status")
    if role_status is None:
        return None
    decision = evaluate_player_availability(role_status)
    if decision.blocker:
        return str(decision.blocker)
    if decision.requires_revalidation:
        return PLAYER_AVAILABILITY_RECHECK_REQUIRED
    return None


def _effective_evidence_blockers(row: dict[str, Any]) -> list[str]:
    blockers = [str(code) for code in (row.get("blockers") or []) if str(code).strip()]
    availability_blocker = _read_side_availability_blocker(row)
    if availability_blocker and availability_blocker not in blockers:
        blockers.append(availability_blocker)
    return blockers


def _evidence_not_ready(row: dict[str, Any]) -> bool:
    hydration = str(row.get("hydration_status") or "").strip().upper()
    return hydration != "PASS" or bool(_effective_evidence_blockers(row))


def _merge_prop_evidence_authority(
    previous: dict[str, Any] | None, row: dict[str, Any]
) -> dict[str, Any] | None:
    """Newest snapshot wins; same-time conflicts fail closed to non-ready evidence.

    Do not prefilter PASS in SQL: a newer FAILED/blocked observation must remain
    visible to canonicalization so an older clean PASS cannot clear it.
    """
    peers = [item for item in (previous, row) if item is not None]
    candidates = canonicalize_prop_manifest(peers)
    if not candidates:
        return None
    chosen = candidates[0]
    capture_times = [(peer, _snapshot_captured_at(peer)) for peer in peers]
    # An unorderable blocked observation (e.g. PostgreSQL infinity) must
    # never be silently discarded in favor of a finite-time PASS.
    unknown_blocked = next(
        (peer for peer, stamp in capture_times
         if stamp == datetime.min.replace(tzinfo=timezone.utc) and _evidence_not_ready(peer)),
        None,
    )
    newest_ts = max(stamp for _, stamp in capture_times)
    newest_peers = [peer for peer, stamp in capture_times if stamp == newest_ts]
    blocked_newest = (
        unknown_blocked if unknown_blocked is not None
        else next((peer for peer in newest_peers if _evidence_not_ready(peer)), None)
    )
    if blocked_newest is None:
        return chosen
    merged = dict(blocked_newest)
    merged["source_snapshot_ids"] = list(chosen.get("source_snapshot_ids") or [])
    merged["source_instance_count"] = chosen.get("source_instance_count")
    merged["canonical_sporting_key"] = chosen.get("canonical_sporting_key")
    return merged


def _prop_manifest_rows(
    db: Any,
    requested_date: str,
    requested_timezone: str,
    *,
    page_size: int = 250,
    exclusions: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    retain_exclusion_snapshot_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Stream ordered snapshot history, retaining one start-time group at a time.

    Sporting identity includes event start time, so rows from a finished ordered
    group cannot supersede later groups. Newest selection still precedes blocker
    filtering, and the SQL read must not hide non-PASS hydration before that
    selection. Historical exclusions are counted rather than retained in full;
    keep bounded display detail plus rows needed for current write receipts.
    """
    selected = (
        "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,"
        "stat_type,line,hydration_status,blockers,role_status"
    )
    manifest: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    excluded_count = 0
    reason_counts: dict[str, int] = {}
    retained_by_reason: dict[str, int] = {}
    retain_ids = retain_exclusion_snapshot_ids or set()
    active: dict[tuple[str, ...], dict[str, Any]] = {}
    seen_keys: set[tuple[str, ...]] = set()
    group_time: str | None = None
    raw_count = merged = offset = 0
    if exclusions is not None:
        exclusions.clear()
    if stats is not None:
        stats.clear()
        stats.update(raw_rows_read=0, canonical_merged_rows=0, read_complete=False)

    def account(row: dict[str, Any]) -> None:
        nonlocal excluded_count
        effective_blockers = _effective_evidence_blockers(row)
        if not _future(row.get("event_start_time")):
            reason = "EVENT_NOT_PREGAME"
        elif not _matching_slate(row, requested_date, requested_timezone):
            reason = "NOT_REQUESTED_SLATE"
        elif _evidence_not_ready(row):
            reason = "EVIDENCE_BLOCKED"
        else:
            manifest.append(row)
            return
        excluded_count += 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        ids = canonical_source_snapshot_ids([row])
        if retained_by_reason.get(reason, 0) < 50 or ids & retain_ids:
            retained_by_reason[reason] = retained_by_reason.get(reason, 0) + 1
            excluded.append({
                **{key: row.get(key) for key in (
                    "source_snapshot_id", "event_id", "sport", "player", "stat_type", "line",
                )},
                "source_snapshot_ids": sorted(ids & retain_ids),
                "reason": reason,
                "blockers": effective_blockers[:4],
                "can_execute": False,
            })

    while True:
        # Intentionally no hydration_status=PASS SQL prefilter: newer FAILED or
        # blocked evidence must participate in canonical newest-wins selection.
        query = (
            db.table("wow_prop_evidence_snapshots")
            .select(selected)
            .order("event_start_time")
            .order("source_snapshot_id")
        )
        ranged = getattr(query, "range", None)
        if callable(ranged):
            batch = ranged(offset, offset + page_size - 1).execute().data or []
        else:
            batch = query.limit(page_size).execute().data or []
        for raw in batch:
            row = dict(raw)
            raw_count += 1
            key = canonical_sporting_key(row)
            row_time = canonical_event_start_time(row.get("event_start_time"))
            if group_time is not None and row_time != group_time:
                for candidate in active.values():
                    account(candidate)
                active.clear()
                seen_keys.clear()
            group_time = row_time
            if key is None:
                # An incomplete identity cannot merge with any other instance.
                for candidate in canonicalize_prop_manifest([row]):
                    account(candidate)
                continue
            if key in seen_keys:
                merged += 1
            seen_keys.add(key)
            previous = active.get(key)
            selected_row = _merge_prop_evidence_authority(previous, row)
            if selected_row is not None:
                active[key] = selected_row
            else:
                active.pop(key, None)
        if stats is not None:
            stats.update(raw_rows_read=raw_count, canonical_merged_rows=merged)
        if not callable(ranged) or len(batch) < page_size:
            break
        offset += page_size
    for candidate in active.values():
        account(candidate)
    excluded.sort(key=lambda row: (
        0 if canonical_source_snapshot_ids([row]) & retain_ids else 1,
        0 if row["reason"] == "EVIDENCE_BLOCKED" else 1,
    ))
    if exclusions is not None:
        exclusions.extend(excluded)
    if stats is not None:
        stats.update(
            read_complete=callable(ranged) or len(batch) < page_size,
            excluded_rows=excluded_count,
            excluded_by_reason=reason_counts,
        )
    return manifest


def _prop_rows(
    db: Any, requested_date: str, requested_timezone: str, limit: int,
    *, exclusions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return _prop_manifest_rows(
        db, requested_date, requested_timezone, exclusions=exclusions
    )[:limit]


def _team_rows(db: Any, requested_date: str, limit: int) -> list[dict[str, Any]]:
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            rows = db.table("wow_mlb_forward_shadow_events").select("official_event_id,official_date,event_start_time,home_team,away_team,venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id,snapshot_timestamp,feature_hydration_status").eq("official_date", requested_date).eq("feature_hydration_status", "PASS").order("event_start_time").limit(limit).execute().data or []
            return [dict(row) for row in rows if _future(row.get("event_start_time"))]
        except Exception as exc:
            last_exc = exc
            if type(exc).__name__ != "ReadTimeout" or attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise last_exc or RuntimeError("TEAM_EVENT_SNAPSHOT_QUERY_FAILED")


def _acquisition_counts(acquisition: dict[str, Any] | None) -> dict[str, int]:
    acquisition = acquisition or {}
    return {
        "lane_discovered_raw": int(acquisition.get("attempted") or 0),
        "lane_snapshot_write_succeeded": int(acquisition.get("snapshot_write_succeeded", acquisition.get("persisted", 0)) or 0),
        "lane_snapshot_write_failed": int(acquisition.get("snapshot_write_failed") or 0),
        "explicit_prewrite_exclusions": int(acquisition.get("explicit_prewrite_exclusions", acquisition.get("held", 0)) or 0),
        "persisted_candidates": int(acquisition.get("persisted") or 0),
    }


def _receipt_snapshot_ids(acquisition: dict[str, Any] | None) -> set[str]:
    return {
        str(receipt.get("source_snapshot_id"))
        for receipt in ((acquisition or {}).get("receipts") or [])
        if receipt.get("write_status") == "SNAPSHOT_WRITE_SUCCEEDED" and receipt.get("source_snapshot_id")
    }


def _prop_handoff_reconciliation(
    *,
    acquisition: dict[str, Any] | None,
    canonical_manifest: list[dict[str, Any]],
    scored_rows: int,
    exclusions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    counts = _acquisition_counts(acquisition)
    canonical_ids = canonical_source_snapshot_ids(canonical_manifest)
    persisted_ids = _receipt_snapshot_ids(acquisition)
    excluded_ids = canonical_source_snapshot_ids(exclusions or []) - canonical_ids
    explicit_precanonical_exclusions = len(persisted_ids & excluded_ids)
    if persisted_ids:
        missing_persisted_ids = sorted(persisted_ids - canonical_ids - excluded_ids)
        canonical_from_current_acquisition = len(persisted_ids & canonical_ids)
    else:
        # Backward-compatible fallback for older acquisition payloads. The count
        # is still explicit, but identity-level proof requires receipts.
        missing_count = max(counts["persisted_candidates"] - len(canonical_manifest), 0)
        missing_persisted_ids = [f"UNRECEIPTED_PERSISTED_ROW_{index + 1}" for index in range(missing_count)]
        canonical_from_current_acquisition = min(counts["persisted_candidates"], len(canonical_manifest))
    prewrite_balanced = (
        counts["lane_discovered_raw"]
        == counts["lane_snapshot_write_succeeded"]
        + counts["lane_snapshot_write_failed"]
        + counts["explicit_prewrite_exclusions"]
    )
    persisted_balanced = counts["persisted_candidates"] == canonical_from_current_acquisition + explicit_precanonical_exclusions
    unscored = max(len(canonical_manifest) - scored_rows, 0)
    return {
        **counts,
        "canonical_rows": len(canonical_manifest),
        "canonical_rows_from_current_acquisition": canonical_from_current_acquisition,
        "explicit_precanonical_exclusions": explicit_precanonical_exclusions,
        "missing_persisted_snapshot_ids": missing_persisted_ids,
        "scored_rows": scored_rows,
        "explicitly_unscored_with_terminal_reason": unscored,
        "prewrite_balanced": prewrite_balanced,
        "persisted_to_canonical_balanced": persisted_balanced and not missing_persisted_ids,
        "canonical_to_scored_balanced": len(canonical_manifest) == scored_rows + unscored,
        "can_execute": False,
    }


def _lane_reconciliation(
    rows: list[dict[str, Any]],
    lane: str,
    blockers: list[str] | None = None,
    acquisition: dict[str, Any] | None = None,
    canonical_manifest: list[dict[str, Any]] | None = None,
    requested_limit: int | None = None,
    exclusions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    blockers = blockers or []
    lane_rows = [row for row in rows if row.get("lane") == lane]
    canonical_count = len(canonical_manifest or []) if lane == "PROPS" else len(lane_rows)
    source_instance_count = (
        sum(int(row.get("source_instance_count") or 1) for row in (canonical_manifest or []))
        if lane == "PROPS"
        else canonical_count
    )
    zero_row_reason = None
    if not lane_rows:
        expected = "PROP_SNAPSHOT_QUERY_FAILED" if lane == "PROPS" else "TEAM_EVENT_SNAPSHOT_QUERY_FAILED"
        if expected in " ".join(blockers):
            zero_row_reason = "DISCOVERY_DATA_UNOBTAINABLE"
        elif lane == "PROPS" and acquisition and acquisition.get("status") == "DATA_UNOBTAINABLE":
            zero_row_reason = "DISCOVERY_DATA_UNOBTAINABLE"
        elif lane == "PROPS" and "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE" in blockers:
            zero_row_reason = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
        elif lane == "PROPS" and "PROP_SNAPSHOT_INGESTION_EMPTY" in blockers:
            zero_row_reason = "PROP_SNAPSHOT_INGESTION_EMPTY"
        elif requested_limit == 0:
            # Zero rows caused by the request itself are never evidence that the
            # canonical lane is empty. Reporting NO_CANONICAL_CANDIDATES here
            # misattributes a client-requested bound to missing upstream data.
            zero_row_reason = "REQUESTED_ROW_LIMIT_ZERO"
        elif any(row.get("reason") == "EVIDENCE_BLOCKED" for row in (exclusions or [])):
            zero_row_reason = "ALL_CANONICAL_CANDIDATES_EXCLUDED"
        else:
            zero_row_reason = "NO_CANONICAL_CANDIDATES"
    result = {
        "discovered_count": source_instance_count,
        "canonicalized_count": canonical_count,
        "duplicate_source_instance_count": max(source_instance_count - canonical_count, 0),
        "requested_row_limit": requested_limit,
        "scored_count": len(lane_rows),
        "completed_count": sum(1 for row in lane_rows if row.get("row_status") == "COMPLETED"),
        "held_count": sum(1 for row in lane_rows if row.get("row_status") == "HELD"),
        "rejected_count": sum(1 for row in lane_rows if row.get("row_status") == "REJECTED"),
        "zero_row_reason": zero_row_reason,
        "acquisition": acquisition if lane == "PROPS" else None,
    }
    if lane == "PROPS":
        result["handoff_reconciliation"] = _prop_handoff_reconciliation(
            acquisition=acquisition,
            canonical_manifest=canonical_manifest or [],
            scored_rows=len(lane_rows),
            exclusions=exclusions,
        )
    return result


def _settlement_basis(sport: str) -> str:
    """Governed settlement identity per sport.  Never a generic default."""
    return {
        "MLB": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "NFL": "FULL_GAME_INCLUDING_OVERTIME",
        "NCAAF": "FULL_GAME_INCLUDING_OVERTIME",
        "NBA": "FULL_GAME_INCLUDING_OVERTIME",
        "WNBA": "FULL_GAME_INCLUDING_OVERTIME",
        "NCAAB": "FULL_GAME_INCLUDING_OVERTIME",
        "NHL": "FULL_GAME_INCLUDING_OVERTIME_AND_SHOOTOUT",
        "SOCCER": "FULL_TIME_1X2_EXCLUDING_EXTRA_TIME",
        "TENNIS": "MATCH_WINNER_INCLUDING_RETIREMENT_RULES",
        "PGA": "TOURNAMENT_OR_HEAD_TO_HEAD_SETTLEMENT",
        "MMA": "FIGHT_WINNER_INCLUDING_DRAW_AND_NO_CONTEST",
        "BOXING": "FIGHT_WINNER_INCLUDING_DRAW_AND_NO_CONTEST",
    }.get(sport, "FULL_GAME_OUTRIGHT")


def _cross_sport_stage_status(row: dict[str, Any]) -> str:
    """Native Daily stage terminal for one discovered cross-sport row.

    ``bucket`` records the model outcome and is set before the full-board
    overlay runs. The overlay may later withdraw ``rank_eligible`` when the
    publication-chain audit proves a mandatory stage unproven; a row without
    rank eligibility is not an approved stage. Pre-overlay, MODEL_COMPLETED
    already implies rank_eligible, so this only ever lowers a row to HELD.
    """
    completed = row.get("bucket") == discovery.MODEL_COMPLETED
    return "COMPLETED" if completed and row.get("rank_eligible") is True else "HELD"


def _cross_sport_moneyline_rows(
    req: DailySnapshotRequest,
    *,
    run_id: str,
    event_api: Any,
    covered_event_ids: set[str],
    fetch_sport_events: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Discover the full cross-sport winner slate, then ask the registry per row.

    MLB's certified canonical-snapshot path above is untouched. This lane exists
    so the board stops *being* MLB whenever MLB is the only healthy bridge: every
    supported sport is discovered, rows whose sport has no registered model are
    retained with a typed MODEL_UNAVAILABLE, and the whole discovered slate
    reconciles.
    """
    if not discovery_feed.enabled():
        return None

    feed = fetch_sport_events
    if feed is None:
        feed = discovery_feed.union_feed(
            discovery_feed.odds_proxy_feed(),
            discovery_feed.rundown_board_feed(slate_date=req.requested_slate_date),
        )

    def resolve_model(event: discovery.DiscoveredEvent) -> Any:
        return bridge_runtime.TEAM_EVENT_BRIDGES.get(event.sport)

    def score(event: discovery.DiscoveredEvent, _model: Any) -> dict[str, Any]:
        if str(event.official_event_id or "") in covered_event_ids:
            return {
                "code": "ALREADY_SCORED_IN_CANONICAL_LANE",
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        try:
            request = TeamEventRequest(
                requester_host_identity="WOW_BETTING_ENGINE",
                research_run_id=run_id,
                requested_slate_date=req.requested_slate_date,
                requested_timezone=req.requested_timezone,
                candidate_family="OUTRIGHT_WINNER",
                decision_intent="BEST_SIDE",
                event_key=event.event_key,
                official_event_id=str(event.official_event_id),
                event_start_time_utc=str(event.commence_time_utc),
                sport=event.sport,
                league=event.league or event.sport,
                settlement_basis=_settlement_basis(event.sport),
                home_team=str(event.home_team),
                away_team=str(event.away_team),
                source_snapshot_id=f"discovery:{event.sport_key}:{event.official_event_id}",
            )
        except Exception as exc:  # noqa: BLE001 - a malformed row is inputs-insufficient
            return {
                "code": "MODEL_INPUTS_INSUFFICIENT",
                "blockers": ["TEAM_EVENT_REQUEST_CONTRACT_INVALID"],
                "error_type": type(exc).__name__,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        try:
            # The registered bridge owns its own canonical acquisition, so a
            # discovered row that cannot be hydrated terminates as
            # MODEL_INPUTS_INSUFFICIENT rather than as a missing model.
            return score_team_event_request(
                request, event_api=event_api, canonical_hydration_required=True
            )
        except HTTPException as exc:
            return _detail(exc)
        except Exception as exc:  # noqa: BLE001 - an invoked scorer that throws
            return {
                "code": "MODEL_SCORER_FAILED",
                "error_type": type(exc).__name__,
                "model_invoked": True,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }

    scan = discovery.run_cross_sport_winner_scan(
        requested_slate_date=req.requested_slate_date,
        requested_timezone=req.requested_timezone,
        fetch_sport_events=feed,
        resolve_model=resolve_model,
        score_row=score,
        # "Across all sports" must inventory active regime variants too.
        # Without this, September discovery silently omitted NHL preseason
        # (and analogous NFL/NBA/MLB variants) and could make the slate appear
        # MLB-only even though the provider registry contained those events.
        include_regime_variants=True,
    )
    rows = [
        _terminal_row(
            "MONEYLINE",
            {
                key: row.get(key)
                for key in ("official_event_id", "commence_time_utc", "home_team", "away_team", "sport")
            },
            row,
            assert_no_terminal_upgrade(
                reduce_row_terminal([_cross_sport_stage_status(row)])
            ),
        )
        for row in scan["rows"]
        if str(row.get("official_event_id") or "") not in covered_event_ids
    ]
    return rows, scan


SPREAD_LANE = "SPREAD"


def load_certified_spread_receipt(
    db: Any, *, sport: str, event_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Read the newest governed spread receipt for one event, or a reason code.

    The registry module owns the relation name and the read contract so the
    writer and the Daily lane cannot drift apart.
    """
    from v17.spread_receipt_registry import read_certified_spread_receipt

    return read_certified_spread_receipt(db, sport=sport, event_id=event_id)


def _admit_certified_spread_receipt(
    receipt: dict[str, Any],
    *,
    event_id: str,
    sport: str,
    home_line: float,
    settlement_basis: str | None,
) -> tuple[dict[str, Any], bool, list[str]]:
    """Admit one candidate receipt against the ledger-bound exact line.

    The receipt's own declared side and line are the thesis; the ledger line is
    the market truth it must match. An away side is the definitional opposite of
    the quoted home line, never an independently invented number.
    """
    from v17.spread_card_admission import spread_card_admission

    blockers: list[str] = []
    side = str(
        receipt.get("exact_line_side") or receipt.get("spread_side") or ""
    ).strip().upper()
    if str(receipt.get("sport") or "").strip().upper() != sport:
        blockers.append("SPREAD_RECEIPT_SPORT_MISMATCH")
    if not settlement_basis or str(receipt.get("settlement_basis") or "") != settlement_basis:
        blockers.append("SPREAD_RECEIPT_SETTLEMENT_BASIS_MISMATCH")
    expected_line = float(home_line) if side != "AWAY" else -float(home_line)
    admission = spread_card_admission(
        receipt,
        requested_event_id=event_id,
        requested_side=side,
        requested_signed_spread=expected_line,
    )
    blockers.extend(admission["blockers"])
    eligible = admission["card_admission_eligible"] is True and not blockers
    return admission, eligible, list(dict.fromkeys(blockers))


def _spread_lane_rows(
    db: Any,
    *,
    moneyline_rows: list[dict[str, Any]],
    requested_limit: int,
    receipt_provider: Any = None,
) -> list[dict[str, Any]]:
    """One explicit research row per event identity already proven on the slate.

    The lane binds the append-only ledger's exact pregame quote and nothing else.
    It never fits or invokes a model, never invents a side, line or probability,
    and never writes market evidence. A row completes only when an already-
    governed receipt passes ``spread_card_admission`` against that exact line;
    otherwise it terminates HELD with typed reason codes so the event stays
    visible instead of being silently omitted.
    """
    from v17.spread_forward_auto_canary import try_bind_exact_line

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in moneyline_rows:
        if source.get("lane") != "MONEYLINE" or len(rows) >= requested_limit:
            continue
        identity = source.get("identity") or {}
        event_id = str(identity.get("official_event_id") or "").strip()
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        result = source.get("result") or {}
        sport = str(
            identity.get("sport") or result.get("sport") or result.get("league") or ""
        ).strip().upper()
        start = identity.get("event_start_time") or identity.get("commence_time_utc")
        lookup = try_bind_exact_line(
            db,
            sport=sport,
            event={
                "raw_event_id": event_id,
                "event_start_time": start,
                "home_team": identity.get("home_team"),
                "away_team": identity.get("away_team"),
            },
        )
        evidence = lookup["evidence"]
        provider = receipt_provider or load_certified_spread_receipt
        receipt: dict[str, Any] | None = None
        receipt_reason: str | None = None
        if evidence is not None:
            try:
                receipt, receipt_reason = provider(
                    db, sport=sport, event_id=event_id
                )
            except Exception as exc:  # noqa: BLE001 - typed registry boundary
                receipt, receipt_reason = None, (
                    f"SPREAD_RECEIPT_REGISTRY_UNAVAILABLE:{type(exc).__name__}"
                )
        payload: dict[str, Any] = {
            "sport": sport or None,
            "market_family": "POINT_SPREAD",
            "settlement_basis": _settlement_basis(sport) if sport else None,
            "selection_status": "HELD",
            "evaluation_state": "SPREAD_MODEL_UNCERTIFIED",
            "reason_codes": list(lookup["reason_codes"])
            or ["SPREAD_SELECTION_REQUIRES_CERTIFIED_MODEL"],
            "line_source": None,
            "exact_line_side": None,
            "exact_signed_spread": None,
            "sportsbook": None,
            "quote_timestamp": None,
            "provider_event_id": None,
            "market_evidence_observed_rows": lookup["observed_rows"],
            # No probability is produced here, so none may be reported.
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
        if evidence is not None:
            payload.update({
                "line_source": "APPEND_ONLY_MARKET_LEDGER",
                "exact_line_side": "HOME",
                "exact_signed_spread": float(evidence.home_spread),
                "home_spread": float(evidence.home_spread),
                "sportsbook": str(evidence.sportsbook),
                "quote_timestamp": str(evidence.quote_timestamp),
                "provider_event_id": str(evidence.provider_event_id),
            })
        status = "HELD"
        if evidence is not None and receipt is not None:
            admission, eligible, blockers = _admit_certified_spread_receipt(
                receipt,
                event_id=event_id,
                sport=sport,
                home_line=float(evidence.home_spread),
                settlement_basis=payload["settlement_basis"],
            )
            payload["spread_admission"] = admission
            if eligible:
                status = "COMPLETED"
                payload.update({
                    "exact_line_side": admission["exact_line_side"],
                    "exact_signed_spread": admission["exact_signed_spread"],
                    "source_receipt_id": receipt.get("source_receipt_id"),
                    "controlling_spread_specialist": admission["controlling_spread_specialist"],
                    "model_artifact_version": admission["model_artifact_version"],
                    "model_class": receipt.get("model_class"),
                    "model_version": (
                        receipt.get("model_version")
                        or receipt.get("model_family")
                        or admission["model_artifact_version"]
                    ),
                    "calibration_version": receipt.get("calibration_version"),
                    "model_timestamp": admission["model_timestamp"],
                    "source_snapshot_timestamp": receipt.get("source_snapshot_timestamp"),
                    "terminal_authority": (
                        receipt.get("terminal_authority")
                        or receipt.get("global_terminal_authority")
                    ),
                    "controlling_engine_identity": receipt.get("controlling_engine_identity"),
                    "p_cover": admission["p_cover"],
                    "p_push": admission["p_push"],
                    "p_not_cover": admission["p_not_cover"],
                    "calibrated_probability_lower_bound": receipt.get(
                        "calibrated_lower_bound",
                        receipt.get("calibrated_probability_lower_bound"),
                    ),
                    "calibrated_probability_upper_bound": receipt.get(
                        "calibrated_upper_bound",
                        receipt.get("calibrated_probability_upper_bound"),
                    ),
                    "selection_status": "QUALIFIED",
                    "reason_codes": [],
                    "probability_publishable": True,
                    "rank_eligible": True,
                })
                if admission["exact_line_side"] == "HOME":
                    payload["home_spread"] = admission["exact_signed_spread"]
            else:
                payload["selection_status"] = "HELD"
                payload["reason_codes"] = blockers
        elif evidence is not None and receipt_reason:
            payload["reason_codes"] = [receipt_reason]
        rows.append(
            _terminal_row(
                SPREAD_LANE,
                {
                    "official_event_id": event_id,
                    "event_start_time": start,
                    "home_team": identity.get("home_team"),
                    "away_team": identity.get("away_team"),
                    "sport": sport or None,
                },
                payload,
                assert_no_terminal_upgrade(reduce_row_terminal([status])),
            )
        )
    return rows


def _guard_moneyline_result(result: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Apply official-publication proof without erasing the sporting output."""
    guard = evaluate_team_event_official_publication(result)
    guarded = {
        **result,
        "official_publication_guard": guard,
        "prepublication_claim": {
            "probability_publishable": result.get("probability_publishable"),
            "rank_eligible": result.get("rank_eligible"),
        },
    }
    if guard["official_publication_allowed"] is not True:
        # Depublication is a publication control, not a terminal. If the inner
        # model already decided a hard rejection, that terminal is preserved
        # rather than being softened into a hold by the guard.
        inner_status = classify_stage_status(result)
        guarded["probability_publishable"] = False
        guarded["rank_eligible"] = False
        return guarded, inner_status if inner_status in ("REJECTED", "PURGED") else "HELD"
    return guarded, "COMPLETED"


def _run_daily_snapshot_impl(req: DailySnapshotRequest, *, db: Any, market_api: Any, event_api: Any) -> dict[str, Any]:
    try:
        date.fromisoformat(req.requested_slate_date)
    except ValueError:
        return {"run_id": f"v17-daily-{uuid4()}", "terminal": True, "run_status": "RUN_INVALID_REQUEST", "rows": [], "blockers": ["REQUESTED_SLATE_DATE_INVALID"], "can_execute": False}

    run_id = f"v17-daily-{uuid4()}"
    # Server-generated, non-secret provenance; never trust a client-supplied identity.
    # The same context is persisted with every detail row, so two concurrent
    # runs of the same event inventory can be reconciled by requested local date.
    request_context = {
        "request_trace_id": str(uuid4()),
        "requested_slate_date": req.requested_slate_date,
        "requested_timezone": req.requested_timezone,
        "caller_class": (
            "INTERNAL_AUTOMATION" if type(req).__name__ == "InternalDailySnapshotRequest"
            else "V17_DAILY_ACTION"
        ),
        "accepted_at_utc": datetime.now(timezone.utc).isoformat(),
        "can_execute": False,
    }
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    requested_lanes = set(req.lanes)
    prop_acquisition: dict[str, Any] | None = None
    prop_manifest: list[dict[str, Any]] = []
    prop_exclusions: list[dict[str, Any]] = []
    prop_read_stats: dict[str, Any] = {}
    prop_candidate_accounting: dict[str, Any] | None = None

    if "PROPS" in requested_lanes:
        try:
            prop_manifest = _prop_manifest_rows(
                db, req.requested_slate_date, req.requested_timezone,
                exclusions=prop_exclusions, stats=prop_read_stats,
            )
            prop_rows = prop_manifest[:req.max_props]
        except Exception as exc:
            prop_rows = []
            prop_manifest = []
            blockers.append(f"PROP_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")
        # Acquisition exists to fill an empty canonical lane. A zero-length page
        # caused by max_props is not an empty lane, so it must not trigger a
        # producer run (and must not later be typed NO_CANONICAL_CANDIDATES).
        if not prop_manifest and not any(str(x).startswith("PROP_SNAPSHOT_QUERY_FAILED") for x in blockers):
            prop_acquisition = acquire_daily_prop_snapshots(
                db=db,
                requested_date=req.requested_slate_date,
                requested_timezone=req.requested_timezone,
                max_candidates=max(req.max_props * 3, req.max_props),
            )
            if prop_acquisition.get("status") == "DATA_UNOBTAINABLE":
                blockers.extend(prop_acquisition.get("blockers") or [])
            elif prop_acquisition.get("status") == "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE":
                blockers.append("RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE")
                blockers.extend(prop_acquisition.get("blockers") or [])
            try:
                prop_manifest = _prop_manifest_rows(
                    db, req.requested_slate_date, req.requested_timezone,
                    exclusions=prop_exclusions, stats=prop_read_stats,
                    retain_exclusion_snapshot_ids=_receipt_snapshot_ids(prop_acquisition),
                )
                prop_rows = prop_manifest[:req.max_props]
            except Exception as exc:
                prop_rows = []
                prop_manifest = []
                blockers.append(f"PROP_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")

            persisted = int(prop_acquisition.get("persisted") or 0)
            if persisted > 0 and not prop_manifest:
                handoff = _prop_handoff_reconciliation(
                    acquisition=prop_acquisition, canonical_manifest=prop_manifest,
                    scored_rows=0, exclusions=prop_exclusions,
                )
                if not (
                    prop_read_stats.get("read_complete") is True
                    and handoff["persisted_to_canonical_balanced"] is True
                ):
                    blockers.append("PROP_SNAPSHOT_INGESTION_EMPTY")

        for row in prop_rows:
            identity = {key: row.get(key) for key in ("event_id", "event_start_time", "sport", "player", "stat_type", "line", "source_snapshot_id")}
            outcomes: list[dict[str, Any]] = []
            for direction in ("MORE", "LESS"):
                try:
                    scored = market_api.score_prop(market_api.ScorePropRequest(**{**identity, "direction": direction}), "WOW_BETTING_ENGINE")
                    outcomes.append({"direction": direction, "status": classify_stage_status(scored), "payload": scored})
                except HTTPException as exc:
                    # A typed rejection returned as an error still carries the
                    # controlling model's terminal; it is classified, not assumed held.
                    detail = _detail(exc)
                    outcomes.append({"direction": direction, "status": classify_stage_status(detail), "payload": detail})
                except Exception as exc:
                    outcomes.append({"direction": direction, "status": "HELD", "payload": {"code": "PROP_SCORER_EXCEPTION", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}})
            publishable = any(x["payload"].get("probability_publishable") is True and x["payload"].get("rank_eligible") is True for x in outcomes)
            rows.append(
                _terminal_row(
                    "PROPS",
                    identity,
                    {"outcomes": outcomes, "probability_publishable": publishable, "rank_eligible": publishable, "can_execute": False},
                    _props_row_reduction(outcomes),
                )
            )

    if "PROPS" in requested_lanes:
        scored_prop_rows = sum(1 for row in rows if row.get("lane") == "PROPS")
        eligible_not_scored = max(len(prop_manifest) - len(prop_rows), 0)
        reason_counts = dict(prop_read_stats.get("excluded_by_reason") or {})
        excluded_rows = int(prop_read_stats.get("excluded_rows") or 0)
        if eligible_not_scored:
            reason_counts["MAX_PROPS_LIMIT"] = (
                reason_counts.get("MAX_PROPS_LIMIT", 0) + eligible_not_scored
            )
        seen = int(prop_read_stats.get("raw_rows_read") or 0)
        merged_rows = int(prop_read_stats.get("canonical_merged_rows") or 0)
        accounted = scored_prop_rows + eligible_not_scored + excluded_rows + merged_rows
        accounting_balanced = prop_read_stats.get("read_complete") is True and accounted == seen
        prop_candidate_accounting = {
            "manifest_rows_seen": seen,
            "raw_rows_read": seen,
            "canonical_merged_rows": merged_rows,
            "read_complete": prop_read_stats.get("read_complete") is True,
            "eligible_rows": len(prop_manifest),
            "scored_rows": scored_prop_rows,
            "eligible_not_scored_rows": eligible_not_scored,
            "excluded_rows": excluded_rows,
            "accounted_rows": accounted,
            "excluded_by_reason": reason_counts,
            "exclusions": prop_exclusions[:50],
            "exclusions_truncated": max(excluded_rows - 50, 0),
            "balanced": accounting_balanced,
            "can_execute": False,
        }
        if not accounting_balanced:
            blockers.append("RUN_INVALID_PROP_CANDIDATE_ACCOUNTING")

    cross_sport_audit: dict[str, Any] | None = None
    if "MONEYLINE" in requested_lanes:
        try:
            event_rows = _team_rows(db, req.requested_slate_date, req.max_team_events)
        except Exception as exc:
            event_rows = []
            blockers.append(f"TEAM_EVENT_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")
        for event in event_rows:
            identity = {key: event.get(key) for key in ("official_event_id", "event_start_time", "home_team", "away_team", "snapshot_id")}
            request = TeamEventRequest(requester_host_identity="WOW_BETTING_ENGINE", research_run_id=run_id, requested_slate_date=req.requested_slate_date, requested_timezone=req.requested_timezone, candidate_family="OUTRIGHT_WINNER", decision_intent="BEST_SIDE", event_key=f"MLB:{event['official_event_id']}", official_event_id=str(event["official_event_id"]), event_start_time_utc=event["event_start_time"], sport="MLB", league="MLB", settlement_basis="FULL_GAME_INCLUDING_EXTRA_INNINGS", home_team=event["home_team"], away_team=event["away_team"], source_snapshot_id=str(event["snapshot_id"]), latest_material_update_timestamp=event.get("snapshot_timestamp"), sport_specific_evidence={"venue": event.get("venue_name"), "home_starting_pitcher": event.get("home_probable_pitcher"), "away_starting_pitcher": event.get("away_probable_pitcher"), "home_starter_status": "PROBABLE", "away_starter_status": "PROBABLE", "home_lineup_status": "PROJECTED", "away_lineup_status": "PROJECTED"})
            try:
                result = score_team_event_request(request, event_api=event_api, canonical_hydration_required=True)
            except HTTPException as exc:
                result = _detail(exc)
            except Exception as exc:
                result = {"code": "TEAM_EVENT_SCORER_EXCEPTION", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}
            result, stage_status = _guard_moneyline_result(result)
            reduction = assert_no_terminal_upgrade(reduce_row_terminal([stage_status]))
            rows.append(_terminal_row("MONEYLINE", identity, result, reduction))

        # Broad discovery runs after — never instead of — the certified MLB
        # canonical lane, and never filtered by which sports have a model.
        covered = {str(event.get("official_event_id") or "") for event in event_rows}
        try:
            produced = _cross_sport_moneyline_rows(
                req, run_id=run_id, event_api=event_api, covered_event_ids=covered
            )
        except Exception as exc:  # noqa: BLE001 - discovery defects must not void the lane
            produced = None
            blockers.append(f"CROSS_SPORT_DISCOVERY_FAILED:{type(exc).__name__}")
        if produced is not None:
            cross_sport_rows, cross_sport_audit = produced
            cross_sport_audit = dict(cross_sport_audit)
            acquisition_details = [
                dict(detail)
                for detail in (cross_sport_audit.pop("acquisition_details", []) or [])
                if isinstance(detail, dict)
            ]
            expected_acquisition_targets = [
                dict(target)
                for target in (
                    cross_sport_audit.pop("expected_acquisition_targets", []) or []
                )
                if isinstance(target, dict)
            ]
            acquisition_persistence = persist_acquisition_detail(
                db,
                run_id=run_id,
                details=acquisition_details,
                expected_targets=expected_acquisition_targets,
            )
            acquisition_ref = acquisition_detail_reference(
                run_id=run_id,
                detail_available=bool(acquisition_persistence.get("detail_available")),
                details_count=int(acquisition_persistence.get("details_expected") or 0),
            )
            cross_sport_audit["acquisition_detail_persistence"] = acquisition_persistence
            cross_sport_audit["acquisition_detail_ref"] = acquisition_ref
            cross_sport_audit["acquisition_family_summary"] = summarize_acquisition_by_family(
                acquisition_details
            )
            reconciliation = dict(cross_sport_audit.get("reconciliation") or {})
            detail_accounted = acquisition_persistence.get("detail_available") is True and not (
                acquisition_persistence.get("blockers")
            )
            if acquisition_persistence.get("board_completeness") is True:
                reconciliation["acquisition_detail_reconciliation"] = "PASS"
                reconciliation["board_completeness"] = "PASS"
            elif detail_accounted:
                # #823: every target is accounted for, but some failed. Truthful
                # partial board; not a run blocker and no row is changed.
                reconciliation["acquisition_detail_reconciliation"] = "PASS"
                reconciliation["board_completeness"] = "INCOMPLETE"
            else:
                reconciliation["acquisition_detail_reconciliation"] = "BLOCKED"
                reconciliation["board_completeness"] = "BLOCKED"
                blockers.extend(acquisition_persistence.get("blockers") or [])
            reconciliation["can_execute"] = False
            cross_sport_audit["reconciliation"] = reconciliation
            rows.extend(cross_sport_rows)
            if cross_sport_audit["reconciliation"]["row_reconciliation"] != "PASS":
                blockers.append("RUN_INVALID_CROSS_SPORT_ROW_RECONCILIATION")

    if "SPREAD" in requested_lanes:
        if "MONEYLINE" not in requested_lanes:
            # The spread lane reuses the winner lane's proven event identities; it
            # never runs its own discovery, provider spend or model fit.
            blockers.append("SPREAD_LANE_REQUIRES_MONEYLINE_SLATE")
        else:
            rows.extend(
                _spread_lane_rows(
                    db,
                    moneyline_rows=list(rows),
                    requested_limit=req.max_team_events,
                )
            )

    prop_counts = _acquisition_counts(prop_acquisition)
    true_zero_upstream = (
        "PROPS" in requested_lanes
        and not prop_manifest
        and not (prop_read_stats.get("excluded_by_reason") or {}).get("EVIDENCE_BLOCKED")
        and prop_counts["lane_discovered_raw"] == 0
        and not any(str(x).startswith("PROP_SNAPSHOT_QUERY_FAILED") for x in blockers)
        and not any(x in blockers for x in ("PROP_SNAPSHOT_INGESTION_EMPTY", "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"))
        and not (prop_acquisition and prop_acquisition.get("status") == "DATA_UNOBTAINABLE")
    )
    if not rows and not blockers:
        if true_zero_upstream:
            blockers.append("NO_CANONICAL_PREGAME_SNAPSHOTS")
        elif "PROPS" not in requested_lanes:
            blockers.append("NO_CANONICAL_PREGAME_SNAPSHOTS")

    requested_limits = {
        "PROPS": req.max_props,
        "MONEYLINE": req.max_team_events,
        "SPREAD": req.max_team_events,
    }
    lane_reconciliation = {
        lane: _lane_reconciliation(
            rows,
            lane,
            blockers,
            prop_acquisition,
            prop_manifest if lane == "PROPS" else None,
            requested_limit=requested_limits.get(lane),
            exclusions=prop_exclusions if lane == "PROPS" else None,
        )
        for lane in requested_lanes
    }
    # Full per-row evidence is persisted before the response is compacted, so
    # compact transport relocates evidence rather than discarding it.
    detail_persistence = persist_row_detail(
        db, run_id=run_id, rows=rows, request_context=request_context
    )
    blockers.extend(detail_persistence.get("blockers") or [])

    run_status = "COMPLETED"
    if "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE" in blockers:
        run_status = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif blockers:
        run_status = "COMPLETED_WITH_ACQUISITION_BLOCKERS"
    response = {
        "run_id": run_id,
        "terminal": True,
        "run_status": run_status,
        "requested_slate_date": req.requested_slate_date,
        "requested_timezone": req.requested_timezone,
        "request_trace_id": request_context["request_trace_id"],
        "caller_class": request_context["caller_class"],
        "requested_lanes": sorted(requested_lanes),
        "rows": rows,
        "reconciliation": _reconcile(rows),
        "lane_reconciliation": lane_reconciliation,
        "cross_sport_discovery_audit": cross_sport_audit,
        "prop_acquisition": prop_acquisition,
        "prop_candidate_accounting": prop_candidate_accounting,
        "row_detail_persistence": detail_persistence,
        "blockers": list(dict.fromkeys(blockers)),
        "can_execute": False,
    }
    if req.response_mode == "FULL":
        response["response_mode"] = "FULL"
        return response
    return compact_response(response, detail_available=bool(detail_persistence.get("detail_available")))


def run_daily_snapshot(
    req: DailySnapshotRequest,
    *,
    db: Any,
    market_api: Any,
    event_api: Any,
    _heavy_permit: memory_admission.HeavyJobPermit | None = None,
) -> dict[str, Any]:
    """Run one Daily slate inside the shared bounded heavyweight slot."""
    owned_permit: memory_admission.HeavyJobPermit | None = None
    if _heavy_permit is None:
        owned_permit = memory_admission.acquire_heavy_job("DAILY_SNAPSHOT")

    token = source_policy.begin_paid_budget_scope()
    try:
        return _run_daily_snapshot_impl(
            req,
            db=db,
            market_api=market_api,
            event_api=event_api,
        )
    finally:
        source_policy.end_paid_budget_scope(token)
        if owned_permit is not None:
            owned_permit.release()


_ACQUISITION_LOGGER = logging.getLogger("wow.v17.prop_evidence_acquisition")
_ACQUISITION_STATE_KEY = "wow_prop_evidence_acquisition_scheduler_installed"


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _install_prop_evidence_acquisition_scheduler(app: FastAPI, *, db_client_fn: Any) -> None:
    """Give prop evidence acquisition an autonomous pass.

    Off by default: enabling it is an explicit production decision, exactly like
    the forward cohort loop it feeds. It seeds the same snapshots the
    authenticated daily route already produces and changes no scoring,
    publication, or calibration semantics.
    """
    if os.getenv("WOW_PROP_EVIDENCE_ACQUISITION_ENABLED", "0") != "1":
        return
    if getattr(app.state, _ACQUISITION_STATE_KEY, False):
        return
    setattr(app.state, _ACQUISITION_STATE_KEY, True)

    interval_seconds = _int_env(
        "WOW_PROP_EVIDENCE_ACQUISITION_INTERVAL_SECONDS", 3600, minimum=300, maximum=86400
    )
    max_candidates = _int_env(
        "WOW_PROP_EVIDENCE_ACQUISITION_MAX_CANDIDATES", 60, minimum=1, maximum=200
    )
    forward_days = _int_env("WOW_PROP_EVIDENCE_ACQUISITION_FORWARD_DAYS", 2, minimum=1, maximum=3)
    initial_delay_seconds = _int_env(
        "WOW_PROP_EVIDENCE_ACQUISITION_INITIAL_DELAY_SECONDS", 15, minimum=0, maximum=300
    )
    timezone_name = os.getenv("WOW_PROP_EVIDENCE_ACQUISITION_TIMEZONE", "America/Chicago").strip() or "America/Chicago"

    @app.on_event("startup")
    async def schedule_prop_evidence_acquisition() -> None:
        task = asyncio.create_task(
            run_prop_evidence_acquisition_loop(
                db_client_fn=db_client_fn,
                logger=_ACQUISITION_LOGGER,
                interval_seconds=interval_seconds,
                max_candidates=max_candidates,
                forward_days=forward_days,
                timezone_name=timezone_name,
                initial_delay_seconds=initial_delay_seconds,
            )
        )
        tasks = getattr(app.state, "wow_prop_evidence_acquisition_tasks", None)
        if tasks is None:
            tasks = set()
            app.state.wow_prop_evidence_acquisition_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def install_daily_snapshot_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any, market_api: Any, event_api: Any) -> None:
    install_v17_detailed_evidence(app, auth_dependency=auth_dependency, market_api=market_api)
    install_prop_forward_cohort_route(app, auth_dependency=auth_dependency, db_client_fn=db_client_fn, market_api=market_api)
    _install_prop_evidence_acquisition_scheduler(app, db_client_fn=db_client_fn)
    # Additive LLP read-only readback is OFF unless explicitly enabled. It
    # consumes durable Daily row details; it never calls the NFL Pick'em runtime,
    # changes the existing /rows contract, or performs any scoring.
    if os.getenv("WOW_LLP_TEAM_INTELLIGENCE_READBACK_ENABLED", "0") == "1":
        from v17.llp_team_intelligence_readback import install_llp_team_intelligence_readback

        install_llp_team_intelligence_readback(
            app, auth_dependency=auth_dependency, db_client_fn=db_client_fn
        )
    if any(getattr(route, "path", None) == "/v17/daily-snapshot-run" for route in app.router.routes):
        return

    @app.post("/v17/daily-snapshot-run", dependencies=[auth_dependency], operation_id="runWowV17DailySnapshot")
    def daily_snapshot_run(req: DailySnapshotRequest):
        try:
            return run_daily_snapshot(
                req,
                db=db_client_fn(),
                market_api=market_api,
                event_api=event_api,
            )
        except memory_admission.HeavyJobDeferred as exc:
            raise HTTPException(status_code=503, detail=exc.receipt()) from exc

    @app.get(
        "/v17/daily-snapshot-run/{run_id}/rows",
        dependencies=[auth_dependency],
        operation_id="readWowV17DailySnapshotRowDetail",
    )
    def daily_snapshot_row_detail(
        run_id: str,
        offset: int = 0,
        limit: int = DETAIL_PAGE_DEFAULT_LIMIT,
    ):
        """Page the full per-row evidence a compact Daily response points at."""
        return read_row_detail_page(
            db_client_fn(),
            run_id=run_id,
            offset=offset,
            limit=min(limit, DETAIL_PAGE_MAX_LIMIT),
        )

    @app.get(
        "/v17/daily-snapshot-run/{run_id}/acquisition-details",
        dependencies=[auth_dependency],
        operation_id="readWowV17DailySnapshotAcquisitionDetail",
    )
    def daily_snapshot_acquisition_detail(
        run_id: str,
        offset: int = 0,
        limit: int = ACQUISITION_DETAIL_PAGE_DEFAULT_LIMIT,
    ):
        """Page sanitized target-level acquisition outcomes for one Daily run."""
        return read_acquisition_detail_page(
            db_client_fn(),
            run_id=run_id,
            offset=offset,
            limit=min(limit, ACQUISITION_DETAIL_PAGE_MAX_LIMIT),
        )
