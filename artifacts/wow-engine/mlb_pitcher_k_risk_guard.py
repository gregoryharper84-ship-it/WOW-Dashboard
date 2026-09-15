"""Governance-safe risk guards for MLB pitcher strikeout props.

This module does not create or alter sporting probability. It derives two
orthogonal diagnostics from evidence the V17 prop lane already owns:

1. current-role/workload compatibility for the starter-only fitted K model;
2. a severe low-K tail contradiction for low-line MORE selections.

The first can fail closed before scoring when recent official usage shows the
pitcher is functioning as an opener/reliever rather than a normal starter. The
second is a qualification ceiling only: it never changes raw/calibrated
probability or numerical bounds.

can_execute = false is preserved by callers; this module has no execution path.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Callable, Iterable

RECENT_ROLE_WINDOW = 6
MIN_ROLE_OBSERVATIONS = 4
SHORT_APPEARANCE_MAX_OUTS = 6  # <= 2.0 IP: opener/short-relief shaped usage
STARTER_MIN_START_SHARE = 0.50
STARTER_MAX_SHORT_SHARE = 0.50
STARTER_MIN_MEAN_BF = 12.0

ROLE_COMPATIBLE = "STARTER_MODEL_COMPATIBLE"
ROLE_INCOMPATIBLE = "STARTER_MODEL_INCOMPATIBLE"
ROLE_SAMPLE_THIN = "RECENT_ROLE_SAMPLE_THIN"

LOW_LINE_MORE_MAX = 3.5
LOW_K_VALUE_MAX = 1.0
LOW_K_TAIL_MIN_STARTS = 7
LOW_K_TAIL_RATE_THRESHOLD = 0.30
TAG_LOW_LINE_MORE_TAIL_CONTRADICTION = "RECENT_LOW_K_TAIL_CONTRADICTION"


def build_recent_role_profile(
    season_splits: Iterable[tuple[int, dict[str, Any]]],
    *,
    event_start: datetime,
    outs_from_ip: Callable[[Any], int],
    int_value: Callable[..., int],
) -> dict[str, Any]:
    """Summarize the pitcher's most recent official pitching appearances.

    Unlike the fitted strikeout history, which intentionally uses prior starts,
    this diagnostic must inspect *all* recent appearances so an opener/reliever
    conversion cannot disappear merely because `gamesStarted == 0`.

    Some provider payloads omit ``gamesStarted`` on a split. Missing is treated
    as unknown, never silently converted to relief usage. Outs/BF can still prove
    a short-workload regime independently.
    """
    appearances: list[tuple[str, dict[str, Any]]] = []
    for split_season, split in season_splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat") if isinstance(split.get("stat"), dict) else None
        if not isinstance(stat, dict):
            continue
        date_value = str(split.get("date") or "")
        try:
            game_date = datetime.fromisoformat(date_value).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue
        ip = stat.get("inningsPitched")
        try:
            outs = outs_from_ip(ip)
        except Exception:
            continue
        raw_games_started = stat.get("gamesStarted")
        games_started = int_value(raw_games_started) if raw_games_started is not None else None
        batters_faced = int_value(stat.get("battersFaced"))
        pitches = int_value(stat.get("numberOfPitches"))
        appearances.append(
            (
                date_value,
                {
                    "date": date_value,
                    "season": split_season,
                    "games_started": games_started,
                    "outs": outs,
                    "batters_faced": batters_faced,
                    "pitches": pitches,
                },
            )
        )

    appearances.sort(key=lambda item: item[0], reverse=True)
    recent = [row for _, row in appearances[:RECENT_ROLE_WINDOW]]
    n = len(recent)
    known_start_rows = [row for row in recent if row["games_started"] is not None]
    starts = sum(1 for row in known_start_rows if row["games_started"] >= 1)
    short = sum(1 for row in recent if row["outs"] <= SHORT_APPEARANCE_MAX_OUTS)
    start_share = starts / len(known_start_rows) if known_start_rows else None
    short_share = short / n if n else None
    outs_values = [float(row["outs"]) for row in recent]
    bf_values = [float(row["batters_faced"]) for row in recent if row["batters_faced"] > 0]
    pitch_values = [float(row["pitches"]) for row in recent if row["pitches"] > 0]
    mean_outs = (sum(outs_values) / len(outs_values)) if outs_values else None
    median_outs = median(outs_values) if outs_values else None
    mean_bf = (sum(bf_values) / len(bf_values)) if bf_values else None
    mean_pitches = (sum(pitch_values) / len(pitch_values)) if pitch_values else None

    if n < MIN_ROLE_OBSERVATIONS:
        status = ROLE_SAMPLE_THIN
    else:
        incompatible = bool(
            (start_share is not None and start_share < STARTER_MIN_START_SHARE)
            or (short_share is not None and short_share >= STARTER_MAX_SHORT_SHARE)
            or (mean_bf is not None and mean_bf < STARTER_MIN_MEAN_BF)
        )
        status = ROLE_INCOMPATIBLE if incompatible else ROLE_COMPATIBLE

    return {
        "status": status,
        "window": RECENT_ROLE_WINDOW,
        "appearances_found": n,
        "start_signal_observations": len(known_start_rows),
        "starts": starts,
        "start_share": start_share,
        "short_appearance_count": short,
        "short_appearance_share": short_share,
        "short_appearance_max_outs": SHORT_APPEARANCE_MAX_OUTS,
        "mean_outs": mean_outs,
        "median_outs": median_outs,
        "mean_batters_faced": mean_bf,
        "mean_pitches": mean_pitches,
        "recent_appearances": recent,
        "source_semantics": "OFFICIAL_ALL_RECENT_PITCHING_APPEARANCES_NOT_STARTS_ONLY",
    }


def low_line_more_tail_risk(
    game_log: Iterable[float],
    *,
    line: float,
    direction: str,
) -> dict[str, Any]:
    """Return a direction-aware qualification advisory for low K overs.

    This is deliberately *not* a probability model. A severe frequency of 0/1-K
    starts is used only as contradiction evidence against advertising a low-line
    MORE as a high-confidence selection. The fitted probability package remains
    unchanged.
    """
    values: list[float] = []
    for value in game_log:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            values.append(parsed)

    n = len(values)
    zero_count = sum(1 for value in values if value == 0)
    low_count = sum(1 for value in values if value <= LOW_K_VALUE_MAX)
    zero_rate = zero_count / n if n else None
    low_rate = low_count / n if n else None
    applies = (
        str(direction or "").strip().upper() == "MORE"
        and float(line) <= LOW_LINE_MORE_MAX
        and n >= LOW_K_TAIL_MIN_STARTS
        and low_rate is not None
        and low_rate >= LOW_K_TAIL_RATE_THRESHOLD
    )
    return {
        "flag": TAG_LOW_LINE_MORE_TAIL_CONTRADICTION if applies else None,
        "applies": applies,
        "n_prior_starts": n,
        "line": float(line),
        "direction": str(direction or "").strip().upper(),
        "zero_k_count": zero_count,
        "zero_k_rate": zero_rate,
        "zero_or_one_k_count": low_count,
        "zero_or_one_k_rate": low_rate,
        "low_k_value_max": LOW_K_VALUE_MAX,
        "low_line_more_max": LOW_LINE_MORE_MAX,
        "tail_rate_threshold": LOW_K_TAIL_RATE_THRESHOLD,
        "semantics": "QUALIFICATION_CONTRADICTION_ONLY_DOES_NOT_MUTATE_PROBABILITY",
    }
