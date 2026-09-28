"""Free football-data.org soccer schedule discovery for WOW V17.

This adapter is discovery/evidence infrastructure only.  It may enumerate a
bounded set of soccer fixtures from competitions included in football-data.org's
free tier, but it never creates sporting probability, canonical event identity,
market probability, rank eligibility, terminal authority, or execution rights.

Provider match IDs are deliberately retained as aliases only.  A later governed
identity resolver must bind participant names + kickoff + competition to WOW's
canonical event identity before any scorer can run.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import requests

from v17 import cross_sport_winner_discovery as discovery

CAN_EXECUTE = False
PROVIDER = "FOOTBALL_DATA_ORG"
PATH_ID = "FOOTBALL_DATA_ORG"
API_ROOT = "https://api.football-data.org/v4"
HTTP_TIMEOUT_SECONDS = 10.0

CREDENTIAL_UNCONFIGURED = "FOOTBALL_DATA_CREDENTIAL_UNCONFIGURED"
TARGET_UNSUPPORTED = "FOOTBALL_DATA_TARGET_UNSUPPORTED"
TRANSPORT_FAILED = "FOOTBALL_DATA_TRANSPORT_FAILED"
INVALID_JSON = "FOOTBALL_DATA_INVALID_JSON"
SCHEMA_INVALID = "FOOTBALL_DATA_SCHEMA_INVALID"

# WOW league identity -> football-data.org v4 competition code.  Defaults are
# restricted to competitions listed in the provider's free-tier coverage.  Do
# not guess paid/non-free competitions.  Operators may narrow this set but not
# widen it without a governed source-review change.
DEFAULT_COMPETITION_CODES: Mapping[str, str] = {
    "EPL": "PL",
    "FRA1": "FL1",
    "GER1": "BL1",
    "ESP1": "PD",
    "ITA1": "SA",
    "UEFACHAMP": "CL",
    "FIFA": "WC",
    "UEFAEURO": "EC",
}

_TOKEN_ENV_ALIASES = (
    "WOW_FOOTBALL_DATA_API_TOKEN",
    "FOOTBALL_DATA_API_TOKEN",
    "FOOTBALL_DATA_TOKEN",
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


def competition_code_for_target(target: Any) -> str | None:
    if str(getattr(target, "family", "") or "").upper() != "SOCCER":
        return None
    if str(getattr(target, "regime", "") or "").upper() not in {"", "REGULAR_SEASON"}:
        return None
    return DEFAULT_COMPETITION_CODES.get(
        str(getattr(target, "league", "") or "").upper()
    )


def _alias_row(match: Mapping[str, Any], *, target: Any, competition_code: str) -> dict[str, Any] | None:
    provider_event_id = match.get("id")
    utc_date = str(match.get("utcDate") or "").strip()
    home = match.get("homeTeam") if isinstance(match.get("homeTeam"), Mapping) else {}
    away = match.get("awayTeam") if isinstance(match.get("awayTeam"), Mapping) else {}
    home_name = str(home.get("name") or home.get("shortName") or "").strip()
    away_name = str(away.get("name") or away.get("shortName") or "").strip()
    if provider_event_id is None or not utc_date or not home_name or not away_name:
        return None

    # Deliberately omit id/event_id/official_event_id.  The provider's match ID
    # is useful reconciliation evidence, not WOW canonical identity.
    return {
        "provider_event_id": str(provider_event_id),
        "provider_event_id_type": "FOOTBALL_DATA_MATCH_ALIAS",
        "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
        "provider_competition_code": competition_code,
        "league": str(getattr(target, "league", "") or "SOCCER").upper(),
        "home_team": home_name,
        "away_team": away_name,
        "commence_time": utc_date,
        "status": str(match.get("status") or "").strip(),
        "provider_last_updated": str(match.get("lastUpdated") or "").strip() or None,
        "discovery_provider": PROVIDER,
        "source_provider": PROVIDER,
        "research_only": True,
        "prediction_authority": False,
        "exact_line_authority": False,
        "canonical_identity_authority": False,
        "can_execute": False,
    }


def fetch_target(
    target: Any,
    *,
    started: datetime,
    horizon_hours: int,
    requester: Callable[..., Any] | None = None,
) -> discovery.AcquisitionFeedResult:
    """Fetch one configured soccer competition through the free schedule API.

    One provider request is made per target.  The provider's date-only filter is
    narrowed again locally to the exact UTC discovery horizon.  This keeps the
    free-tier request budget bounded and prevents a date-boundary response from
    widening the governed slate.
    """
    competition_code = competition_code_for_target(target)
    if not competition_code:
        raise _failure(TARGET_UNSUPPORTED, attempted=False)

    _alias, token = _token()
    if not token:
        raise _failure(CREDENTIAL_UNCONFIGURED, attempted=False)

    start = started.astimezone(timezone.utc)
    end = start + timedelta(hours=max(1, min(int(horizon_hours), 168)))
    get = requester or requests.get
    try:
        response = get(
            f"{API_ROOT}/competitions/{competition_code}/matches",
            headers={"X-Auth-Token": token},
            params={
                "dateFrom": start.date().isoformat(),
                "dateTo": end.date().isoformat(),
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise _failure(TRANSPORT_FAILED, attempted=True) from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise _failure(f"FOOTBALL_DATA_HTTP_{status or 'UNKNOWN'}", attempted=True, upstream_status=status or None)

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise _failure(INVALID_JSON, attempted=True, upstream_status=status) from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("matches"), list):
        raise _failure(SCHEMA_INVALID, attempted=True, upstream_status=status)

    rows: list[Mapping[str, Any]] = []
    for match in payload["matches"]:
        if not isinstance(match, Mapping):
            continue
        kickoff = _utc(match.get("utcDate"))
        if kickoff is None or kickoff < start or kickoff > end:
            continue
        row = _alias_row(match, target=target, competition_code=competition_code)
        if row is not None:
            rows.append(row)

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
    "DEFAULT_COMPETITION_CODES",
    "HTTP_TIMEOUT_SECONDS",
    "INVALID_JSON",
    "PATH_ID",
    "PROVIDER",
    "SCHEMA_INVALID",
    "TARGET_UNSUPPORTED",
    "TRANSPORT_FAILED",
    "competition_code_for_target",
    "fetch_target",
]
