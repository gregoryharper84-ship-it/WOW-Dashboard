"""Bounded OIDC control plane for MLB certified pitcher forward evidence.

The Render web-process evidence scheduler is intentionally disabled for memory
stability. This route lets a governed GitHub OIDC workflow seed immutable
pregame evidence in small repeat-safe batches from official MLB probable-pitcher
schedule data and the existing governed hydration adapter.

No sporting probability, calibration, certification, promotion, publication,
ranking, pricing, or execution authority is created here. can_execute=false.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from pick_request_runtime_core import PickRequestRow, RawPropEvidence, _snapshot_payload, _validate_evidence
from prop_auto_hydration import MLB_STATS_API_BASE, PropAutoHydrationError
from prop_auto_hydration_router import auto_hydrate_prop_evidence

CAN_EXECUTE = False
ROUTE_PATH = "/internal/v17/mlb-prop-forward-evidence/acquire"
SPORT = "MLB"
SOURCE_TYPE = "AUTONOMOUS_DISCOVERY"
PLATFORM = "MLB_STATS_API_OFFICIAL_V1"
STAT_TYPES = (
    "PITCHER_STRIKEOUTS",
    "PITCHING_OUTS",
    "STRIKES_THROWN",
    "BALLS_THROWN",
)


class MLBForwardEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_date: str
    requested_timezone: str = "America/Chicago"
    candidate_offset: int = Field(default=0, ge=0, le=2000)
    max_candidates: int = Field(default=24, ge=1, le=48)


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cached_http_get(http_get: Callable[..., Any]) -> Callable[..., Any]:
    cache: dict[tuple[Any, ...], Any] = {}

    def get(url: str, params=None, headers=None, **kwargs: Any) -> Any:
        params_key = tuple(sorted((str(k), str(v)) for k, v in dict(params or {}).items()))
        headers_key = tuple(sorted((str(k).lower(), str(v)) for k, v in dict(headers or {}).items()))
        key = (str(url), params_key, headers_key)
        if key not in cache:
            cache[key] = http_get(url, params=params, headers=headers, **kwargs)
        return cache[key]

    return get


def _request_schedule(slate_date: str, *, http_get: Callable[..., Any]) -> dict[str, Any]:
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


def _schedule_pitchers(
    payload: dict[str, Any],
    *,
    requested_date: str,
    requested_timezone: str,
    now: datetime,
) -> list[dict[str, Any]]:
    try:
        zone = ZoneInfo(requested_timezone)
        date.fromisoformat(requested_date)
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for block in payload.get("dates") or []:
        if not isinstance(block, dict):
            continue
        for game in block.get("games") or []:
            if not isinstance(game, dict):
                continue
            event_start = _aware(game.get("gameDate"))
            if (
                event_start is None
                or event_start <= now
                or event_start.astimezone(zone).date().isoformat() != requested_date
            ):
                continue
            game_pk = str(game.get("gamePk") or "").strip()
            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            if not game_pk:
                continue
            for side in ("home", "away"):
                side_node = teams.get(side) if isinstance(teams.get(side), dict) else {}
                probable = side_node.get("probablePitcher") if isinstance(side_node.get("probablePitcher"), dict) else {}
                player = " ".join(str(probable.get("fullName") or "").split())
                player_id = str(probable.get("id") or "").strip()
                if not player:
                    continue
                identity = (game_pk, player_id or player.lower())
                if identity in seen:
                    continue
                seen.add(identity)
                other = "away" if side == "home" else "home"
                opponent_node = teams.get(other) if isinstance(teams.get(other), dict) else {}
                opponent_team = opponent_node.get("team") if isinstance(opponent_node.get("team"), dict) else {}
                opponent = str(opponent_team.get("abbreviation") or opponent_team.get("name") or "").strip() or None
                candidates.append({
                    "event_id": f"MLB:{game_pk}",
                    "official_game_pk": game_pk,
                    "event_start_time": event_start.isoformat(),
                    "player": player,
                    "player_id": player_id or None,
                    "opponent": opponent,
                })
    candidates.sort(key=lambda row: (str(row["event_start_time"]), str(row["player"])))
    return candidates


def _candidate_line(game_log: list[float]) -> float:
    values = [float(value) for value in game_log]
    if not values:
        raise ValueError("game_log cannot be empty")
    return float(int(float(median(values))) + 0.5)


def _existing_snapshot(db: Any, candidate: dict[str, Any], stat_type: str) -> bool:
    result = (
        db.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id")
        .eq("event_id", str(candidate["event_id"]))
        .eq("sport", SPORT)
        .eq("player", str(candidate["player"]))
        .eq("stat_type", stat_type)
        .limit(1)
        .execute()
    )
    return bool(result.data or [])


def _flatten(candidates: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    return [(candidate, stat_type) for candidate in candidates for stat_type in STAT_TYPES]


def acquire_mlb_forward_evidence_batch(
    req: MLBForwardEvidenceRequest,
    *,
    db: Any,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cached_get = _cached_http_get(http_get)
    result: dict[str, Any] = {
        "status": "COMPLETED",
        "sport": SPORT,
        "requested_date": req.requested_date,
        "requested_timezone": req.requested_timezone,
        "candidate_offset": req.candidate_offset,
        "max_candidates": req.max_candidates,
        "total_candidates": 0,
        "window_candidate_n": 0,
        "attempted": 0,
        "already_captured": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "snapshot_write_failed": 0,
        "next_offset": None,
        "blockers": [],
        "stat_types": list(STAT_TYPES),
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
    try:
        schedule = _request_schedule(req.requested_date, http_get=cached_get)
        candidates = _schedule_pitchers(
            schedule,
            requested_date=req.requested_date,
            requested_timezone=req.requested_timezone,
            now=now,
        )
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [
            getattr(exc, "code", f"MLB_PROP_SCHEDULE_ACQUISITION_FAILED:{type(exc).__name__}")
        ]
        return result

    flattened = _flatten(candidates)
    result["total_candidates"] = len(flattened)
    start = min(req.candidate_offset, len(flattened))
    end = min(start + req.max_candidates, len(flattened))
    window = flattened[start:end]
    result["window_candidate_n"] = len(window)
    result["next_offset"] = end if end < len(flattened) else None

    for candidate, stat_type in window:
        result["attempted"] += 1
        try:
            if _existing_snapshot(db, candidate, stat_type):
                result["already_captured"] += 1
                continue
        except Exception as exc:
            result["held"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:MLB_EXISTING_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}"
            )
            continue

        try:
            raw = auto_hydrate_prop_evidence(
                sport=SPORT,
                player=str(candidate["player"]),
                stat_type=stat_type,
                event_start_time=str(candidate["event_start_time"]),
                http_get=cached_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_MLB_OIDC_FORWARD_DISCOVERY",
                opponent=candidate.get("opponent"),
                canonical_event_id=str(candidate["event_id"]),
            )
            evidence = RawPropEvidence.model_validate(raw)
            if not evidence.game_log:
                result["held"] += 1
                result["blockers"].append(
                    f"{candidate['player']}:{stat_type}:MLB_FORWARD_LINE_DERIVATION_INSUFFICIENT"
                )
                continue
            try:
                line = _candidate_line(evidence.game_log)
            except (TypeError, ValueError) as exc:
                result["held"] += 1
                result["blockers"].append(
                    f"{candidate['player']}:{stat_type}:MLB_FORWARD_LINE_DERIVATION_FAILED:{type(exc).__name__}"
                )
                continue
            row = PickRequestRow(
                row_key=(
                    f"mlb-forward:{candidate['official_game_pk']}:{candidate['player']}:"
                    f"{stat_type}:{line}"
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
        except PropAutoHydrationError as exc:
            result["held"] += 1
            result["blockers"].append(f"{candidate['player']}:{stat_type}:{exc.code}")
            continue
        except Exception as exc:
            result["held"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:MLB_FORWARD_EVIDENCE_PROCESSING_ERROR:{type(exc).__name__}"
            )
            continue

        try:
            db.table("wow_prop_evidence_snapshots").upsert(
                snapshot, on_conflict="source_snapshot_id"
            ).execute()
            result["persisted"] += 1
        except Exception as exc:
            result["held"] += 1
            result["snapshot_write_failed"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:MLB_PROP_SNAPSHOT_WRITE_FAILED:{type(exc).__name__}"
            )

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] and result["persisted"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["held"]:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


def install_mlb_prop_forward_evidence_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    if any(getattr(route, "path", None) == ROUTE_PATH for route in app.router.routes):
        return

    @app.post(
        ROUTE_PATH,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="acquireWowV17MlbPropForwardEvidence",
    )
    def acquire(req: MLBForwardEvidenceRequest) -> dict[str, Any]:
        return acquire_mlb_forward_evidence_batch(req, db=db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "ROUTE_PATH",
    "STAT_TYPES",
    "MLBForwardEvidenceRequest",
    "acquire_mlb_forward_evidence_batch",
    "install_mlb_prop_forward_evidence_route",
]
