"""Exact postgame settlement for already-fitted V17 NFL prop routes.

This overlay adds settlement evidence only for the four NFL routes that already
have fitted specialists. It reuses the existing NFL evidence contract:

* ESPN NFL scoreboard identity proves the exact provider event and teams are final;
* the canonical nflverse weekly player-stat row used by the fitted NFL prop
  pipeline supplies the exact postgame stat value.

Settlement remains restricted to immutable forward-evidence predictions. Missing
or late canonical rows stay typed holds; they are never converted to zero, DNP,
HIT, or MISS. The overlay never changes model math, calibration, certification,
publication, ranking, promotion, registration, or execution authority.
``can_execute`` is always false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from typing import Any, Callable, Mapping

import httpx

import nfl_prop_auto_hydration as nfl_hydration
import v17.prop_exact_route_settlement as settlement
import v17.prop_exact_route_settlement_scope_guard as scope_guard
from v17.prop_universal_forward_evidence import route_key, route_token


NFL_SUPPORTED = frozenset({
    ("NFL", "PASSING_YARDS"),
    ("NFL", "RUSHING_YARDS"),
    ("NFL", "RECEIVING_YARDS"),
    ("NFL", "ANYTIME_TD"),
})
NFL_SETTLEMENT_SOURCE = "NFL_CANONICAL_NFLVERSE_PLAYER_STATS"
_PATCH_MARKER = "_wow_nfl_exact_prop_settlement_overlay"
_ORIGINAL_ROUTE_STATUS = settlement._route_status


def _route_status(key: tuple[str, str]) -> tuple[str, str | None, str | None]:
    if key in NFL_SUPPORTED:
        return settlement.SETTLEMENT_READY, NFL_SETTLEMENT_SOURCE, None
    return _ORIGINAL_ROUTE_STATUS(key)


def _espn_event_sides(event: Mapping[str, Any]) -> dict[str, str]:
    competitions = event.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1:
        return {}
    competition = competitions[0]
    competitors = competition.get("competitors") if isinstance(competition, Mapping) else None
    if not isinstance(competitors, list):
        return {}
    sides: dict[str, str] = {}
    for competitor in competitors:
        if not isinstance(competitor, Mapping):
            continue
        side = str(competitor.get("homeAway") or "").strip().lower()
        team = competitor.get("team") if isinstance(competitor.get("team"), Mapping) else {}
        abbreviation = str(team.get("abbreviation") or "").strip().upper()
        if side in {"home", "away"} and abbreviation:
            sides[side] = abbreviation
    return sides


def _nfl_event_finality(
    *,
    role: Mapping[str, Any],
    event_start: datetime,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    provider_event_id = str(role.get("event_id") or "").strip()
    expected_sides = {
        "home": str(role.get("provider_home_team") or "").strip().upper(),
        "away": str(role.get("provider_away_team") or "").strip().upper(),
    }
    if not provider_event_id:
        return {
            "status": "IDENTITY_UNRESOLVED",
            "blocker": "NFL_ESPN_EVENT_ID_REQUIRED",
            "can_execute": False,
        }
    if not all(expected_sides.values()) or expected_sides["home"] == expected_sides["away"]:
        return {
            "status": "IDENTITY_UNRESOLVED",
            "blocker": "NFL_PROVIDER_TEAM_IDENTITY_REQUIRED",
            "can_execute": False,
        }

    matches: dict[str, dict[str, Any]] = {}
    queried_dates: list[str] = []
    try:
        for offset in (-1, 0, 1):
            date_key = (event_start + timedelta(days=offset)).strftime("%Y%m%d")
            queried_dates.append(date_key)
            payload = settlement._request_json(
                nfl_hydration.ESPN_SCOREBOARD_URL,
                http_get=http_get,
                params={"dates": date_key, "limit": 100},
                headers={"User-Agent": "WOW-Research/1.0", "Accept": "application/json"},
            )
            for raw in payload.get("events", []):
                if not isinstance(raw, Mapping):
                    continue
                event_id = str(raw.get("id") or "").strip()
                if event_id == provider_event_id:
                    matches[event_id] = dict(raw)
    except Exception as exc:
        return {
            "status": "OFFICIAL_SOURCE_UNAVAILABLE",
            "blocker": "NFL_EVENT_STATUS_SOURCE_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "can_execute": False,
        }

    if len(matches) != 1:
        return {
            "status": "OFFICIAL_EVENT_ID_MISMATCH",
            "blocker": "NFL_PROVIDER_EVENT_ID_NOT_EXACTLY_RESOLVED",
            "provider_event_id": provider_event_id,
            "queried_dates": queried_dates,
            "match_n": len(matches),
            "can_execute": False,
        }

    event = matches[provider_event_id]
    observed_sides = _espn_event_sides(event)
    if observed_sides != expected_sides:
        return {
            "status": "OFFICIAL_EVENT_ID_MISMATCH",
            "blocker": "NFL_PROVIDER_EVENT_TEAM_IDENTITY_CONFLICT",
            "provider_event_id": provider_event_id,
            "expected_sides": expected_sides,
            "observed_sides": observed_sides,
            "can_execute": False,
        }

    status = event.get("status") if isinstance(event.get("status"), Mapping) else {}
    status_type = status.get("type") if isinstance(status.get("type"), Mapping) else {}
    if status_type.get("completed") is not True:
        return {
            "status": "NOT_FINAL",
            "official_state": status_type.get("state"),
            "provider_event_id": provider_event_id,
            "can_execute": False,
        }
    return {
        "status": "FINAL",
        "provider_event_id": provider_event_id,
        "provider_sides": observed_sides,
        "can_execute": False,
    }


def _nfl_row_name(row: Mapping[str, Any]) -> str:
    return str(row.get("player_display_name") or row.get("player_name") or "")


def _nfl_stat_value(
    row: Mapping[str, Any],
    stat_type: str,
) -> tuple[float | None, dict[str, float] | None]:
    if stat_type == "PASSING_YARDS":
        return settlement._finite_number(row.get("passing_yards")), None
    if stat_type == "RUSHING_YARDS":
        return settlement._finite_number(row.get("rushing_yards")), None
    if stat_type == "RECEIVING_YARDS":
        return settlement._finite_number(row.get("receiving_yards")), None
    if stat_type == "ANYTIME_TD":
        raw_components = {
            "rushing_tds": settlement._finite_number(row.get("rushing_tds")),
            "receiving_tds": settlement._finite_number(row.get("receiving_tds")),
            "special_teams_tds": settlement._finite_number(row.get("special_teams_tds")),
        }
        if any(value is None for value in raw_components.values()):
            return None, None
        components = {
            key: float(value)
            for key, value in raw_components.items()
            if value is not None
        }
        touchdown_count = sum(components.values())
        return (1.0 if touchdown_count > 0.0 else 0.0), components
    return None, None


def settle_nfl_scalar(
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    role = snapshot.get("role_status") if isinstance(snapshot.get("role_status"), Mapping) else {}
    event_start = settlement._aware(prediction.get("event_start_time"))
    canonical_game_id = str(role.get("verified_canonical_event_id") or "").strip()
    player_name = str(role.get("player") or prediction.get("player") or "").strip()
    try:
        season = int(role.get("provider_season"))
    except (TypeError, ValueError):
        season = 0

    if event_start is None or not canonical_game_id or not player_name or season <= 0:
        return {
            "status": "IDENTITY_UNRESOLVED",
            "blocker": "NFL_CANONICAL_EVENT_PLAYER_IDENTITY_REQUIRED",
            "can_execute": False,
        }

    finality = _nfl_event_finality(
        role=role,
        event_start=event_start,
        http_get=http_get,
    )
    if finality.get("status") != "FINAL":
        return finality

    try:
        rows, digest = nfl_hydration._cached_nflverse_rows(
            season,
            http_get=http_get,
            now_ts=now.timestamp(),
        )
    except Exception as exc:
        return {
            "status": "OFFICIAL_SOURCE_UNAVAILABLE",
            "blocker": "NFL_CANONICAL_PLAYER_STATS_SOURCE_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "can_execute": False,
        }

    game_rows = [
        row for row in rows
        if str(row.get("game_id") or "").strip() == canonical_game_id
    ]
    source_url = nfl_hydration.NFLVERSE_URL.format(season=season)
    source = f"{source_url}#sha256={digest};game_id={canonical_game_id}"
    if not game_rows:
        return {
            "status": "OFFICIAL_STAT_SOURCE_NOT_YET_UPDATED",
            "blocker": "NFL_CANONICAL_GAME_ROW_MISSING",
            "canonical_game_id": canonical_game_id,
            "settlement_source": source,
            "can_execute": False,
        }

    expected_name = nfl_hydration._name_key(player_name)
    matches = [
        row for row in game_rows
        if nfl_hydration._name_key(_nfl_row_name(row)) == expected_name
    ]
    if not matches:
        return {
            "status": "OFFICIAL_PLAYER_STAT_ROW_MISSING",
            "blocker": "NFL_CANONICAL_PLAYER_GAME_ROW_REQUIRED",
            "canonical_game_id": canonical_game_id,
            "player": player_name,
            "settlement_source": source,
            "can_execute": False,
        }
    if len(matches) != 1:
        return {
            "status": "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS",
            "canonical_game_id": canonical_game_id,
            "player": player_name,
            "match_n": len(matches),
            "can_execute": False,
        }

    stat_type = str(prediction.get("stat_type") or "").strip().upper()
    if ("NFL", stat_type) not in NFL_SUPPORTED:
        return {
            "status": "MODEL_OR_IDENTITY_UNSUPPORTED",
            "blocker": "NFL_STAT_SETTLEMENT_UNSUPPORTED",
            "can_execute": False,
        }

    actual, components = _nfl_stat_value(matches[0], stat_type)
    if actual is None:
        return {
            "status": "OFFICIAL_STAT_MISSING",
            "canonical_game_id": canonical_game_id,
            "stat_type": stat_type,
            "settlement_source": source,
            "can_execute": False,
        }

    result = {
        "status": "SETTLED",
        "outcome": settlement._settlement_result(
            prediction=prediction,
            actual_stat=actual,
            source=source,
            now=now,
        ),
        "canonical_game_id": canonical_game_id,
        "source_digest": digest,
        "can_execute": False,
    }
    if components is not None:
        result["settlement_components"] = components
        result["touchdown_settlement_method"] = (
            "BERNOULLI_ANY_TOUCHDOWN_FROM_CANONICAL_TD_COMPONENTS"
        )
    return result


def run_exact_route_settlement(
    req: settlement.ExactRouteSettlementRequest,
    *,
    db: Any,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Settle one globally bounded immutable-forward batch across all routes."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    requested_routes = settlement._parse_routes(req.routes)
    supported_requested = set(requested_routes) & set(settlement.SUPPORTED_SETTLEMENT_ROUTES)
    predictions = scope_guard._eligible_forward_predictions(
        db,
        requested_routes=supported_requested,
        now=now,
    )
    ids = [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")]
    existing = settlement._existing_outcomes(db, ids) if ids else set()
    pending = [
        row for row in predictions
        if str(row.get("prediction_id")) not in existing
    ][: req.limit]
    snapshot_ids = [
        str(row["source_snapshot_id"])
        for row in pending
        if row.get("source_snapshot_id")
    ]
    snapshot_map = settlement._snapshots(db, snapshot_ids) if snapshot_ids else {}

    results: list[dict[str, Any]] = []
    for prediction in pending:
        prediction_id = str(prediction.get("prediction_id") or "")
        snapshot_id = str(prediction.get("source_snapshot_id") or "")
        snapshot = snapshot_map.get(snapshot_id)
        key = route_key(prediction.get("sport"), prediction.get("stat_type"))
        if not snapshot:
            results.append({
                "prediction_id": prediction_id,
                "route": route_token(*key),
                "status": "IDENTITY_UNRESOLVED",
                "blocker": "SOURCE_SNAPSHOT_REQUIRED",
                "can_execute": False,
            })
            continue

        if key in settlement.MLB_SUPPORTED:
            result = settlement.settle_mlb_scalar(
                prediction, snapshot, http_get=http_get, now=now
            )
        elif key in settlement.WNBA_SUPPORTED:
            result = settlement.settle_wnba_scalar(
                prediction, snapshot, http_get=http_get, now=now
            )
        elif key in NFL_SUPPORTED:
            result = settle_nfl_scalar(
                prediction, snapshot, http_get=http_get, now=now
            )
        else:
            result = {
                "status": "EXACT_ROUTE_SETTLEMENT_ADAPTER_REQUIRED",
                "can_execute": False,
            }

        outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else None
        if outcome is not None:
            settlement._persist_outcome(db, outcome)
        results.append({
            "prediction_id": prediction_id,
            "route": route_token(*key),
            **result,
        })

    settled = sum(
        1 for row in results if str(row.get("status") or "").startswith("SETTLED")
    )
    held = len(results) - settled
    route_dispositions = []
    for key in requested_routes:
        status, source, blocker = settlement._route_status(key)
        route_dispositions.append({
            "sport": key[0],
            "stat_type": key[1],
            "status": status,
            "official_source": source,
            "blocker": blocker,
            "can_execute": False,
        })

    return {
        "terminal": True,
        "run_status": "COMPLETED",
        "requested_routes": len(requested_routes),
        "supported_settlement_routes_requested": len(supported_requested),
        "forward_evidence_predictions_eligible": len(predictions),
        "pending_predictions_considered": len(pending),
        "settled_or_voided": settled,
        "held": held,
        "results": results,
        "route_dispositions": route_dispositions,
        "full_settlement_inventory": settlement.build_settlement_inventory(),
        "settlement_scope": "IMMUTABLE_FORWARD_EVIDENCE_ONLY",
        "ordinary_prediction_rows_excluded": True,
        "calibration_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "production_registration_performed": False,
        "can_execute": False,
    }


def install() -> None:
    if getattr(settlement, _PATCH_MARKER, False):
        return

    settlement.NFL_SUPPORTED = NFL_SUPPORTED
    settlement.SUPPORTED_SETTLEMENT_ROUTES = frozenset(
        set(settlement.SUPPORTED_SETTLEMENT_ROUTES) | set(NFL_SUPPORTED)
    )
    settlement._route_status = _route_status
    settlement.settle_nfl_scalar = settle_nfl_scalar
    settlement.run_exact_route_settlement = run_exact_route_settlement
    scope_guard.run_exact_route_settlement = run_exact_route_settlement

    for module_name in (
        "v17.prop_lifecycle_autopilot",
        "v17.prop_forward_cohort_route",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, "run_exact_route_settlement", run_exact_route_settlement)

    setattr(settlement, _PATCH_MARKER, True)


install()


__all__ = [
    "NFL_SUPPORTED",
    "NFL_SETTLEMENT_SOURCE",
    "install",
    "run_exact_route_settlement",
    "settle_nfl_scalar",
]
