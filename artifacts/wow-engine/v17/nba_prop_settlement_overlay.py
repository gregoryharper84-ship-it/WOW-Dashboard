"""Exact postgame settlement for research-only NBA scalar/composite candidates.

Uses the official NBA CDN live box score for the exact hydrated game/player ID.
The overlay adds settlement evidence only. NBA routes remain candidate-only and
non-publishable until forward calibration, independent certification, governed
promotion, production registration, availability review, and canonical Action
canary all pass. ``can_execute`` is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
import sys
from typing import Any, Callable, Mapping

import httpx

import v17.nfl_prop_settlement_overlay as nfl_overlay  # preserve installed NFL settlement
import v17.prop_exact_route_settlement as settlement
import v17.prop_exact_route_settlement_scope_guard as scope_guard
from v17.prop_universal_forward_evidence import route_key, route_token

NBA_SUPPORTED = frozenset({
    ("NBA", "POINTS"),
    ("NBA", "REBOUNDS"),
    ("NBA", "ASSISTS"),
    ("NBA", "PRA"),
    ("NBA", "POINTS_REBOUNDS"),
    ("NBA", "POINTS_ASSISTS"),
    ("NBA", "REBOUNDS_ASSISTS"),
})
NBA_SETTLEMENT_SOURCE = "NBA_OFFICIAL_CDN_LIVE_BOX_SCORE"
NBA_BOX_SCORE_URL = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
_PATCH_MARKER = "_wow_nba_exact_prop_settlement_overlay"
_ORIGINAL_ROUTE_STATUS = settlement._route_status


def _route_status(key: tuple[str, str]) -> tuple[str, str | None, str | None]:
    if key in NBA_SUPPORTED:
        return settlement.SETTLEMENT_READY, NBA_SETTLEMENT_SOURCE, None
    return _ORIGINAL_ROUTE_STATUS(key)


def _player_rows(game: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for side in ("homeTeam", "awayTeam"):
        team = game.get(side) if isinstance(game.get(side), Mapping) else {}
        players = team.get("players") if isinstance(team, Mapping) else None
        if not isinstance(players, list):
            continue
        rows.extend(dict(row) for row in players if isinstance(row, Mapping))
    return rows


def _stat_components(player: Mapping[str, Any]) -> dict[str, float] | None:
    stats = player.get("statistics") if isinstance(player.get("statistics"), Mapping) else {}
    values = {
        "POINTS": settlement._finite_number(stats.get("points")),
        "REBOUNDS": settlement._finite_number(stats.get("reboundsTotal")),
        "ASSISTS": settlement._finite_number(stats.get("assists")),
    }
    if any(value is None for value in values.values()):
        return None
    return {key: float(value) for key, value in values.items() if value is not None}


def _actual_stat(stat_type: str, components: Mapping[str, float]) -> float | None:
    if stat_type in {"POINTS", "REBOUNDS", "ASSISTS"}:
        return float(components[stat_type])
    if stat_type == "PRA":
        return float(components["POINTS"] + components["REBOUNDS"] + components["ASSISTS"])
    if stat_type == "POINTS_REBOUNDS":
        return float(components["POINTS"] + components["REBOUNDS"])
    if stat_type == "POINTS_ASSISTS":
        return float(components["POINTS"] + components["ASSISTS"])
    if stat_type == "REBOUNDS_ASSISTS":
        return float(components["REBOUNDS"] + components["ASSISTS"])
    return None


def settle_nba_scalar(
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    role = snapshot.get("role_status") if isinstance(snapshot.get("role_status"), Mapping) else {}
    game_id = str(role.get("official_game_id") or "").strip()
    player_id = str(role.get("player_id") or "").strip()
    if not game_id or not player_id:
        return {
            "status": "IDENTITY_UNRESOLVED",
            "blocker": "OFFICIAL_NBA_GAME_ID_AND_PLAYER_ID_REQUIRED",
            "can_execute": False,
        }

    source = NBA_BOX_SCORE_URL.format(game_id=game_id)
    try:
        payload = settlement._request_json(
            source,
            http_get=http_get,
            headers={"User-Agent": "WOW-Research/1.0", "Accept": "application/json"},
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "OFFICIAL_SOURCE_UNAVAILABLE",
            "blocker": "NBA_OFFICIAL_BOX_SCORE_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "can_execute": False,
        }

    game = payload.get("game") if isinstance(payload.get("game"), Mapping) else {}
    observed_game_id = str(game.get("gameId") or "").strip()
    if observed_game_id and observed_game_id != game_id:
        return {
            "status": "OFFICIAL_EVENT_ID_MISMATCH",
            "blocker": "NBA_OFFICIAL_GAME_ID_CONFLICT",
            "expected_game_id": game_id,
            "observed_game_id": observed_game_id,
            "can_execute": False,
        }
    game_status = int(game.get("gameStatus") or 0)
    if game_status != 3:
        return {
            "status": "NOT_FINAL",
            "official_state": game.get("gameStatusText"),
            "official_game_id": game_id,
            "can_execute": False,
        }

    matches = [
        row for row in _player_rows(game)
        if str(row.get("personId") or row.get("personID") or "").strip() == player_id
    ]
    if not matches:
        return {
            "status": "SETTLED_VOID_DNP",
            "outcome": settlement._settlement_result(
                prediction=prediction,
                actual_stat=None,
                source=source,
                now=now,
                void_reason="PLAYER_DNP",
            ),
            "can_execute": False,
        }
    if len(matches) != 1:
        return {
            "status": "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS",
            "official_game_id": game_id,
            "player_id": player_id,
            "match_n": len(matches),
            "can_execute": False,
        }

    components = _stat_components(matches[0])
    if components is None:
        return {
            "status": "OFFICIAL_STAT_MISSING",
            "blocker": "NBA_OFFICIAL_COMPONENT_STATS_REQUIRED",
            "official_game_id": game_id,
            "player_id": player_id,
            "can_execute": False,
        }
    stat_type = str(prediction.get("stat_type") or "").strip().upper()
    actual = _actual_stat(stat_type, components)
    if actual is None or ("NBA", stat_type) not in NBA_SUPPORTED:
        return {
            "status": "MODEL_OR_IDENTITY_UNSUPPORTED",
            "blocker": "NBA_STAT_SETTLEMENT_UNSUPPORTED",
            "can_execute": False,
        }
    return {
        "status": "SETTLED",
        "outcome": settlement._settlement_result(
            prediction=prediction,
            actual_stat=actual,
            source=source,
            now=now,
        ),
        "settlement_components": components,
        "settlement_method": "EXACT_OFFICIAL_NBA_PLAYER_EVENT_COMPONENT_SUM",
        "official_game_id": game_id,
        "can_execute": False,
    }


def run_exact_route_settlement(
    req: settlement.ExactRouteSettlementRequest,
    *,
    db: Any,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
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
    pending = [row for row in predictions if str(row.get("prediction_id")) not in existing][: req.limit]
    snapshot_ids = [str(row["source_snapshot_id"]) for row in pending if row.get("source_snapshot_id")]
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
            result = settlement.settle_mlb_scalar(prediction, snapshot, http_get=http_get, now=now)
        elif key in settlement.WNBA_SUPPORTED:
            result = settlement.settle_wnba_scalar(prediction, snapshot, http_get=http_get, now=now)
        elif key in nfl_overlay.NFL_SUPPORTED:
            result = nfl_overlay.settle_nfl_scalar(prediction, snapshot, http_get=http_get, now=now)
        elif key in NBA_SUPPORTED:
            result = settle_nba_scalar(prediction, snapshot, http_get=http_get, now=now)
        else:
            result = {"status": "EXACT_ROUTE_SETTLEMENT_ADAPTER_REQUIRED", "can_execute": False}

        outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else None
        if outcome is not None:
            settlement._persist_outcome(db, outcome)
        results.append({"prediction_id": prediction_id, "route": route_token(*key), **result})

    settled = sum(1 for row in results if str(row.get("status") or "").startswith("SETTLED"))
    route_dispositions = []
    for key in requested_routes:
        status, source, blocker = settlement._route_status(key)
        route_dispositions.append({
            "sport": key[0], "stat_type": key[1], "status": status,
            "official_source": source, "blocker": blocker, "can_execute": False,
        })
    return {
        "terminal": True,
        "run_status": "COMPLETED",
        "requested_routes": len(requested_routes),
        "supported_settlement_routes_requested": len(supported_requested),
        "forward_evidence_predictions_eligible": len(predictions),
        "pending_predictions_considered": len(pending),
        "settled_or_voided": settled,
        "held": len(results) - settled,
        "results": results,
        "route_dispositions": route_dispositions,
        "full_settlement_inventory": settlement.build_settlement_inventory(),
        "settlement_scope": "IMMUTABLE_FORWARD_EVIDENCE_ONLY",
        "ordinary_prediction_rows_excluded": True,
        "calibration_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "publication_authority_changed": False,
        "can_execute": False,
    }


def install() -> None:
    if getattr(settlement, _PATCH_MARKER, False):
        return
    settlement.NBA_SUPPORTED = NBA_SUPPORTED
    settlement.SUPPORTED_SETTLEMENT_ROUTES = frozenset(
        set(settlement.SUPPORTED_SETTLEMENT_ROUTES) | set(NBA_SUPPORTED)
    )
    settlement._route_status = _route_status
    settlement.settle_nba_scalar = settle_nba_scalar
    settlement.run_exact_route_settlement = run_exact_route_settlement
    # Preserve the established NFL overlay as the canonical exported settlement
    # facade while extending that same facade to NBA. This keeps downstream
    # identity/invariant checks intact and avoids parallel global runners.
    nfl_overlay.run_exact_route_settlement = run_exact_route_settlement
    scope_guard.run_exact_route_settlement = run_exact_route_settlement
    for module_name in ("v17.prop_lifecycle_autopilot", "v17.prop_forward_cohort_route"):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, "run_exact_route_settlement", run_exact_route_settlement)
    setattr(settlement, _PATCH_MARKER, True)


install()


__all__ = [
    "NBA_SUPPORTED", "NBA_SETTLEMENT_SOURCE", "install",
    "run_exact_route_settlement", "settle_nba_scalar",
]
