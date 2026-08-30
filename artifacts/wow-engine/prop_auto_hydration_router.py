"""Sport-aware automatic prop hydration router.

Only routes with reviewed automatic evidence providers are dispatched. Provider
identity is returned separately for telemetry and is never caller-controlled.
"""
from __future__ import annotations

from typing import Any, Callable, Optional
from datetime import datetime

import httpx

from prop_auto_hydration import (
    PropAutoHydrationError,
    auto_hydrate_prop_evidence as _hydrate_mlb,
    AUTO_HYDRATION_PROVIDER as MLB_PROVIDER,
)
import wnba_prop_auto_hydration as _wnba
from wnba_injury_status import WNBAInjuryStatusError, availability_from_report as _strict_availability

WNBA_PROVIDER = _wnba.PROVIDER_ID


def _strict_availability_adapter(*args, **kwargs):
    try:
        return _strict_availability(*args, **kwargs)
    except WNBAInjuryStatusError as exc:
        raise _wnba.WNBAPropHydrationError(exc.code, str(exc), detail=exc.detail) from exc


# Canonical router owns the production WNBA hydration path. Replace the legacy
# broad-scan parser at import time so no later-player designation can be
# attributed to the target player. This is fail-closed and covered in CI.
_wnba._availability_from_report = _strict_availability_adapter


def provider_for_sport(sport: str) -> str:
    return WNBA_PROVIDER if str(sport or "").strip().upper() == "WNBA" else MLB_PROVIDER


def auto_hydrate_prop_evidence(
    *,
    sport: str,
    player: str,
    stat_type: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
    opponent: Optional[str] = None,
) -> dict[str, Any]:
    normalized_sport = str(sport or "").strip().upper()
    if normalized_sport == "WNBA":
        try:
            result = _wnba.hydrate_wnba_prop_evidence(
                player=player,
                stat_type=stat_type,
                event_start_time=event_start_time,
                http_get=http_get,
                now=now,
                source_capture_timestamp=source_capture_timestamp,
                source_label=source_label,
                opponent=opponent,
            )
        except _wnba.WNBAPropHydrationError as exc:
            raise PropAutoHydrationError(exc.code, str(exc), detail=exc.detail) from exc
        result = dict(result)
        result.pop("hydration_provider", None)
        return result

    return _hydrate_mlb(
        sport=normalized_sport,
        player=player,
        stat_type=stat_type,
        event_start_time=event_start_time,
        http_get=http_get,
        now=now,
        source_capture_timestamp=source_capture_timestamp,
        source_label=source_label,
    )
