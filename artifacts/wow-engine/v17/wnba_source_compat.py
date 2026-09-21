"""Current-source transport compatibility for official WNBA evidence.

WNBA Stats' 2026 LeagueGameLog endpoint is query-order-sensitive. The governed
hydrator already requires official sources and fails closed; this shim changes
only HTTP request ordering/headers so the same official endpoint remains
reachable. It does not alter event identity, history rows, injury status,
probability, calibration, ranking, or terminal authority.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

import wnba_prop_auto_hydration as wnba

_STATE_ATTR = "_wow_v17_current_source_compat"
_LEAGUE_GAME_LOG_ORDER = (
    "LeagueID",
    "Season",
    "SeasonType",
    "PlayerOrTeam",
    "Counter",
    "Direction",
    "Sorter",
    "DateFrom",
    "DateTo",
)


def _ordered_params(url: str, params: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    original = dict(params or {})
    if not str(url).endswith("/leaguegamelog"):
        return original
    ordered: dict[str, Any] = {}
    for key in _LEAGUE_GAME_LOG_ORDER:
        if key in original:
            ordered[key] = original[key]
    for key, value in original.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _official_headers(url: str, headers: Optional[Mapping[str, str]]) -> dict[str, str]:
    merged = dict(headers or {})
    if str(url).startswith(wnba.WNBA_STATS_BASE):
        # These mirror normal browser/client cache semantics without changing
        # authority or introducing an alternate data source.
        merged.setdefault("Cache-Control", "no-cache")
        merged.setdefault("Pragma", "no-cache")
        merged.setdefault("Connection", "keep-alive")
    return merged


def install_wnba_current_source_compat() -> bool:
    current = getattr(wnba, "_request", None)
    if not callable(current):
        return False
    if getattr(current, _STATE_ATTR, False):
        return True

    original: Callable[..., Any] = current

    def compatible_request(
        url: str,
        *,
        http_get: Callable[..., Any],
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        expect_json: bool = True,
    ) -> Any:
        return original(
            url,
            http_get=http_get,
            params=_ordered_params(url, params),
            headers=_official_headers(url, headers),
            expect_json=expect_json,
        )

    setattr(compatible_request, _STATE_ATTR, True)
    wnba._request = compatible_request
    return True


__all__ = [
    "install_wnba_current_source_compat",
]
