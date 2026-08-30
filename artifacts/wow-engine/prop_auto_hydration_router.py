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
from wnba_prop_auto_hydration import (
    WNBAPropHydrationError,
    hydrate_wnba_prop_evidence,
    PROVIDER_ID as WNBA_PROVIDER,
)


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
            result = hydrate_wnba_prop_evidence(
                player=player,
                stat_type=stat_type,
                event_start_time=event_start_time,
                http_get=http_get,
                now=now,
                source_capture_timestamp=source_capture_timestamp,
                source_label=source_label,
                opponent=opponent,
            )
        except WNBAPropHydrationError as exc:
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
