"""Free Live Tennis API schedule discovery for WOW V17.

Discovery/evidence infrastructure only.  The adapter enumerates bounded ATP/WTA
singles fixtures from the provider's FREE `/fixtures` endpoint.  Provider match
and player IDs are aliases only; no returned field owns WOW canonical identity,
sporting probability, exact-line authority, rank eligibility, publication, or
execution.

A fixture with `start_time=null` is preserved as a real DATE_ONLY state when its
`event_date` intersects the requested horizon.  It is never assigned an invented
clock time.  Downstream canonical identity/scoring remains fail-closed until an
actual scheduled start can be proven.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import requests

from v17 import cross_sport_winner_discovery as discovery

CAN_EXECUTE = False
PROVIDER = "LIVE_TENNIS_API"
PATH_ID = "LIVE_TENNIS_API"
API_ROOT = "https://api.livetennisapi.com/api/public/v1"
HTTP_TIMEOUT_SECONDS = 10.0
PAGE_LIMIT = 50
MAX_PAGES = 4

CREDENTIAL_UNCONFIGURED = "LIVE_TENNIS_API_CREDENTIAL_UNCONFIGURED"
TARGET_UNSUPPORTED = "LIVE_TENNIS_API_TARGET_UNSUPPORTED"
TRANSPORT_FAILED = "LIVE_TENNIS_API_TRANSPORT_FAILED"
INVALID_JSON = "LIVE_TENNIS_API_INVALID_JSON"
SCHEMA_INVALID = "LIVE_TENNIS_API_SCHEMA_INVALID"
PAGINATION_LIMIT_REACHED = "LIVE_TENNIS_API_PAGINATION_LIMIT_REACHED"

TOUR_BY_LEAGUE: Mapping[str, str] = {
    "ATP": "atp",
    "WTA": "wta",
}

_TOKEN_ENV_ALIASES = (
    "WOW_LIVE_TENNIS_API_KEY",
    "LIVE_TENNIS_API_KEY",
)


def _utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _token() -> tuple[str | None, str | None]:
    for alias in _TOKEN_ENV_ALIASES:
        value = str(os.environ.get(alias) or "").strip()
        if value:
            return alias, value
    return None, None


def _failure(
    code: str,
    *,
    attempted: bool,
    upstream_status: int | None = None,
) -> discovery.DiscoveryFeedError:
    acquisition = discovery.AcquisitionFeedResult(
        rows=(),
        provider_status=(
            discovery.PROVIDER_FAILED if attempted else discovery.PROVIDER_NOT_ATTEMPTED
        ),
        fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
        exhaustion_status=discovery.PROVIDER_PATHS_EXHAUSTED,
        blocker_code=code,
        primary_blocker_code=code,
        primary_path_id=PATH_ID,
        primary_path_state=(
            discovery.PATH_FAILED if attempted else discovery.PATH_NOT_ATTEMPTED
        ),
        fallback_path_state=discovery.PATH_NOT_APPLICABLE,
        primary_upstream_status=upstream_status,
    )
    return discovery.DiscoveryFeedError(code, acquisition=acquisition)


def tour_for_target(target: Any) -> str | None:
    family = str(getattr(target, "family", "") or "").upper()
    league = str(getattr(target, "league", "") or "").upper()
    regime = str(getattr(target, "regime", "") or "").upper()
    if family not in {"TENNIS", "ATP", "WTA"}:
        return None
    if regime not in {"", "REGULAR_SEASON"}:
        return None
    return TOUR_BY_LEAGUE.get(league if league else family)


def _date_intersects_horizon(event_date: date, start: datetime, end: datetime) -> bool:
    start_date = start.date()
    end_date = end.date()
    return start_date <= event_date <= end_date


def _alias_row(fixture: Mapping[str, Any], *, target: Any) -> dict[str, Any] | None:
    provider_event_id = fixture.get("id")
    player1 = str(fixture.get("player1_name") or "").strip()
    player2 = str(fixture.get("player2_name") or "").strip()
    if provider_event_id is None or not player1 or not player2:
        return None

    start_time = str(fixture.get("start_time") or "").strip() or None
    event_date = str(fixture.get("event_date") or "").strip() or None
    schedule_precision = "EXACT_START_TIME" if start_time else "DATE_ONLY_START_TIME_UNASSIGNED"

    # Deliberately omit id/event_id/official_event_id.  The provider ID is a
    # reconciliation alias and cannot become WOW canonical identity by presence.
    return {
        "provider_event_id": str(provider_event_id),
        "provider_event_id_type": "LIVE_TENNIS_MATCH_ALIAS",
        "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
        "league": str(getattr(target, "league", "") or "TENNIS").upper(),
        "tour": str(fixture.get("tour") or "").strip() or None,
        "player1_name": player1,
        "player2_name": player2,
        "player1_provider_id": (
            str(fixture.get("player1_id")) if fixture.get("player1_id") is not None else None
        ),
        "player2_provider_id": (
            str(fixture.get("player2_id")) if fixture.get("player2_id") is not None else None
        ),
        "commence_time": start_time,
        "event_date": event_date,
        "schedule_precision": schedule_precision,
        "tournament": str(fixture.get("tournament") or "").strip() or None,
        "round": str(fixture.get("round") or "").strip() or None,
        "round_code": str(fixture.get("round_code") or "").strip() or None,
        "surface": str(fixture.get("surface") or "").strip() or None,
        "status": str(fixture.get("status") or "").strip() or None,
        "discovery_provider": PROVIDER,
        "source_provider": PROVIDER,
        "research_only": True,
        "prediction_authority": False,
        "exact_line_authority": False,
        "canonical_identity_authority": False,
        "settlement_authority": False,
        "can_execute": False,
    }


def fetch_target(
    target: Any,
    *,
    started: datetime,
    horizon_hours: int,
    requester: Callable[..., Any] | None = None,
) -> discovery.AcquisitionFeedResult:
    """Fetch one ATP/WTA singles target through the FREE fixtures endpoint.

    Pagination is bounded to four 50-row pages (200 fixtures per tour).  If a
    fourth full page is reached, fail typed rather than silently declaring board
    coverage complete.  Exact start timestamps are filtered to the UTC horizon;
    date-only fixtures are retained only when their published event date
    intersects that horizon.
    """
    tour = tour_for_target(target)
    if not tour:
        raise _failure(TARGET_UNSUPPORTED, attempted=False)

    _alias, token = _token()
    if not token:
        raise _failure(CREDENTIAL_UNCONFIGURED, attempted=False)

    start = started.astimezone(timezone.utc)
    end = start + timedelta(hours=max(1, min(int(horizon_hours), 168)))
    get = requester or requests.get
    rows: list[Mapping[str, Any]] = []

    for page in range(MAX_PAGES):
        offset = page * PAGE_LIMIT
        try:
            response = get(
                f"{API_ROOT}/fixtures",
                headers={"X-API-Key": token},
                params={
                    "tour": tour,
                    "draw": "singles",
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise _failure(TRANSPORT_FAILED, attempted=True) from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise _failure(
                f"LIVE_TENNIS_API_HTTP_{status or 'UNKNOWN'}",
                attempted=True,
                upstream_status=status or None,
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise _failure(INVALID_JSON, attempted=True, upstream_status=status) from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
            raise _failure(SCHEMA_INVALID, attempted=True, upstream_status=status)

        page_data = payload["data"]
        for fixture in page_data:
            if not isinstance(fixture, Mapping):
                continue
            exact_start = _utc(fixture.get("start_time"))
            if exact_start is not None:
                if exact_start < start or exact_start > end:
                    continue
            else:
                published_date = _date(fixture.get("event_date"))
                if published_date is None or not _date_intersects_horizon(published_date, start, end):
                    continue
            row = _alias_row(fixture, target=target)
            if row is not None:
                rows.append(row)

        if len(page_data) < PAGE_LIMIT:
            break
        if page == MAX_PAGES - 1:
            raise _failure(PAGINATION_LIMIT_REACHED, attempted=True, upstream_status=status)

    return discovery.AcquisitionFeedResult(
        rows=tuple(rows),
        provider_status=discovery.PROVIDER_SUCCEEDED,
        fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
        exhaustion_status=discovery.PATHS_NOT_EXHAUSTED,
        primary_path_id=PATH_ID,
        primary_path_state=discovery.succeeded_path_state(rows),
        fallback_path_state=discovery.PATH_NOT_APPLICABLE,
    )


__all__ = [
    "API_ROOT",
    "CAN_EXECUTE",
    "CREDENTIAL_UNCONFIGURED",
    "HTTP_TIMEOUT_SECONDS",
    "INVALID_JSON",
    "MAX_PAGES",
    "PAGE_LIMIT",
    "PAGINATION_LIMIT_REACHED",
    "PATH_ID",
    "PROVIDER",
    "SCHEMA_INVALID",
    "TARGET_UNSUPPORTED",
    "TOUR_BY_LEAGUE",
    "TRANSPORT_FAILED",
    "fetch_target",
    "tour_for_target",
]
