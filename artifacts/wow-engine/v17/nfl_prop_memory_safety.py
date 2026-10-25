"""Memory-safe nflverse history cache for interactive NFL prop hydration.

The canonical NFL hydrator historically cached whole season CSV files as
``list[dict]`` objects.  That representation retains every source column and a
per-row dictionary for the full current and prior seasons, which is expensive on
the 512 MiB production web process.  This overlay preserves the same source
bytes, digest, TTL, fail-closed behavior, and ``row.get`` values used by the
canonical hydrator while retaining only the fields that ``_history`` reads.

This module is acquisition/resource plumbing only.  It does not create or alter
sporting probabilities, fitted artifacts, calibration, terminal reduction, or
execution authority.
"""
from __future__ import annotations

import csv
import hashlib
import io
import threading
from typing import Any, Callable, NamedTuple

import nfl_prop_auto_hydration as nfl

_STATE_KEY = "_wow_v17_compact_nflverse_cache_installed"
_CACHE_LOCK = threading.RLock()


class _CompactNFLRow(NamedTuple):
    player_name: str
    season: float
    week: float
    game_id: str
    team: str
    opponent_team: str
    position: str
    passing_yards: float
    attempts: float
    rushing_yards: float
    carries: float
    receiving_yards: float
    targets: float
    rushing_tds: float
    receiving_tds: float
    special_teams_tds: float

    def get(self, key: str, default: Any = None) -> Any:
        if key in {"player_display_name", "player_name"}:
            return self.player_name
        return getattr(self, key, default)


# Same live-cache contract as the canonical hydrator, but compact rows retain
# only fields consumed by nfl_prop_auto_hydration._history.
_COMPACT_CACHE: dict[int, tuple[float, tuple[_CompactNFLRow, ...], str]] = {}


def _compact_rows(content: bytes) -> tuple[_CompactNFLRow, ...]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[_CompactNFLRow] = []
    for row in reader:
        player_name = str(row.get("player_display_name") or row.get("player_name") or "")
        rows.append(
            _CompactNFLRow(
                player_name=player_name,
                season=nfl._num(row, "season"),
                week=nfl._num(row, "week"),
                game_id=str(row.get("game_id") or ""),
                team=str(row.get("team") or ""),
                opponent_team=str(row.get("opponent_team") or ""),
                position=str(row.get("position") or ""),
                passing_yards=nfl._num(row, "passing_yards"),
                attempts=nfl._num(row, "attempts"),
                rushing_yards=nfl._num(row, "rushing_yards"),
                carries=nfl._num(row, "carries"),
                receiving_yards=nfl._num(row, "receiving_yards"),
                targets=nfl._num(row, "targets"),
                rushing_tds=nfl._num(row, "rushing_tds"),
                receiving_tds=nfl._num(row, "receiving_tds"),
                special_teams_tds=nfl._num(row, "special_teams_tds"),
            )
        )
    if not rows:
        raise nfl.NFLPropHydrationError(
            "NFL_PROP_HISTORY_EMPTY",
            "nflverse season payload was empty",
        )
    return tuple(rows)


def _cached_compact_nflverse_rows(
    season: int,
    *,
    http_get: Callable[..., Any],
    now_ts: float,
) -> tuple[tuple[_CompactNFLRow, ...], str]:
    use_cache = bool(
        http_get is nfl.httpx.get
        or getattr(http_get, "_wow_nflverse_compact_cache_eligible", False)
    )
    url = nfl.NFLVERSE_URL.format(season=season)

    if use_cache:
        # Preserve the canonical single-flight guarantee and TTL. Expired seasons
        # are dropped eagerly so a sparse-player lookup cannot retain old compact
        # cohorts beyond the live evidence window.
        with _CACHE_LOCK:
            for cached_season, cached in list(_COMPACT_CACHE.items()):
                if now_ts - cached[0] >= nfl.CACHE_TTL_SECONDS:
                    _COMPACT_CACHE.pop(cached_season, None)
            cached = _COMPACT_CACHE.get(season)
            if cached and now_ts - cached[0] < nfl.CACHE_TTL_SECONDS:
                return cached[1], cached[2]

            content = nfl._request(url, http_get=http_get, json_expected=False)
            digest = hashlib.sha256(content).hexdigest()
            rows = _compact_rows(content)
            _COMPACT_CACHE[season] = (now_ts, rows, digest)
            return rows, digest

    content = nfl._request(url, http_get=http_get, json_expected=False)
    digest = hashlib.sha256(content).hexdigest()
    return _compact_rows(content), digest


def install_nfl_prop_memory_safety() -> bool:
    """Replace only the internal season-cache representation, once per process."""
    if getattr(nfl, _STATE_KEY, False):
        return True
    with _CACHE_LOCK:
        # Startup normally installs before hydration, but clearing the legacy
        # cache also makes a hot-install fail safe by releasing the heavyweight
        # representation before the compact path is used.
        nfl._CSV_CACHE.clear()
        _COMPACT_CACHE.clear()
        nfl._cached_nflverse_rows = _cached_compact_nflverse_rows
        setattr(nfl, _STATE_KEY, True)
    return True


__all__ = [
    "install_nfl_prop_memory_safety",
]
