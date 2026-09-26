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
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from v17 import cross_sport_discovery_feed as discovery_feed
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
)
from v17.daily_terminal_reduction import (
    assert_no_terminal_upgrade,
    classify_stage_status,
    reduce_row_terminal,
)
from v17.detailed_evidence_install import install_v17_detailed_evidence
from v17.prop_evidence_acquisition_scheduler import run_prop_evidence_acquisition_loop
from v17.prop_forward_cohort_route import install_prop_forward_cohort_route
from v17.prop_canonical_identity import canonical_source_snapshot_ids, canonicalize_prop_manifest
from v17.team_event_official_publication_guard import evaluate_team_event_official_publication
from v17.team_event_request_runtime import TeamEventRequest, score_team_event_request


class DailySnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_slate_date: str
    requested_timezone: str = Field(min_length=1, max_length=64)
    lanes: list[Literal["PROPS", "MONEYLINE"]] = Field(default_factory=lambda: ["PROPS", "MONEYLINE"])
    max_props: int = Field(default=6, ge=0, le=12)
    max_team_events: int = Field(default=6, ge=0, le=12)
    # COMPACT is the normal client contract: full per-row evidence is persisted
    # and read back through the paged retrieval route instead of being inlined.
    response_mode: Literal["COMPACT", "FULL"] = "COMPACT"


def _detail(exc: HTTPException) -> dict[str, Any]:
    return dict(exc.detail) if isinstance(exc.detail, dict) else {"code": "HTTP_EXCEPTION", "message": str(exc.detail)}


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
    return {
        "lane": lane,
        "identity": identity,
        "result": payload,
        "terminal": True,
        "row_status": row_status,
        "terminal_reduction": reduction,
        "probability_publishable": publishable,
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


def _prop_manifest_rows(
    db: Any,
    requested_date: str,
    requested_timezone: str,
    *,
    page_size: int = 250,
) -> list[dict[str, Any]]:
    """Read the complete canonical slate manifest without a first-page truncation."""
    selected = "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,stat_type,line,hydration_status,blockers"
    manifest: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = db.table("wow_prop_evidence_snapshots").select(selected).eq("hydration_status", "PASS").order("event_start_time")
        ranged = getattr(query, "range", None)
        if callable(ranged):
            batch = ranged(offset, offset + page_size - 1).execute().data or []
        else:
            # Compatibility for test doubles/clients without range(); one bounded
            # read preserves prior behavior while production Supabase uses range().
            batch = query.limit(page_size).execute().data or []
        batch = [dict(row) for row in batch]
        manifest.extend(
            row for row in batch
            if not row.get("blockers") and _future(row.get("event_start_time")) and _matching_slate(row, requested_date, requested_timezone)
        )
        if not callable(ranged) or len(batch) < page_size:
            break
        offset += page_size
    return canonicalize_prop_manifest(manifest)


def _prop_rows(db: Any, requested_date: str, requested_timezone: str, limit: int) -> list[dict[str, Any]]:
    return _prop_manifest_rows(db, requested_date, requested_timezone)[:limit]


def _team_rows(db: Any, requested_date: str, limit: int) -> list[dict[str, Any]]:
    rows = db.table("wow_mlb_forward_shadow_events").select("official_event_id,official_date,event_start_time,home_team,away_team,venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id,snapshot_timestamp,feature_hydration_status").eq("official_date", requested_date).eq("feature_hydration_status", "PASS").order("event_start_time").limit(limit).execute().data or []
    return [dict(row) for row in rows if _future(row.get("event_start_time"))]


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
) -> dict[str, Any]:
    counts = _acquisition_counts(acquisition)
    canonical_ids = canonical_source_snapshot_ids(canonical_manifest)
    persisted_ids = _receipt_snapshot_ids(acquisition)
    if persisted_ids:
        missing_persisted_ids = sorted(persisted_ids - canonical_ids)
        canonical_from_current_acquisition = len(persisted_ids & canonical_ids)
    else:
        # Backward-compatible fallback for older acquisition payloads. The count
        # is still explicit, but identity-level proof requires receipts.
        missing_count = max(counts["persisted_candidates"] - len(canonical_manifest), 0)
        missing_persisted_ids = [f"UNRECEIPTED_PERSISTED_ROW_{index + 1}" for index in range(missing_count)]
        canonical_from_current_acquisition = min(counts["persisted_candidates"], len(canonical_manifest))
    explicit_precanonical_exclusions = 0
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
                reduce_row_terminal(
                    ["COMPLETED" if row.get("bucket") == discovery.MODEL_COMPLETED else "HELD"]
                )
            ),
        )
        for row in scan["rows"]
        if str(row.get("official_event_id") or "") not in covered_event_ids
    ]
    return rows, scan


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


def run_daily_snapshot(req: DailySnapshotRequest, *, db: Any, market_api: Any, event_api: Any) -> dict[str, Any]:
    try:
        date.fromisoformat(req.requested_slate_date)
    except ValueError:
        return {"run_id": f"v17-daily-{uuid4()}", "terminal": True, "run_status": "RUN_INVALID_REQUEST", "rows": [], "blockers": ["REQUESTED_SLATE_DATE_INVALID"], "can_execute": False}

    run_id = f"v17-daily-{uuid4()}"
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    requested_lanes = set(req.lanes)
    prop_acquisition: dict[str, Any] | None = None
    prop_manifest: list[dict[str, Any]] = []

    if "PROPS" in requested_lanes:
        try:
            prop_manifest = _prop_manifest_rows(db, req.requested_slate_date, req.requested_timezone)
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
                prop_manifest = _prop_manifest_rows(db, req.requested_slate_date, req.requested_timezone)
                prop_rows = prop_manifest[:req.max_props]
            except Exception as exc:
                prop_rows = []
                prop_manifest = []
                blockers.append(f"PROP_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")

            persisted = int(prop_acquisition.get("persisted") or 0)
            if persisted > 0 and not prop_manifest:
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
            reconciliation = dict(cross_sport_audit.get("reconciliation") or {})
            if acquisition_persistence.get("board_completeness") is True:
                reconciliation["acquisition_detail_reconciliation"] = "PASS"
                reconciliation["board_completeness"] = "PASS"
            else:
                reconciliation["acquisition_detail_reconciliation"] = "BLOCKED"
                reconciliation["board_completeness"] = "BLOCKED"
                blockers.extend(acquisition_persistence.get("blockers") or [])
            reconciliation["can_execute"] = False
            cross_sport_audit["reconciliation"] = reconciliation
            rows.extend(cross_sport_rows)
            if cross_sport_audit["reconciliation"]["row_reconciliation"] != "PASS":
                blockers.append("RUN_INVALID_CROSS_SPORT_ROW_RECONCILIATION")

    prop_counts = _acquisition_counts(prop_acquisition)
    true_zero_upstream = (
        "PROPS" in requested_lanes
        and not prop_manifest
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

    requested_limits = {"PROPS": req.max_props, "MONEYLINE": req.max_team_events}
    lane_reconciliation = {
        lane: _lane_reconciliation(
            rows,
            lane,
            blockers,
            prop_acquisition,
            prop_manifest if lane == "PROPS" else None,
            requested_limit=requested_limits.get(lane),
        )
        for lane in requested_lanes
    }
    # Full per-row evidence is persisted before the response is compacted, so
    # compact transport relocates evidence rather than discarding it.
    detail_persistence = persist_row_detail(db, run_id=run_id, rows=rows)
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
        "requested_lanes": sorted(requested_lanes),
        "rows": rows,
        "reconciliation": _reconcile(rows),
        "lane_reconciliation": lane_reconciliation,
        "cross_sport_discovery_audit": cross_sport_audit,
        "prop_acquisition": prop_acquisition,
        "row_detail_persistence": detail_persistence,
        "blockers": list(dict.fromkeys(blockers)),
        "can_execute": False,
    }
    if req.response_mode == "FULL":
        response["response_mode"] = "FULL"
        return response
    return compact_response(response, detail_available=bool(detail_persistence.get("detail_available")))


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
    if any(getattr(route, "path", None) == "/v17/daily-snapshot-run" for route in app.router.routes):
        return

    @app.post("/v17/daily-snapshot-run", dependencies=[auth_dependency], operation_id="runWowV17DailySnapshot")
    def daily_snapshot_run(req: DailySnapshotRequest):
        return run_daily_snapshot(req, db=db_client_fn(), market_api=market_api, event_api=event_api)

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
