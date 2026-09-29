"""Protected, bounded control plane for WNBA forward-evidence acquisition.

The Render web host intentionally keeps the autonomous in-process evidence sweep
disabled. GitHub's governed OIDC lifecycle workflow can invoke this route in
small rotating batches instead. The route only persists immutable pregame
snapshots for the already-fitted WNBA component routes.

No probability, calibration, certification, promotion, publication, ranking,
price, or execution authority is granted. ``can_execute=false`` always.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from prop_auto_hydration import PropAutoHydrationError
from v17 import wnba_prop_evidence_acquisition as acquisition

CAN_EXECUTE = False
ROUTE_PATH = "/internal/v17/wnba-prop-forward-evidence/acquire"


class WNBAForwardEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_date: str
    requested_timezone: str = "America/Chicago"
    candidate_offset: int = Field(default=0, ge=0, le=2000)
    max_candidates: int = Field(default=48, ge=1, le=96)


class _PinnedJsonResponse:
    status_code = 200
    content = b"{}"

    def __init__(self, payload: Mapping[str, Any]):
        self._payload = dict(payload)

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


def _cached_http_get(http_get: Callable[..., Any]) -> Callable[..., Any]:
    cache: dict[tuple[Any, ...], Any] = {}

    def get(url: str, params=None, headers=None, **kwargs: Any) -> Any:
        params_key = tuple(sorted((str(k), str(v)) for k, v in dict(params or {}).items()))
        headers_key = tuple(sorted((str(k).lower(), str(v)) for k, v in dict(headers or {}).items()))
        key = (str(url), params_key, headers_key)
        if key in cache:
            return cache[key]
        response = http_get(url, params=params, headers=headers, **kwargs)
        # Do not freeze a successful-but-non-JSON WNBA API/CDN response into the
        # request-local retry cache. The governed hydrator owns typed retry/fail-close.
        if str(url) == acquisition.wnba.WNBA_SCHEDULE_URL or str(url).startswith(acquisition.wnba.WNBA_STATS_BASE):
            try:
                payload = response.json()
            except Exception:
                return response
            if not isinstance(payload, Mapping):
                return response
        cache[key] = response
        return response

    return get


def _minimal_schedule_headers() -> dict[str, str]:
    return {
        "User-Agent": acquisition.wnba._stats_headers()["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _request_schedule_with_same_source_fallback(
    *, http_get: Callable[..., Any]
) -> tuple[dict[str, Any], str]:
    """Retry the identical official CDN schedule with a minimal public header set."""
    try:
        return acquisition._request_schedule(http_get=http_get), "BROWSER_CONTEXT"
    except acquisition.wnba.WNBAPropHydrationError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if (
            exc.code != "WNBA_OFFICIAL_SOURCE_UNAVAILABLE"
            or str(detail.get("url") or "") != acquisition.wnba.WNBA_SCHEDULE_URL
        ):
            raise
    payload = acquisition.wnba._request(
        acquisition.wnba.WNBA_SCHEDULE_URL,
        http_get=http_get,
        headers=_minimal_schedule_headers(),
    )
    if not isinstance(payload, Mapping):
        raise TypeError("WNBA schedule response was not an object")
    return dict(payload), "MINIMAL_OFFICIAL_CDN_FALLBACK"


def _pin_schedule_payload(
    http_get: Callable[..., Any], schedule: Mapping[str, Any]
) -> Callable[..., Any]:
    """Reuse the exact acquired schedule for row hydration without another CDN hop."""
    pinned = _PinnedJsonResponse(schedule)

    def get(url: str, params=None, headers=None, **kwargs: Any) -> Any:
        if str(url) == acquisition.wnba.WNBA_SCHEDULE_URL:
            return pinned
        return http_get(url, params=params, headers=headers, **kwargs)

    return get


def _source_diagnostic(exc: Exception) -> dict[str, Any] | None:
    """Return a secret-safe source receipt for typed WNBA acquisition failures."""
    code = str(getattr(exc, "code", "") or "").strip()
    detail = getattr(exc, "detail", None)
    if code != "WNBA_OFFICIAL_SOURCE_UNAVAILABLE" or not isinstance(detail, dict):
        return None
    raw_url = str(detail.get("url") or "").strip()
    parsed = urlsplit(raw_url) if raw_url else None
    raw_errors = detail.get("errors") if isinstance(detail.get("errors"), list) else []
    error_kinds: list[str] = []
    for raw in raw_errors:
        kind = str(raw).split(":", 1)[0].strip()
        if kind and kind not in error_kinds:
            error_kinds.append(kind)
    attempts = detail.get("attempts")
    return {
        "code": code,
        "host": parsed.netloc if parsed else None,
        "path": parsed.path if parsed else None,
        "attempts": int(attempts) if isinstance(attempts, int) else None,
        "error_kinds": error_kinds,
    }


def _existing_snapshot(db: Any, candidate: dict[str, Any], stat_type: str) -> bool:
    result = (
        db.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id")
        .eq("event_id", str(candidate["event_id"]))
        .eq("sport", "WNBA")
        .eq("player", str(candidate["player"]))
        .eq("stat_type", stat_type)
        .limit(1)
        .execute()
    )
    return bool(result.data or [])


def _flatten(players: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    return [
        (candidate, stat_type)
        for candidate in players
        for stat_type in acquisition.CORE_STATS
    ]


def acquire_wnba_forward_evidence_batch(
    req: WNBAForwardEvidenceRequest,
    *,
    db: Any,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result: dict[str, Any] = {
        "status": "COMPLETED",
        "sport": "WNBA",
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
        "source_diagnostics": [],
        "source_transport_mode": None,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
    try:
        # Schedule acquisition deliberately bypasses request-local response caching
        # so each typed retry is a real network attempt. If Render receives a
        # successful non-JSON body under browser-context headers, retry the same
        # official CDN URL with the minimal public contract used by the source.
        schedule, transport_mode = _request_schedule_with_same_source_fallback(http_get=http_get)
        result["source_transport_mode"] = transport_mode
        cached_get = _cached_http_get(http_get)
        hydration_get = _pin_schedule_payload(cached_get, schedule)
        players = acquisition._schedule_players(
            schedule,
            requested_date=req.requested_date,
            requested_timezone=req.requested_timezone,
            now=now,
            http_get=hydration_get,
        )
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [getattr(exc, "code", f"WNBA_FORWARD_DISCOVERY_FAILED:{type(exc).__name__}")]
        diagnostic = _source_diagnostic(exc)
        if diagnostic is not None:
            result["source_diagnostics"] = [diagnostic]
        return result

    flattened = _flatten(players)
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
                f"{candidate['player']}:{stat_type}:WNBA_EXISTING_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}"
            )
            continue

        try:
            raw = acquisition.auto_hydrate_prop_evidence(
                sport="WNBA",
                player=str(candidate["player"]),
                stat_type=str(stat_type),
                event_start_time=str(candidate["event_start_time"]),
                http_get=hydration_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_WNBA_OIDC_FORWARD_DISCOVERY",
                opponent=candidate.get("opponent"),
                canonical_event_id=str(candidate["event_id"]),
            )
            evidence = acquisition.RawPropEvidence.model_validate(raw)
            line = acquisition._candidate_line(evidence.game_log)
            row = acquisition.PickRequestRow(
                row_key=(
                    f"wnba-forward:{candidate['official_game_id']}:"
                    f"{candidate['player']}:{stat_type}:{line}"
                ),
                event_id=str(candidate["event_id"]),
                event_start_time=str(candidate["event_start_time"]),
                sport="WNBA",
                player=str(candidate["player"]),
                stat_type=stat_type,
                line=line,
                direction="MORE",
                evidence=evidence,
                source_type=acquisition.SOURCE_TYPE,
                platform=acquisition.PLATFORM,
                opponent=candidate.get("opponent"),
                source_capture_timestamp=now.isoformat(),
            )
            normalized = acquisition._validate_evidence(row, stat_type)
            snapshot_id, _fingerprint, snapshot = acquisition._snapshot_payload(row, normalized)
            snapshot["source_snapshot_id"] = snapshot_id
            result["hydrated"] += 1
            db.table("wow_prop_evidence_snapshots").upsert(
                snapshot, on_conflict="source_snapshot_id"
            ).execute()
            result["persisted"] += 1
        except (PropAutoHydrationError, acquisition.wnba.WNBAPropHydrationError) as exc:
            result["held"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:{getattr(exc, 'code', type(exc).__name__)}"
            )
            diagnostic = _source_diagnostic(exc)
            if diagnostic is not None and diagnostic not in result["source_diagnostics"]:
                result["source_diagnostics"].append(diagnostic)
        except Exception as exc:
            result["held"] += 1
            result["snapshot_write_failed"] += 1
            result["blockers"].append(
                f"{candidate['player']}:{stat_type}:WNBA_FORWARD_ACQUISITION_ERROR:{type(exc).__name__}"
            )

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] and result["persisted"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["held"]:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


def install_wnba_prop_forward_evidence_route(
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
        operation_id="acquireWowV17WnbaPropForwardEvidence",
    )
    def acquire(req: WNBAForwardEvidenceRequest) -> dict[str, Any]:
        return acquire_wnba_forward_evidence_batch(req, db=db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "ROUTE_PATH",
    "WNBAForwardEvidenceRequest",
    "acquire_wnba_forward_evidence_batch",
    "install_wnba_prop_forward_evidence_route",
]
