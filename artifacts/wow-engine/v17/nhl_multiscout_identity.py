"""Resolve Multiscout NHL provider aliases to official league event IDs.

Odds-provider / ESPN-style discovery IDs remain aliases. Canonical identity is
assigned only after an exact participant + start-time match against the
first-party NHL club schedule already used by WOW research.

This module has no sporting-probability, market-price, calibration, ranking, or
execution authority.
"""
from __future__ import annotations

from typing import Any, Mapping

try:
    from v17.nhl_event_identity import NHLEventIdentityError, resolve_nhl_current_event_identity
except ModuleNotFoundError:  # pragma: no cover - direct script import
    from nhl_event_identity import NHLEventIdentityError, resolve_nhl_current_event_identity

CAN_EXECUTE = False
NHL_SPORT_KEY = "icehockey_nhl"


def resolve_multiscout_nhl_identity(
    event_identity: Mapping[str, Any],
    *,
    session: Any = None,
    timeout: int = 12,
) -> dict[str, Any]:
    """Return a Multiscout identity dict with provider and canonical IDs separated.

    On success:
      - ``provider_event_id`` retained from the discovery source
      - ``official_event_id`` becomes the 10-digit NHL league game id
      - ``official_event_identity_verified=True``
      - ``canonical_identity_status=CANONICAL_RESOLVED``

    On failure the row stays unverified with a typed blocker; never forges a
    verified mapping from a provider UUID alone.
    """
    out = dict(event_identity)
    sport = str(out.get("sport_key") or "").strip().lower()
    if sport != NHL_SPORT_KEY:
        return out

    provider_event_id = str(
        out.get("provider_event_id") or out.get("official_event_id") or ""
    ).strip()
    if provider_event_id:
        out["provider_event_id"] = provider_event_id
    out.setdefault("provider_event_source", out.get("provider_event_source") or "ODDS_API")

    home = out.get("home_team")
    away = out.get("away_team")
    start = out.get("commence_time") or out.get("event_start_utc")
    kwargs: dict[str, Any] = {
        "event_start_time": str(start or ""),
        "home_team": str(home or ""),
        "away_team": str(away or ""),
        "timeout": timeout,
    }
    if session is not None:
        kwargs["session"] = session

    try:
        resolution = resolve_nhl_current_event_identity(**kwargs)
    except NHLEventIdentityError as exc:
        out["official_event_identity_verified"] = False
        out["canonical_event_id"] = None
        out["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        out["canonical_identity_blocker"] = str(exc.code)
        out["prediction_authority"] = False
        out["can_execute"] = False
        return out
    except Exception as exc:  # noqa: BLE001 - preserve fail-closed Multiscout path
        out["official_event_identity_verified"] = False
        out["canonical_event_id"] = None
        out["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        out["canonical_identity_blocker"] = type(exc).__name__
        out["prediction_authority"] = False
        out["can_execute"] = False
        return out

    canonical_event_id = str(resolution.get("event_id") or "").strip()
    if not (canonical_event_id.isdigit() and len(canonical_event_id) == 10):
        out["official_event_identity_verified"] = False
        out["canonical_event_id"] = None
        out["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        out["canonical_identity_blocker"] = "NHL_CANONICAL_EVENT_ID_INVALID"
        out["prediction_authority"] = False
        out["can_execute"] = False
        return out

    # Provider discovery id stays distinct from the league fixture id.
    out["official_event_id"] = canonical_event_id
    out["canonical_event_id"] = canonical_event_id
    out["official_event_identity_verified"] = True
    out["canonical_identity_status"] = "CANONICAL_RESOLVED"
    out["canonical_identity_source"] = str(
        resolution.get("identity_provider") or "NHL_PUBLIC_WEB_API"
    )
    out["canonical_identity_resolution"] = "OFFICIAL_NHL_SCHEDULE_EXACT_MATCH"
    out["canonical_identity_verified_at"] = resolution.get("identity_verified_at")
    out["canonical_identity_event_start_utc"] = resolution.get("event_start_time")
    out["canonical_identity_receipt"] = {
        "identity_provider": resolution.get("identity_provider"),
        "identity_source": resolution.get("identity_source"),
        "identity_verified_at": resolution.get("identity_verified_at"),
        "event_id": canonical_event_id,
        "event_start_time": resolution.get("event_start_time"),
        "home_team": resolution.get("home_team"),
        "away_team": resolution.get("away_team"),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    out["canonical_identity_market_features_used"] = False
    out["prediction_authority"] = False
    out["can_execute"] = False
    return out


__all__ = [
    "CAN_EXECUTE",
    "NHL_SPORT_KEY",
    "resolve_multiscout_nhl_identity",
]
