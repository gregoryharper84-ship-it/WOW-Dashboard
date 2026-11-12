#!/usr/bin/env python3
"""Offline PBP-vs-weekly count *signal* comparison; never produces settled labels.

nflfastR's pass_attempt includes sacks and PBP flags can differ from official
box-score semantics. This diagnostic exposes disagreements by declared stat
rather than treating a PBP flag sum or absence as a verified market outcome.
"""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import io
from pathlib import Path

import pandas as pd

from acquire_nfl_count_prop_sources import OFFICIAL_SIZE_BYTES
from audit_nfl_count_prop_pbp_presence import PBP_GZ_SHA256
from train_nfl_missing_prop_candidates import SEASONS

CAN_EXECUTE = False
FLAGS = ("pass_attempt", "complete_pass", "pass_touchdown", "rush_attempt",
         "sack", "qb_spike", "two_point_attempt")
FIELDS = ("game_id", "season", "week", "season_type", "play_id", "play_type",
          "passer_player_id", "receiver_player_id", "rusher_player_id", *FLAGS)
SOURCE_ROLES = {
    "attempts": frozenset(("QB",)),
    "completions": frozenset(("QB",)),
    "passing_tds": frozenset(("QB",)),
    "receptions": frozenset(("RB", "WR", "TE")),
    "targets": frozenset(("RB", "WR", "TE")),
    "carries": frozenset(("QB", "RB", "WR", "TE")),
}


def add_quality_totals(
    total: dict[str, dict[str, int]], observed: dict[str, dict[str, int]],
) -> None:
    """Accumulate every verified quality dimension without a fixed-key schema."""
    for dimension, metrics in observed.items():
        bucket = total.setdefault(dimension, {})
        for metric, count in metrics.items():
            bucket[metric] = bucket.get(metric, 0) + int(count)


def read_pinned_pbp_signals(
    path: Path, season: int,
) -> tuple[dict[str, Counter], set[str], dict[str, int]]:
    """Count provisional PBP play signals by exact game+GSIS player identity."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("NFL_PROP_PBP_SOURCE_MISSING:" + str(season))
    blob = path.read_bytes()
    if (len(blob) != OFFICIAL_SIZE_BYTES["pbp"][season]
            or hashlib.sha256(blob).hexdigest() != PBP_GZ_SHA256[season]):
        raise ValueError("NFL_PROP_PBP_SOURCE_DIGEST_MISMATCH:" + str(season))
    counts = {name: Counter() for name in SOURCE_ROLES}
    games: set[str] = set()
    seen_plays: set[tuple[str, str]] = set()
    unknown = {flag: 0 for flag in FLAGS}
    unattributed = {name: 0 for name in SOURCE_ROLES}
    special = {"qb_spike_rows": 0, "qb_kneel_rows": 0,
               "qb_spike_attributed": 0, "qb_kneel_attributed": 0}
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as source:
            for frame in pd.read_csv(source, usecols=list(FIELDS), dtype=str,
                                     chunksize=50000, low_memory=False):
                reg = frame.loc[frame["season_type"].eq("REG")].copy()
                if reg.empty:
                    continue
                if reg["season"].isna().any() or not reg["season"].eq(str(season)).all():
                    raise ValueError("NFL_PROP_PBP_EVENT_SCHEMA_INVALID:" + str(season) + ":season")
                ids = reg["game_id"].astype(str).str.strip().str.extract(
                    r"^(20[0-9]{2})_([0-9]{2})_[A-Z0-9]+_[A-Z0-9]+$"
                )
                week = pd.to_numeric(reg["week"], errors="coerce")
                if (ids.isna().any().any()
                        or not ids[0].eq(str(season)).all()
                        or week.isna().any() or not week.between(1, 18).all()
                        or (week % 1 != 0).any()
                        or not pd.to_numeric(ids[1], errors="coerce").eq(week).all()):
                    raise ValueError("NFL_PROP_PBP_EVENT_SCHEMA_INVALID:" + str(season) + ":game_id")
                games.update(reg["game_id"].astype(str).str.strip())
                # Two NFL box-score count definitions include administrative
                # plays: spikes are incompletions/attempts; kneels are carries.
                # Record source-attribution evidence before applying filters.
                special["qb_spike_rows"] += int(reg["play_type"].eq("qb_spike").sum())
                special["qb_kneel_rows"] += int(reg["play_type"].eq("qb_kneel").sum())
                special["qb_spike_attributed"] += int(
                    (reg["play_type"].eq("qb_spike")
                     & reg["passer_player_id"].notna()
                     & pd.to_numeric(reg["pass_attempt"], errors="coerce").eq(1)).sum()
                )
                special["qb_kneel_attributed"] += int(
                    (reg["play_type"].eq("qb_kneel")
                     & reg["rusher_player_id"].notna()
                     & pd.to_numeric(reg["rush_attempt"], errors="coerce").eq(1)).sum()
                )
                actor = reg.loc[:, ("passer_player_id", "receiver_player_id",
                                    "rusher_player_id")].notna().any(axis=1)
                # A claimed pass/rush without any actor is incomplete source
                # attribution, not a missing positive play to silently drop.
                claimed = (
                    pd.to_numeric(reg["pass_attempt"], errors="coerce").eq(1)
                    | pd.to_numeric(reg["rush_attempt"], errors="coerce").eq(1)
                )
                unassigned = (
                    ~actor & claimed & reg["play_type"].ne("no_play")
                    & ~pd.to_numeric(reg["two_point_attempt"], errors="coerce").eq(1)
                )
                if unassigned.any():
                    raise ValueError(
                        "NFL_PROP_PBP_ATTRIBUTION_INCOMPLETE:"
                        + str(season) + ":" + str(int(unassigned.sum()))
                    )
                relevant = reg.loc[actor].copy()
                if relevant.empty:
                    continue
                if (relevant["play_id"].isna().any()
                        or relevant["play_id"].astype(str).str.strip().eq("").any()):
                    raise ValueError("NFL_PROP_PBP_EVENT_SCHEMA_INVALID:" + str(season) + ":play_id")
                for game, play in zip(relevant["game_id"], relevant["play_id"]):
                    key = (str(game).strip(), str(play).strip())
                    if key in seen_plays:
                        raise ValueError("NFL_PROP_PBP_EVENT_DUPLICATE:" + str(season))
                    seen_plays.add(key)
                for field in FLAGS:
                    original = relevant[field]
                    values = pd.to_numeric(original, errors="coerce")
                    if ((original.notna() & values.isna()).any()
                            or not values.dropna().isin((0, 1)).all()):
                        raise ValueError("NFL_PROP_PBP_FLAG_INVALID:" + field)
                    unknown[field] += int(values.isna().sum())
                    relevant[field] = values
                relevant["play_type"] = relevant["play_type"].str.strip()
                flagged = (relevant["pass_attempt"].eq(1)
                           | relevant["rush_attempt"].eq(1))
                missing_type = (relevant["play_type"].isna()
                                | relevant["play_type"].eq(""))
                if (flagged & missing_type).any():
                    raise ValueError(
                        "NFL_PROP_PBP_EVENT_SCHEMA_INVALID:" + str(season)
                        + ":play_type"
                    )
                normal = relevant["two_point_attempt"].eq(0)
                passing = (
                    normal & relevant["pass_attempt"].eq(1)
                    & relevant["sack"].eq(0)
                    & (
                        (relevant["play_type"].eq("pass") & relevant["qb_spike"].eq(0))
                        | (relevant["play_type"].eq("qb_spike")
                           & relevant["qb_spike"].eq(1))
                    )
                )
                rushing = (
                    normal & relevant["rush_attempt"].eq(1)
                    & relevant["play_type"].isin(("run", "qb_kneel"))
                )
                specs = {
                    "attempts": ("passer_player_id", passing),
                    "completions": ("passer_player_id", passing & relevant["complete_pass"].eq(1)),
                    "passing_tds": ("passer_player_id", passing & relevant["pass_touchdown"].eq(1)),
                    "receptions": ("receiver_player_id", passing & relevant["complete_pass"].eq(1)),
                    "targets": ("receiver_player_id", passing),
                    "carries": ("rusher_player_id", rushing),
                }
                for name, (field, mask) in specs.items():
                    selected = relevant.loc[mask, ["game_id", field]]
                    absent = selected[field].isna() | selected[field].astype(str).str.strip().eq("")
                    unattributed[name] += int(absent.sum())
                    for game, player in selected.loc[~absent].itertuples(index=False, name=None):
                        counts[name][(str(game).strip(), str(player).strip())] += 1
    except ValueError as exc:
        if str(exc).startswith("NFL_PROP_"):
            raise
        raise ValueError("NFL_PROP_PBP_EVENT_SCHEMA_INVALID:" + str(season)) from exc
    except (OSError, EOFError, UnicodeError, pd.errors.ParserError) as exc:
        raise ValueError("NFL_PROP_PBP_SOURCE_UNREADABLE:" + str(season)) from exc
    if not games:
        raise ValueError("NFL_PROP_PBP_EVENT_SCHEMA_INVALID:" + str(season) + ":empty")
    return counts, games, {"flag_nulls": unknown, "unattributed": unattributed,
                           "administrative_play_rows": special}


def inspect_pinned_residual_actor_plays(
    path: Path, season: int, game_id: str, player_id: str, stat: str,
) -> dict[str, object]:
    """Bounded source-forensics receipt, never a corrected or inferred stat."""
    if stat not in ("attempts", "carries"):
        raise ValueError("NFL_PROP_PBP_STAT_COLUMNS_MISSING")
    if path.is_symlink() or not path.is_file():
        raise ValueError("NFL_PROP_PBP_SOURCE_MISSING:" + str(season))
    blob = path.read_bytes()
    if (len(blob) != OFFICIAL_SIZE_BYTES["pbp"][season]
            or hashlib.sha256(blob).hexdigest() != PBP_GZ_SHA256[season]):
        raise ValueError("NFL_PROP_PBP_SOURCE_DIGEST_MISMATCH:" + str(season))
    field = "passer_player_id" if stat == "attempts" else "rusher_player_id"
    flag = "pass_attempt" if stat == "attempts" else "rush_attempt"
    types = Counter()
    flagged = 0
    excluded = Counter()
    examples = []
    columns = ("game_id", "season_type", "play_id", "play_type", field,
               flag, "sack", "qb_spike", "two_point_attempt")
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as source:
        try:
            chunks = pd.read_csv(source, usecols=list(dict.fromkeys(columns)),
                                 dtype=str, chunksize=50000, low_memory=False)
            for frame in chunks:
                selected = frame.loc[
                    frame["season_type"].eq("REG")
                    & frame["game_id"].astype(str).str.strip().eq(game_id)
                    & frame[field].astype(str).str.strip().eq(player_id)
                ]
                for row in selected.to_dict("records"):
                    play_type = str(row["play_type"]).strip()
                    types[play_type] += 1
                    flagged_play = pd.to_numeric(row[flag], errors="coerce") == 1
                    if not flagged_play:
                        continue
                    flagged += 1
                    eligible = (play_type in ("pass", "qb_spike")
                                if stat == "attempts"
                                else play_type in ("run", "qb_kneel"))
                    if not eligible:
                        excluded[play_type] += 1
                        if len(examples) < 5:
                            examples.append({
                                "play_id": str(row["play_id"]),
                                "play_type": play_type,
                                "sack": row["sack"],
                                "qb_spike": row["qb_spike"],
                                "two_point_attempt": row["two_point_attempt"],
                            })
        except (ValueError, OSError, EOFError, pd.errors.ParserError) as exc:
            raise ValueError("NFL_PROP_PBP_SOURCE_UNREADABLE:" + str(season)) from exc
    return {
        "game_id": game_id, "gsis_id": player_id, "stat": stat,
        "actor_play_type_counts": dict(sorted(types.items())),
        "actor_flagged_event_rows": flagged,
        "flagged_unmatched_play_types": dict(sorted(excluded.items())),
        "flagged_excluded_examples": examples,
        "status": "HOLD_PBP_STAT_DISAGREEMENT_UNRESOLVED",
        "missing_play_imputed": False, "can_execute": False,
    }


def compare_weekly_source_to_pbp_signals(
    weekly: pd.DataFrame, signals: dict[str, Counter], games: set[str],
) -> dict[str, object]:
    """Compare observed source counts without filling any missing weekly row."""
    need = {"game_id", "player_id", "position", *SOURCE_ROLES}
    if need - set(weekly.columns):
        raise ValueError("NFL_PROP_PBP_STAT_COLUMNS_MISSING")
    report = {}
    for name, roles in SOURCE_ROLES.items():
        rows = weekly.loc[weekly["position"].astype(str).str.upper().isin(roles)]
        values = pd.to_numeric(rows[name], errors="coerce")
        if (values.isna().any() or values.lt(0).any()
                or (values % 1 != 0).any()):
            raise ValueError("NFL_PROP_SOURCE_COUNT_INVALID:" + name)
        equal = lower = higher = no_game = zero_source_signal = 0
        examples = []
        positive = 0
        for (game, player), target in zip(
                rows[["game_id", "player_id"]].itertuples(index=False, name=None),
                values):
            game, player = str(game).strip(), str(player).strip()
            got = int(signals[name].get((game, player), 0))
            if target == 0:
                zero_source_signal += int(got > 0)
                continue
            positive += 1
            if game not in games:
                no_game += 1
                continue
            if got == target:
                equal += 1
            elif got < target:
                lower += 1
            else:
                higher += 1
            if got != target and len(examples) < 3:
                examples.append({"game_id": game, "player_id": player,
                                 "weekly_positive_count": int(target),
                                 "candidate_pbp_count": got})
        report[name] = {
            "positive_weekly_rows": positive,
            "pbp_signal_equal": equal,
            "pbp_signal_lower": lower,
            "pbp_signal_higher": higher,
            "pbp_game_absent": no_game,
            "weekly_zero_pbp_positive": zero_source_signal,
            "examples": examples,
        }
    return {
        "status": "HOLD_PBP_STAT_SIGNALS_NOT_SETTLEMENT",
        "by_source_stat": report,
        "no_missing_weekly_label_imputed": True,
        "fitted_specialist_certified": False,
        "can_execute": False,
    }
