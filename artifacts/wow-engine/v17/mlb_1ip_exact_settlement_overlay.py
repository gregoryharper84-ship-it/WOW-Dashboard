"""Exact official event-tree settlement for MLB first-inning pitch-count props.

This Class-B evidence overlay extends the existing exact-route settlement engine for
``MLB:1ST_INNING_PITCHES_THROWN`` only. It counts official MLB StatsAPI pitch events
thrown by the exact starting pitcher during inning 1. It does not change the fitted
1IP model, calibration, line support, qualification thresholds, publication state,
promotion state, terminal authority, or execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx

import v17.prop_exact_route_settlement as settlement

CAN_EXECUTE = False
ROUTE = ("MLB", "1ST_INNING_PITCHES_THROWN")
_ORIGINAL_SETTLE_MLB_SCALAR = settlement.settle_mlb_scalar


def _pitcher_started(feed: Mapping[str, Any], prediction: Mapping[str, Any], role: Mapping[str, Any]) -> tuple[bool | None, dict[str, Any] | None]:
    node, _side = settlement._mlb_player_node(feed, prediction, role)
    if node is None:
        return None, None
    stats = node.get("stats") if isinstance(node.get("stats"), Mapping) else {}
    pitching = stats.get("pitching") if isinstance(stats.get("pitching"), Mapping) else {}
    games_started = settlement._finite_number(pitching.get("gamesStarted"))
    if games_started is None:
        return None, dict(node)
    return games_started == 1.0, dict(node)


def _first_inning_pitch_count(feed: Mapping[str, Any], *, player_id: str) -> tuple[int | None, int]:
    live = feed.get("liveData") if isinstance(feed.get("liveData"), Mapping) else {}
    plays = live.get("plays") if isinstance(live.get("plays"), Mapping) else {}
    all_plays = plays.get("allPlays") if isinstance(plays.get("allPlays"), list) else []
    pitch_n = 0
    matched_play_n = 0
    for play in all_plays:
        if not isinstance(play, Mapping):
            continue
        about = play.get("about") if isinstance(play.get("about"), Mapping) else {}
        if int(about.get("inning") or 0) != 1:
            continue
        matchup = play.get("matchup") if isinstance(play.get("matchup"), Mapping) else {}
        pitcher = matchup.get("pitcher") if isinstance(matchup.get("pitcher"), Mapping) else {}
        if str(pitcher.get("id") or "") != player_id:
            continue
        matched_play_n += 1
        events = play.get("playEvents") if isinstance(play.get("playEvents"), list) else []
        for event in events:
            if isinstance(event, Mapping) and event.get("isPitch") is True:
                pitch_n += 1
    if matched_play_n == 0:
        return None, 0
    return pitch_n, matched_play_n


def _settle_mlb_1ip_or_delegate(
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    stat_type = str(prediction.get("stat_type") or "").strip().upper()
    if stat_type != ROUTE[1]:
        return _ORIGINAL_SETTLE_MLB_SCALAR(prediction, snapshot, http_get=http_get, now=now)

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    role = snapshot.get("role_status") if isinstance(snapshot.get("role_status"), Mapping) else {}
    game_pk = str(role.get("official_game_pk") or "").strip()
    player_id = str(role.get("player_id") or "").strip()
    if not game_pk.isdigit() or not player_id.isdigit():
        return {
            "status": "IDENTITY_UNRESOLVED",
            "blocker": "OFFICIAL_MLB_GAME_PK_AND_PLAYER_ID_REQUIRED",
            "can_execute": False,
        }

    source = f"{settlement.MLB_STATS_API_BASE}/game/{game_pk}/feed/live"
    try:
        feed = settlement._request_json(source, http_get=http_get)
    except Exception as exc:  # noqa: BLE001
        return {"status": "OFFICIAL_SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "can_execute": False}

    game_data = feed.get("gameData") if isinstance(feed.get("gameData"), Mapping) else {}
    status = game_data.get("status") if isinstance(game_data.get("status"), Mapping) else {}
    if str(status.get("abstractGameState") or "") != "Final":
        return {"status": "NOT_FINAL", "official_state": status.get("abstractGameState"), "can_execute": False}

    started, node = _pitcher_started(feed, prediction, role)
    if node is None:
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
    if started is False:
        return {
            "status": "SETTLED_VOID_NOT_STARTER",
            "outcome": settlement._settlement_result(
                prediction=prediction,
                actual_stat=None,
                source=source,
                now=now,
                void_reason="NOT_STARTING_PITCHER",
            ),
            "can_execute": False,
        }
    if started is None:
        return {"status": "OFFICIAL_STARTER_STATUS_MISSING", "can_execute": False}

    actual, matched_play_n = _first_inning_pitch_count(feed, player_id=player_id)
    if actual is None:
        return {
            "status": "OFFICIAL_1IP_EVENT_TREE_MISSING",
            "blocker": "MLB_1IP_EXACT_EVENT_TREE_SETTLEMENT_UNAVAILABLE",
            "matched_play_n": matched_play_n,
            "can_execute": False,
        }

    return {
        "status": "SETTLED",
        "outcome": settlement._settlement_result(
            prediction=prediction,
            actual_stat=float(actual),
            source=source,
            now=now,
        ),
        "settlement_method": "EXACT_OFFICIAL_INNING1_PITCH_EVENTS_BY_STARTER_ID",
        "matched_play_n": matched_play_n,
        "can_execute": False,
    }


def install() -> None:
    settlement.MLB_SUPPORTED = frozenset(set(settlement.MLB_SUPPORTED) | {ROUTE})
    settlement.SEPARATE_SETTLEMENT_ROUTES = frozenset(
        key for key in settlement.SEPARATE_SETTLEMENT_ROUTES if key != ROUTE
    )
    settlement.SUPPORTED_SETTLEMENT_ROUTES = settlement.MLB_SUPPORTED | settlement.WNBA_SUPPORTED
    settlement.settle_mlb_scalar = _settle_mlb_1ip_or_delegate


install()


__all__ = ["CAN_EXECUTE", "ROUTE", "install"]
