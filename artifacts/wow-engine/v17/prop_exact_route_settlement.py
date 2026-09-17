"""Exact-route official-stat settlement for V17 forward prop evidence.

This module grades only immutable governed prediction rows whose exact sport/stat
route has an explicit official outcome adapter. It never infers a neighboring
stat, never substitutes sportsbook settlement, and never grants model,
certification, promotion, publication, or execution authority.

Currently wired official adapters:
* MLB pitcher strikeouts, pitching outs, strikes thrown, balls thrown;
* MLB batter plate appearances;
* WNBA points, rebounds, assists, three-pointers made.

MLB 1IP and Fantasy Score keep separate typed settlement blockers until their
exact event-tree/component settlement contracts are wired.

``can_execute`` is always false.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable, Iterable, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field

from v17.cross_sport_certification_inventory import CERTIFICATION_SPORTS
from v17.prop_capability_manifest import DECLARED_PROP_LANES, normalize_prop_sport
from v17.prop_universal_forward_evidence import route_key, route_token
from v17.fantasy_score_forward_cohort_runtime import LANE_SPECS
from wnba_prop_auto_hydration import STAT_COLUMNS as WNBA_STAT_COLUMNS

CAN_EXECUTE = False
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
PAGE_SIZE = 1000
IN_FILTER_CHUNK_SIZE = 150
HTTP_TIMEOUT_SECONDS = 12.0

MLB_STATS_API_BASE = "https://statsapi.mlb.com/api/v1.1"
WNBA_SCHEDULE_URL = "https://cdn.wnba.com/static/json/staticData/scheduleLeagueV2.json"
WNBA_STATS_URL = "https://stats.wnba.com/stats/leaguegamelog"

MLB_SUPPORTED = frozenset({
    ("MLB", "PITCHER_STRIKEOUTS"),
    ("MLB", "PITCHING_OUTS"),
    ("MLB", "STRIKES_THROWN"),
    ("MLB", "BALLS_THROWN"),
    ("MLB", "PLATE_APPEARANCES"),
})
WNBA_SUPPORTED = frozenset({
    ("WNBA", "POINTS"),
    ("WNBA", "REBOUNDS"),
    ("WNBA", "ASSISTS"),
    ("WNBA", "THREE_POINTERS_MADE"),
})
SUPPORTED_SETTLEMENT_ROUTES = MLB_SUPPORTED | WNBA_SUPPORTED
SEPARATE_SETTLEMENT_ROUTES = frozenset({("MLB", "1ST_INNING_PITCHES_THROWN")})
FANTASY_SETTLEMENT_ROUTES = frozenset((spec.sport, spec.stat_type) for spec in LANE_SPECS.values())

SETTLEMENT_READY = "OFFICIAL_SETTLEMENT_ADAPTER_READY"
SEPARATE_SETTLEMENT_REQUIRED = "SEPARATE_SETTLEMENT_CONTRACT_REQUIRED"
FANTASY_COMPONENT_SETTLEMENT_REQUIRED = "FANTASY_SCORE_COMPONENT_SETTLEMENT_REQUIRED"
NO_CURRENT_PROP_CATEGORY_DECLARED = "NO_CURRENT_PROP_CATEGORY_DECLARED"
EXACT_SETTLEMENT_ADAPTER_REQUIRED = "EXACT_ROUTE_SETTLEMENT_ADAPTER_REQUIRED"


class ExactRouteSettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)
    limit: int = Field(default=200, ge=1, le=1000)


@dataclass(frozen=True)
class SettlementRouteInventoryRow:
    sport: str
    stat_type: str
    status: str
    official_source: str | None
    blocker: str | None
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _route_status(key: tuple[str, str]) -> tuple[str, str | None, str | None]:
    if key in MLB_SUPPORTED:
        return SETTLEMENT_READY, "MLB_STATS_API_OFFICIAL_GAME_FEED", None
    if key in WNBA_SUPPORTED:
        return SETTLEMENT_READY, "WNBA_OFFICIAL_CDN_PLUS_STATS_LEAGUE_GAME_LOG", None
    if key in SEPARATE_SETTLEMENT_ROUTES:
        return SEPARATE_SETTLEMENT_REQUIRED, None, "MLB_1IP_EXACT_EVENT_TREE_SETTLEMENT_REQUIRED"
    if key in FANTASY_SETTLEMENT_ROUTES:
        return FANTASY_COMPONENT_SETTLEMENT_REQUIRED, None, "EXACT_FANTASY_SCORING_PROFILE_COMPONENT_SETTLEMENT_REQUIRED"
    if key in DECLARED_PROP_LANES:
        return EXACT_SETTLEMENT_ADAPTER_REQUIRED, None, "CERTIFIED_OFFICIAL_OUTCOME_ADAPTER_NOT_WIRED"
    return NO_CURRENT_PROP_CATEGORY_DECLARED, None, "PROP_ROUTE_NOT_DECLARED"


def build_settlement_inventory(*, sports: Iterable[str] = CERTIFICATION_SPORTS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_sports: set[str] = set()
    for key in sorted(DECLARED_PROP_LANES):
        status, source, blocker = _route_status(key)
        rows.append(SettlementRouteInventoryRow(
            sport=key[0], stat_type=key[1], status=status,
            official_source=source, blocker=blocker,
        ).as_dict())
        seen_sports.add(key[0])
    for raw in sports:
        sport = normalize_prop_sport(raw)
        if sport in seen_sports:
            continue
        rows.append(SettlementRouteInventoryRow(
            sport=sport,
            stat_type="__SPORT_PROP_CATEGORY_INVENTORY__",
            status=NO_CURRENT_PROP_CATEGORY_DECLARED,
            official_source=None,
            blocker="NO_CURRENT_PROP_CATEGORY_DECLARED",
        ).as_dict())
    rows.sort(key=lambda row: (row["sport"], row["stat_type"]))
    return rows


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _name_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _request_json(
    url: str,
    *,
    http_get: Callable[..., Any],
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    response = http_get(
        url,
        params=dict(params or {}),
        headers=dict(headers or {}),
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    status = int(getattr(response, "status_code", 200))
    if status >= 400:
        raise RuntimeError(f"OFFICIAL_SOURCE_HTTP_{status}")
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("OFFICIAL_SOURCE_JSON_INVALID")
    return dict(payload)


def _settlement_result(
    *,
    prediction: Mapping[str, Any],
    actual_stat: float | None,
    source: str,
    now: datetime,
    void_reason: str | None = None,
) -> dict[str, Any]:
    if void_reason:
        return {
            "prediction_id": prediction["prediction_id"],
            "official_result": f"VOID_{void_reason}",
            "actual_stat": None,
            "hit": None,
            "push": False,
            "void": True,
            "settlement_source": source,
            "settlement_timestamp": now.isoformat(),
            "failure_category": void_reason,
        }
    if actual_stat is None:
        raise ValueError("actual_stat is required for non-void settlement")
    line = _finite_number(prediction.get("line"))
    direction = str(prediction.get("direction") or "").strip().upper()
    if line is None or direction not in {"MORE", "LESS"}:
        raise ValueError("prediction line/direction invalid")
    push = actual_stat == line
    hit: bool | None
    if push:
        hit = None
        official_result = "PUSH"
    elif direction == "MORE":
        hit = actual_stat > line
        official_result = "HIT" if hit else "MISS"
    else:
        hit = actual_stat < line
        official_result = "HIT" if hit else "MISS"
    return {
        "prediction_id": prediction["prediction_id"],
        "official_result": official_result,
        "actual_stat": actual_stat,
        "hit": hit,
        "push": push,
        "void": False,
        "settlement_source": source,
        "settlement_timestamp": now.isoformat(),
        "failure_category": None if hit is not False else str(prediction.get("primary_failure_path") or "MODEL_MISS"),
    }


def _mlb_player_node(feed: Mapping[str, Any], prediction: Mapping[str, Any], role: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    players = feed.get("gameData", {}).get("players", {}) if isinstance(feed.get("gameData"), Mapping) else {}
    expected_id = str(role.get("player_id") or "").strip()
    player_key: str | None = None
    if expected_id and isinstance(players, Mapping) and f"ID{expected_id}" in players:
        player_key = f"ID{expected_id}"
    elif isinstance(players, Mapping):
        matches = [
            key for key, value in players.items()
            if isinstance(value, Mapping)
            and _name_key(value.get("fullName")) == _name_key(prediction.get("player"))
        ]
        if len(matches) == 1:
            player_key = str(matches[0])
    if player_key is None:
        return None, None
    live = feed.get("liveData") if isinstance(feed.get("liveData"), Mapping) else {}
    box = live.get("boxscore") if isinstance(live.get("boxscore"), Mapping) else {}
    teams = box.get("teams") if isinstance(box.get("teams"), Mapping) else {}
    for side in ("away", "home"):
        team = teams.get(side) if isinstance(teams.get(side), Mapping) else {}
        nodes = team.get("players") if isinstance(team.get("players"), Mapping) else {}
        node = nodes.get(player_key)
        if isinstance(node, Mapping):
            return dict(node), side
    return None, None


def _outs_from_ip(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    pieces = text.split(".", 1)
    try:
        innings = int(pieces[0])
        partial = int(pieces[1]) if len(pieces) == 2 else 0
    except ValueError:
        return None
    if innings < 0 or partial not in {0, 1, 2}:
        return None
    return innings * 3 + partial


def settle_mlb_scalar(
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    role = snapshot.get("role_status") if isinstance(snapshot.get("role_status"), Mapping) else {}
    game_pk = str(role.get("official_game_pk") or "").strip()
    if not game_pk.isdigit():
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "OFFICIAL_MLB_GAME_PK_REQUIRED", "can_execute": False}
    source = f"{MLB_STATS_API_BASE}/game/{game_pk}/feed/live"
    try:
        feed = _request_json(source, http_get=http_get)
    except Exception as exc:
        return {"status": "OFFICIAL_SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "can_execute": False}
    game_data = feed.get("gameData") if isinstance(feed.get("gameData"), Mapping) else {}
    status = game_data.get("status") if isinstance(game_data.get("status"), Mapping) else {}
    if str(status.get("abstractGameState") or "") != "Final":
        return {"status": "NOT_FINAL", "official_state": status.get("abstractGameState"), "can_execute": False}

    node, side = _mlb_player_node(feed, prediction, role)
    if node is None:
        return {
            "status": "SETTLED_VOID_DNP",
            "outcome": _settlement_result(prediction=prediction, actual_stat=None, source=source, now=now, void_reason="PLAYER_DNP"),
            "can_execute": False,
        }
    stats = node.get("stats") if isinstance(node.get("stats"), Mapping) else {}
    stat_type = str(prediction.get("stat_type") or "").upper()

    if stat_type in {"PITCHER_STRIKEOUTS", "PITCHING_OUTS", "STRIKES_THROWN", "BALLS_THROWN"}:
        pitching = stats.get("pitching") if isinstance(stats.get("pitching"), Mapping) else {}
        games_started = _finite_number(pitching.get("gamesStarted"))
        if games_started != 1.0:
            return {
                "status": "SETTLED_VOID_NOT_STARTER",
                "outcome": _settlement_result(prediction=prediction, actual_stat=None, source=source, now=now, void_reason="NOT_STARTING_PITCHER"),
                "can_execute": False,
            }
        if stat_type == "PITCHER_STRIKEOUTS":
            actual = _finite_number(pitching.get("strikeOuts"))
        elif stat_type == "PITCHING_OUTS":
            outs = _outs_from_ip(pitching.get("inningsPitched"))
            actual = float(outs) if outs is not None else None
        elif stat_type == "STRIKES_THROWN":
            actual = _finite_number(pitching.get("strikes"))
        else:
            pitches = _finite_number(pitching.get("numberOfPitches"))
            strikes = _finite_number(pitching.get("strikes"))
            actual = pitches - strikes if pitches is not None and strikes is not None and pitches >= strikes else None
    elif stat_type == "PLATE_APPEARANCES":
        batting = stats.get("batting") if isinstance(stats.get("batting"), Mapping) else {}
        live = feed.get("liveData") if isinstance(feed.get("liveData"), Mapping) else {}
        box = live.get("boxscore") if isinstance(live.get("boxscore"), Mapping) else {}
        team = box.get("teams", {}).get(side, {}) if isinstance(box.get("teams"), Mapping) and side else {}
        order = team.get("battingOrder") if isinstance(team, Mapping) and isinstance(team.get("battingOrder"), list) else []
        person = node.get("person") if isinstance(node.get("person"), Mapping) else {}
        player_id = person.get("id")
        if player_id not in order:
            return {
                "status": "SETTLED_VOID_NOT_STARTER",
                "outcome": _settlement_result(prediction=prediction, actual_stat=None, source=source, now=now, void_reason="NOT_STARTING_BATTER"),
                "can_execute": False,
            }
        actual = _finite_number(batting.get("plateAppearances"))
    else:
        return {"status": "MODEL_OR_IDENTITY_UNSUPPORTED", "blocker": "MLB_STAT_SETTLEMENT_UNSUPPORTED", "can_execute": False}

    if actual is None:
        return {"status": "OFFICIAL_STAT_MISSING", "can_execute": False}
    return {
        "status": "SETTLED",
        "outcome": _settlement_result(prediction=prediction, actual_stat=actual, source=source, now=now),
        "can_execute": False,
    }


def _wnba_stats_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://stats.wnba.com",
        "Referer": "https://www.wnba.com/",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
    }


def _result_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list):
        one = payload.get("resultSet")
        result_sets = [one] if isinstance(one, Mapping) else []
    target = next((item for item in result_sets if isinstance(item, Mapping) and str(item.get("name") or "").casefold() == "leaguegamelog"), None)
    if target is None and len(result_sets) == 1 and isinstance(result_sets[0], Mapping):
        target = result_sets[0]
    if not isinstance(target, Mapping):
        raise RuntimeError("WNBA_STATS_RESULT_SET_MISSING")
    headers = target.get("headers")
    row_set = target.get("rowSet")
    if not isinstance(headers, list) or not isinstance(row_set, list):
        raise RuntimeError("WNBA_STATS_RESULT_SET_INVALID")
    return [dict(zip(headers, row)) for row in row_set if isinstance(row, list) and len(row) == len(headers)]


def _wnba_game(schedule: Mapping[str, Any], game_id: str) -> dict[str, Any] | None:
    league = schedule.get("leagueSchedule") if isinstance(schedule.get("leagueSchedule"), Mapping) else {}
    dates = league.get("gameDates") if isinstance(league.get("gameDates"), list) else []
    matches: list[dict[str, Any]] = []
    for date in dates:
        games = date.get("games") if isinstance(date, Mapping) and isinstance(date.get("games"), list) else []
        for game in games:
            if isinstance(game, Mapping) and str(game.get("gameId") or "") == game_id:
                matches.append(dict(game))
    return matches[0] if len(matches) == 1 else None


def settle_wnba_scalar(
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
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "WNBA_OFFICIAL_GAME_AND_PLAYER_ID_REQUIRED", "can_execute": False}
    try:
        schedule = _request_json(
            WNBA_SCHEDULE_URL,
            http_get=http_get,
            headers={"User-Agent": _wnba_stats_headers()["User-Agent"], "Accept": "application/json"},
        )
    except Exception as exc:
        return {"status": "OFFICIAL_SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "can_execute": False}
    game = _wnba_game(schedule, game_id)
    if game is None:
        return {"status": "OFFICIAL_EVENT_ID_MISMATCH", "can_execute": False}
    if int(game.get("gameStatus") or 0) != 3:
        return {"status": "NOT_FINAL", "game_status": game.get("gameStatus"), "can_execute": False}

    event_start = _aware(prediction.get("event_start_time"))
    if event_start is None:
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "EVENT_START_REQUIRED", "can_execute": False}
    try:
        payload = _request_json(
            WNBA_STATS_URL,
            http_get=http_get,
            params={
                "LeagueID": "10", "PlayerOrTeam": "P", "Season": str(event_start.year),
                "SeasonType": "Regular Season", "Counter": "0", "DateFrom": "", "DateTo": "",
                "Direction": "ASC", "Sorter": "DATE",
            },
            headers=_wnba_stats_headers(),
        )
        rows = _result_rows(payload)
    except Exception as exc:
        return {"status": "OFFICIAL_SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "can_execute": False}
    matches = [
        row for row in rows
        if str(row.get("GAME_ID") or "") == game_id
        and str(row.get("PLAYER_ID") or row.get("PERSON_ID") or "") == player_id
    ]
    source = f"{WNBA_STATS_URL}?LeagueID=10&Season={event_start.year}&PlayerOrTeam=P"
    if not matches:
        return {
            "status": "SETTLED_VOID_DNP",
            "outcome": _settlement_result(prediction=prediction, actual_stat=None, source=source, now=now, void_reason="PLAYER_DNP"),
            "can_execute": False,
        }
    if len(matches) != 1:
        return {"status": "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS", "match_n": len(matches), "can_execute": False}
    stat_type = str(prediction.get("stat_type") or "").upper()
    column = WNBA_STAT_COLUMNS.get(stat_type)
    if not column:
        return {"status": "MODEL_OR_IDENTITY_UNSUPPORTED", "blocker": "WNBA_STAT_SETTLEMENT_UNSUPPORTED", "can_execute": False}
    actual = _finite_number(matches[0].get(column))
    if actual is None:
        return {"status": "OFFICIAL_STAT_MISSING", "stat_column": column, "can_execute": False}
    return {
        "status": "SETTLED",
        "outcome": _settlement_result(prediction=prediction, actual_stat=actual, source=source, now=now),
        "can_execute": False,
    }


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as exc:
        raise RuntimeError(f"{boundary}:{type(exc).__name__}") from exc


def _paginate(boundary: str, build: Callable[[], Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    start = 0
    while True:
        page = _db_call(boundary, lambda start=start: build().range(start, start + PAGE_SIZE - 1).execute().data or [])
        rows = [dict(row) for row in page]
        output.extend(rows)
        if len(rows) < PAGE_SIZE:
            return output
        start += PAGE_SIZE


def _chunks(values: list[str], size: int = IN_FILTER_CHUNK_SIZE) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _existing_outcomes(db: Any, ids: list[str]) -> set[str]:
    output: set[str] = set()
    for chunk in _chunks(ids):
        rows = _paginate(
            "wow_outcomes.select_existing_exact_route_settlement",
            lambda chunk=chunk: db.table("wow_outcomes").select("prediction_id").in_("prediction_id", chunk),
        )
        output.update(str(row["prediction_id"]) for row in rows if row.get("prediction_id"))
    return output


def _snapshots(db: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(ids):
        rows = _paginate(
            "wow_prop_evidence_snapshots.select_settlement_identity",
            lambda chunk=chunk: db.table("wow_prop_evidence_snapshots")
            .select("source_snapshot_id,event_id,event_start_time,sport,player,stat_type,line,role_status,opportunity_ledger")
            .in_("source_snapshot_id", chunk),
        )
        output.update({str(row["source_snapshot_id"]): row for row in rows if row.get("source_snapshot_id")})
    return output


def _persist_outcome(db: Any, outcome: dict[str, Any]) -> None:
    _db_call(
        "wow_outcomes.upsert_exact_route_settlement",
        lambda: db.table("wow_outcomes").upsert(outcome, on_conflict="prediction_id", ignore_duplicates=True).execute(),
    )


def _parse_routes(tokens: list[str]) -> list[tuple[str, str]]:
    if not tokens:
        return sorted(DECLARED_PROP_LANES)
    output: list[tuple[str, str]] = []
    for token in tokens:
        parts = str(token).split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid route token {token!r}")
        key = route_key(parts[0], parts[1])
        if key not in DECLARED_PROP_LANES:
            raise ValueError(f"undeclared route {route_token(*key)}")
        if key not in output:
            output.append(key)
    return output


def run_exact_route_settlement(
    req: ExactRouteSettlementRequest,
    *,
    db: Any,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    requested_routes = _parse_routes(req.routes)
    supported_requested = set(requested_routes) & set(SUPPORTED_SETTLEMENT_ROUTES)
    predictions = _paginate(
        "wow_predictions.select_exact_route_unsettled_candidates",
        lambda: db.table("wow_predictions")
        .select(
            "prediction_id,source_snapshot_id,event_id,event_start_time,player,sport,stat_type,line,direction,"
            "model_family,model_artifact_version,model_artifact_checksum,primary_failure_path"
        )
        .eq("model_provider_identity", PROVIDER)
        .lt("event_start_time", now.isoformat())
        .order("event_start_time"),
    )
    predictions = [row for row in predictions if route_key(row.get("sport"), row.get("stat_type")) in supported_requested]
    ids = [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")]
    existing = _existing_outcomes(db, ids) if ids else set()
    pending = [row for row in predictions if str(row.get("prediction_id")) not in existing][: req.limit]
    snapshot_ids = [str(row["source_snapshot_id"]) for row in pending if row.get("source_snapshot_id")]
    snapshot_map = _snapshots(db, snapshot_ids) if snapshot_ids else {}

    results: list[dict[str, Any]] = []
    for prediction in pending:
        prediction_id = str(prediction.get("prediction_id") or "")
        snapshot_id = str(prediction.get("source_snapshot_id") or "")
        snapshot = snapshot_map.get(snapshot_id)
        key = route_key(prediction.get("sport"), prediction.get("stat_type"))
        if not snapshot:
            results.append({"prediction_id": prediction_id, "route": route_token(*key), "status": "IDENTITY_UNRESOLVED", "blocker": "SOURCE_SNAPSHOT_REQUIRED", "can_execute": False})
            continue
        if key in MLB_SUPPORTED:
            result = settle_mlb_scalar(prediction, snapshot, http_get=http_get, now=now)
        elif key in WNBA_SUPPORTED:
            result = settle_wnba_scalar(prediction, snapshot, http_get=http_get, now=now)
        else:
            result = {"status": "EXACT_ROUTE_SETTLEMENT_ADAPTER_REQUIRED", "can_execute": False}
        outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else None
        if outcome is not None:
            _persist_outcome(db, outcome)
        results.append({"prediction_id": prediction_id, "route": route_token(*key), **result})

    settled = sum(1 for row in results if str(row.get("status") or "").startswith("SETTLED"))
    held = len(results) - settled
    route_dispositions = []
    for key in requested_routes:
        status, source, blocker = _route_status(key)
        route_dispositions.append({
            "sport": key[0], "stat_type": key[1], "status": status,
            "official_source": source, "blocker": blocker, "can_execute": False,
        })
    return {
        "terminal": True,
        "run_status": "COMPLETED",
        "requested_routes": len(requested_routes),
        "supported_settlement_routes_requested": len(supported_requested),
        "pending_predictions_considered": len(pending),
        "settled_or_voided": settled,
        "held": held,
        "results": results,
        "route_dispositions": route_dispositions,
        "full_settlement_inventory": build_settlement_inventory(),
        "calibration_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "production_registration_performed": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "ExactRouteSettlementRequest",
    "MLB_SUPPORTED",
    "SUPPORTED_SETTLEMENT_ROUTES",
    "WNBA_SUPPORTED",
    "build_settlement_inventory",
    "run_exact_route_settlement",
    "settle_mlb_scalar",
    "settle_wnba_scalar",
]
