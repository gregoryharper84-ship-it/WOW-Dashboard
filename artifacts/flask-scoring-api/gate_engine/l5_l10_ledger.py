"""
l5_l10_ledger.py
Calculate L5/L10 average, median, hit rate, season average, and splits.
Never fake-fills missing data. Returns FAILED status if insufficient games.
"""
from __future__ import annotations

import statistics
from typing import Any

from .labels import DataStatus


MIN_GAMES_L5  = 4
MIN_GAMES_L10 = 6


def run(row: dict[str, Any], game_log: list[float] | None = None,
        season_log: list[float] | None = None) -> dict[str, Any]:
    """
    game_log    — chronological list of stat values (most recent last)
    season_log  — full season log (superset of game_log, or None)

    Gate result at row["gates"]["l5_l10_ledger"].
    """
    line = row.get("line")

    if game_log is None:
        result = _no_data_result("NO_GAME_LOG_PROVIDED")
        row["gates"]["l5_l10_ledger"] = result
        row["blockers"].append("L10:NO_GAME_LOG_PROVIDED")
        return row

    clean = [float(v) for v in game_log if v is not None]

    if len(clean) < MIN_GAMES_L5:
        result = _no_data_result(
            f"INSUFFICIENT_GAMES:{len(clean)}_of_{MIN_GAMES_L5}_required"
        )
        row["gates"]["l5_l10_ledger"] = result
        row["blockers"].append(f"L10:SMALL_SAMPLE:{len(clean)}")
        return row

    l5  = clean[-5:] if len(clean) >= 5 else clean
    l10 = clean[-10:] if len(clean) >= 10 else clean

    l5_avg    = round(statistics.mean(l5), 2)
    l10_avg   = round(statistics.mean(l10), 2)
    l5_median = round(statistics.median(l5), 2)
    l10_median= round(statistics.median(l10), 2)

    season_avg = None
    if season_log:
        season_clean = [float(v) for v in season_log if v is not None]
        if season_clean:
            season_avg = round(statistics.mean(season_clean), 2)

    l5_hit_rate  = _hit_rate(l5,  line, row.get("direction", "MORE"))
    l10_hit_rate = _hit_rate(l10, line, row.get("direction", "MORE"))

    has_l10 = len(clean) >= 10
    data_status = (
        DataStatus.RETRIEVED.value if has_l10
        else DataStatus.RECONSTRUCTED.value
    )

    result = {
        "passed":         True,
        "data_status":    data_status,
        "games_available": len(clean),
        "l5_avg":         l5_avg,
        "l5_median":      l5_median,
        "l5_hit_rate":    l5_hit_rate,
        "l10_avg":        l10_avg,
        "l10_median":     l10_median,
        "l10_hit_rate":   l10_hit_rate,
        "season_avg":     season_avg,
        "l5_games":       l5,
        "l10_games":      l10,
        "small_sample_warning": not has_l10,
    }

    row["gates"]["l5_l10_ledger"] = result
    return row


def _hit_rate(games: list[float], line: float | None, direction: str) -> float | None:
    if line is None or not games:
        return None
    direction = direction.upper()
    hits = sum(
        1 for g in games
        if (direction in ("MORE", "OVER") and g > line)
        or (direction in ("LESS", "UNDER") and g < line)
    )
    return round(hits / len(games), 3)


def _no_data_result(reason: str) -> dict[str, Any]:
    return {
        "passed":               False,
        "data_status":          DataStatus.FAILED.value,
        "reason":               reason,
        "games_available":      0,
        "l5_avg":               None,
        "l5_median":            None,
        "l5_hit_rate":          None,
        "l10_avg":              None,
        "l10_median":           None,
        "l10_hit_rate":         None,
        "season_avg":           None,
        "l5_games":             [],
        "l10_games":            [],
        "small_sample_warning": True,
    }
