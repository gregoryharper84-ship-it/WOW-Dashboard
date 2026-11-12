#!/usr/bin/env python3
"""Offline NFL count-prop participant coverage audit; never fits or certifies models.

Compares pinned nflverse player-week summaries against offensive snap-count
participation using a pinned PFR->GSIS player crosswalk. Missing player-games
remain UNKNOWN, not zero. No execution, publication or serving authority.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from bisect import bisect_left
from pathlib import Path

import numpy as np
import pandas as pd

from train_nfl_missing_prop_candidates import (
    EXPECTED_RELEASE_SHA256, SEASONS, MIN_PRIOR_GAMES, ROUTES, load_source_files,
)

# Published digests from the official nflverse-data release-asset metadata.
SNAP_SHA256 = {
    "2021": "8e4dae054a4749cf2d4919508d9161a6068fd67509979aefa385bfb3803d0ee5",
    "2022": "0018a4833fbf0f825286c1c27450c6254391b548d6c55bcde728e93e4816815a",
    "2023": "303b61aa5c33ffda863f93a750fc14483f397f9187ad502b1ce71e9b516a64c0",
    "2024": "a2aa58efe093f8aa0ad5aadf09f81d8ec690a1183bd2dde68d20e7f109a9c335",
    "2025": "3fc2deb0e9ad86d34d4578cb80bb21c95253e088ee22ca028adf46f7485eff1f",
}
PLAYERS_SHA256 = "da58c285c9013d65bb517d687095e0970f3a4bb148bb81f6ed2aec2bf398b72e"
OFFENSIVE_ROLES = frozenset({"QB", "RB", "WR", "TE"})
SNAP_COLUMNS = ("game_id", "season", "week", "game_type", "pfr_player_id", "position", "offense_snaps")
CROSSWALK_COLUMNS = ("gsis_id", "pfr_id")
STATS_KEYS = ("game_id", "season", "week", "player_id")


def _verified_csv(path: Path, expected_sha: str) -> pd.DataFrame:
    """Parse only the same file bytes proven to match the pinned source SHA-256."""
    if not path.is_file():
        raise ValueError("NFL_PROP_PARTICIPATION_SOURCE_MISSING:" + path.name)
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != expected_sha:
        raise ValueError("NFL_PROP_PARTICIPATION_SOURCE_DIGEST_MISMATCH:" + path.name)
    return pd.read_csv(io.BytesIO(blob), low_memory=False)



def audit_observed_l10_history_gap_exposure(
    player_stats: pd.DataFrame, mapped_missing: pd.DataFrame,
    routes: dict = ROUTES, min_prior_games: int = MIN_PRIOR_GAMES,
) -> dict[str, object]:
    """Bound gaps inside model-eligible observed L10 windows; never infer targets.

    Recorded weekly-stat rows can omit official snap-positive player-games.
    This reports potential feature-selection exposure, NOT verified true-zero
    counts, reconstructed features, training certification or source clearance.
    """
    if "position" not in player_stats.columns:
        return {
            "status": "HOLD_UNRESOLVED_PARTICIPATION", "by_route": {},
            "unavailable_reason": "NFL_PROP_PARTICIPATION_COLUMNS_MISSING",
            "missing_source_zero_imputed": False, "can_execute": False,
        }
    if not {"player_id", "season", "week", "game_id", "position"}.issubset(player_stats):
        raise ValueError("NFL_PROP_PARTICIPATION_COLUMNS_MISSING:STATS")
    if not {"gsis_id", "season", "week", "game_id", "position"}.issubset(mapped_missing):
        raise ValueError("NFL_PROP_PARTICIPATION_COLUMNS_MISSING:MISSING")
    stats, missing = player_stats.copy(), mapped_missing.copy()
    for frame, idfield in ((stats, "player_id"), (missing, "gsis_id")):
        if any(frame[field].isna().any()
               or frame[field].astype(str).str.strip().eq("").any()
               for field in (idfield, "game_id", "position")):
            raise ValueError("NFL_PROP_PARTICIPATION_SNAP_INVALID:history_identity")
        for field in ("season", "week"):
            numeric = pd.to_numeric(frame[field], errors="coerce")
            valid = (numeric.ge(2021) & numeric.le(2025) if field == "season"
                     else numeric.ge(1) & numeric.le(18))
            if not valid.all() or numeric.mod(1).ne(0).any():
                raise ValueError("NFL_PROP_PARTICIPATION_SNAP_INVALID:history_chronology")
            frame[field] = numeric.astype(int)
        frame["event_index"] = frame.season * 100 + frame.week
        frame["position"] = frame.position.astype(str).str.upper()
    if stats.duplicated(["player_id", "season", "week"]).any():
        raise ValueError("NFL_PROP_PARTICIPATION_STATS_DUPLICATE")
    if missing.duplicated(["gsis_id", "season", "week"]).any():
        raise ValueError("NFL_PROP_PARTICIPATION_SNAP_DUPLICATE")
    results = {}
    for route, config in routes.items():
        roles = frozenset(config["roles"].split(","))
        observed = stats.loc[stats.position.isin(roles)].sort_values(
            ["player_id", "event_index", "game_id"])
        gaps = missing.loc[missing.position.isin(roles)]
        missing_events = {
            str(player): sorted(group.event_index.astype(int).tolist())
            for player, group in gaps.groupby("gsis_id", sort=False)
        }
        eligible = exposed = 0
        by_season = {str(year): {"eligible": 0, "exposed": 0} for year in SEASONS}
        examples = []
        for player_id, group in observed.groupby("player_id", sort=False):
            event_indexes = group.event_index.astype(int).tolist()
            unknown = missing_events.get(str(player_id), [])
            for index in range(min_prior_games, len(event_indexes)):
                current = event_indexes[index]
                oldest_recorded = event_indexes[index - min_prior_games]
                year = str(int(group.iloc[index].season))
                eligible += 1
                by_season[year]["eligible"] += 1
                first = bisect_left(unknown, oldest_recorded)
                end = bisect_left(unknown, current)
                if end > first:
                    exposed += 1
                    by_season[year]["exposed"] += 1
                    if len(examples) < 6:
                        examples.append({
                            "player_id": str(player_id),
                            "observed_game_id": str(group.iloc[index].game_id),
                            "unknown_prior_participant_games": end - first,
                            "oldest_l10_recorded_event_index": oldest_recorded,
                        })
        results[route] = {
            "eligible_recorded_l10_rows": eligible,
            "observed_l10_windows_with_unrecorded_snap_positive_game": exposed,
            "by_season": by_season, "examples": examples,
            "missing_source_zero_imputed": False, "feature_vector_rebuilt": False,
        }
    return {
        "status": "HOLD_UNRESOLVED_PARTICIPATION", "by_route": results,
        "scope": "source_observed_prior_rows_vs_mapped_offensive_snap_positive_gaps",
        "unmapped_identity_audited": False, "unknown_outcomes_filled": False,
        "missing_source_zero_imputed": False, "candidate_training_authorized": False,
        "independent_semantic_acceptance": False, "can_execute": False,
    }


def reconcile_player_games(
    player_stats: pd.DataFrame,
    snap_counts: pd.DataFrame,
    players: pd.DataFrame,
) -> dict[str, object]:
    """Find every REG offensive participant missing from weekly count summaries.

    This audits scope coverage, not individual zero counts, no-snap special-team
    participation, exact bet-line provenance, or fitted-probability quality.
    """
    for name, frame, required in (
        ("SNAPS", snap_counts, SNAP_COLUMNS),
        ("PLAYERS", players, CROSSWALK_COLUMNS),
        ("STATS", player_stats, STATS_KEYS),
    ):
        missing = sorted(set(required) - set(frame.columns))
        if missing:
            raise ValueError("NFL_PROP_PARTICIPATION_COLUMNS_MISSING:" + name + ":" + ",".join(missing))

    cross = players.loc[:, CROSSWALK_COLUMNS].dropna().copy()
    for field in CROSSWALK_COLUMNS:
        cross[field] = cross[field].astype(str).str.strip()
    cross = cross[(cross.gsis_id != "") & (cross.pfr_id != "")].drop_duplicates()

    snap = snap_counts.loc[
        snap_counts.game_type.astype(str).str.upper().eq("REG")
        & snap_counts.position.astype(str).str.upper().isin(OFFENSIVE_ROLES)
    ].copy()
    for field in ("season", "week", "offense_snaps"):
        values = pd.to_numeric(snap[field], errors="coerce")
        if (not np.isfinite(values.to_numpy(dtype=float)).all()
                or (field == "offense_snaps" and values.lt(0).any())):
            raise ValueError("NFL_PROP_PARTICIPATION_SNAP_INVALID:" + field)
        snap[field] = values
    if (snap["season"].isin(SEASONS) == False).any() or (
        (snap["week"] < 1) | (snap["week"] > 18) | (snap["week"] % 1 != 0)
    ).any():
        raise ValueError("NFL_PROP_PARTICIPATION_SNAP_INVALID:scope")
    snap = snap.loc[snap.offense_snaps.gt(0)].copy()
    if snap.empty:
        raise ValueError("NFL_PROP_PARTICIPATION_SNAP_EMPTY")
    for field in ("game_id", "pfr_player_id"):
        if snap[field].isna().any() or snap[field].astype(str).str.strip().eq("").any():
            raise ValueError("NFL_PROP_PARTICIPATION_SNAP_INVALID:" + field)
        snap[field] = snap[field].astype(str).str.strip()
    if snap.duplicated(["game_id", "pfr_player_id"]).any():
        raise ValueError("NFL_PROP_PARTICIPATION_SNAP_DUPLICATE")
    snap["season"] = snap["season"].astype(int)
    snap["week"] = snap["week"].astype(int)

    # The 2026 player dictionary includes PFR aliases of older namesakes.
    # An unrelated ambiguity must not poison this predeclared participant cohort.
    # Fail closed for every conflicting identity that actually appears in snaps.
    cross = cross.loc[cross["pfr_id"].isin(snap["pfr_player_id"])].copy()
    if (cross.duplicated(["pfr_id"]).any() or cross.duplicated(["gsis_id"]).any()):
        raise ValueError("NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT")

    mapped = snap.merge(
        cross, how="left", left_on="pfr_player_id", right_on="pfr_id",
        validate="many_to_one", indicator="_crosswalk",
    )
    unmapped = mapped.loc[mapped["_crosswalk"].eq("left_only")].copy()
    known = mapped.loc[mapped["_crosswalk"].eq("both")].copy()
    known["player_id"] = known["gsis_id"]
    keys = player_stats.loc[:, STATS_KEYS].copy()
    keys["game_id"] = keys["game_id"].astype(str).str.strip()
    keys["player_id"] = keys["player_id"].astype(str).str.strip()
    if keys.duplicated(list(STATS_KEYS)).any():
        raise ValueError("NFL_PROP_PARTICIPATION_STATS_DUPLICATE")

    checked = known.merge(
        keys, on=list(STATS_KEYS), how="left", validate="one_to_one", indicator="_present",
    )
    missing = checked.loc[checked["_present"].eq("left_only")]
    examples = missing.loc[:, ["season", "week", "game_id", "pfr_player_id", "gsis_id", "offense_snaps"]]
    unknown_examples = unmapped.loc[:, ["season", "week", "game_id", "pfr_player_id", "offense_snaps"]]
    # Distinguish an entirely unknown PFR key from a PFR record that exists
    # but has no usable GSIS bridge. Neither is a defensible player mapping.
    source_pfr = set(
        players["pfr_id"].dropna().astype(str).str.strip().loc[
            lambda ids: ids.ne("") & ~ids.str.upper().isin(("NA", "NAN", "NONE", "NULL"))
        ]
    )
    unmapped = unmapped.copy()
    unmapped["identity_gap_type"] = unmapped["pfr_player_id"].map(
        lambda pfr: ("PFR_PRESENT_GSIS_UNAVAILABLE"
                     if pfr in source_pfr else "PFR_NOT_IN_PINNED_PLAYERS")
    )
    gap_names = ("PFR_NOT_IN_PINNED_PLAYERS", "PFR_PRESENT_GSIS_UNAVAILABLE")
    gap_counts = {
        gap: int(unmapped["identity_gap_type"].eq(gap).sum())
        for gap in gap_names
    }
    gap_distinct_ids = {
        gap: int(unmapped.loc[
            unmapped["identity_gap_type"].eq(gap), "pfr_player_id"
        ].nunique())
        for gap in gap_names
    }
    if sum(gap_counts.values()) != len(unmapped):
        raise ValueError("NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT")
    # Four PFR IDs account for 48 unmatched player-games in the first hosted
    # cohort; preserve exact source IDs and affected game scope for SIRT only.
    # A source PFR ID is never substituted for a missing GSIS player identity.
    unmapped_profiles = []
    for pfr_id, occurrences in unmapped.groupby("pfr_player_id", sort=True):
        reasons = sorted(set(occurrences["identity_gap_type"]))
        if len(reasons) != 1:
            raise ValueError("NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT")
        unmapped_profiles.append({
            "pfr_player_id": str(pfr_id),
            "identity_gap_type": reasons[0],
            "player_games": int(len(occurrences)),
            "seasons": sorted(int(v) for v in occurrences["season"].unique()),
            "positions": sorted(str(v) for v in occurrences["position"].unique()),
            "max_offense_snaps": int(occurrences["offense_snaps"].max()),
            "first_game_id": str(occurrences.sort_values(
                ["season", "week", "game_id"]
            ).iloc[0]["game_id"]),
        })
    unknown_examples = unmapped.loc[:, [
        "season", "week", "game_id", "pfr_player_id",
        "offense_snaps", "identity_gap_type",
    ]]
    # Diagnostic-only strata: isolate absent event rows from crosswalk gaps.
    # Never convert a missing participant-game into an observed zero count.
    matched = checked.loc[checked["_present"].eq("both")]

    def _histogram(frame: pd.DataFrame, dimension: str) -> dict[str, int]:
        """Count audited rows in a predeclared mutually exclusive stratum."""
        if dimension == "snap_band":
            keys = ("1-9", "10-24", "25-49", "50+")
            labels = pd.cut(
                frame["offense_snaps"], bins=[0, 9, 24, 49, float("inf")],
                labels=keys, include_lowest=True,
            ).astype(str)
        elif dimension == "position":
            keys = ("QB", "RB", "WR", "TE")
            labels = frame["position"].astype(str).str.upper()
        else:
            keys = tuple(str(year) for year in SEASONS)
            labels = frame["season"].astype(str)
        return {key: int(labels.eq(key).sum()) for key in keys}

    strata = {}
    for dimension in ("season", "position", "snap_band"):
        strata[dimension] = {
            "matched_stats": _histogram(matched, dimension),
            "mapped_missing_stats": _histogram(missing, dimension),
            "unmapped_identity": _histogram(unmapped, dimension),
        }
    # Upper bound on potential fitted-candidate label loss.  These are
    # *prior known weekly rows*, not a fitted feature audit or zero-stat proof.
    # Any historical position changes can only lower true route eligibility.
    prior_events: dict[str, list[int]] = {}
    for stat in keys.itertuples(index=False):
        prior_events.setdefault(str(stat.player_id), []).append(
            int(stat.season) * 100 + int(stat.week)
        )
    for events in prior_events.values():
        events.sort()
    history_exposure = {}
    for route, config in ROUTES.items():
        roles = set(config["roles"].split(","))
        route_rows = missing.loc[
            missing["position"].astype(str).str.upper().isin(roles)
        ]
        seasonal = {str(year): 0 for year in SEASONS}
        affected = 0
        for row in route_rows.itertuples(index=False):
            current = int(row.season) * 100 + int(row.week)
            prior = bisect_left(
                prior_events.get(str(row.gsis_id), []), current
            )
            if prior >= MIN_PRIOR_GAMES:
                affected += 1
                seasonal[str(int(row.season))] += 1
        history_exposure[route] = {
            "missing_source_participant_games": int(len(route_rows)),
            "upper_bound_history_eligible_missing_rows": affected,
            "upper_bound_by_season": seasonal,
            "min_prior_recorded_rows": MIN_PRIOR_GAMES,
            "method": "known_earlier_player_weekly_rows_only_conservative_upper_bound",
            "count_label_inferred": False,
        }
    observed_l10_gaps = audit_observed_l10_history_gap_exposure(
        player_stats, missing,
    )
    defects = len(missing) + len(unmapped)
    return {
        "observed_l10_history_gap_exposure": observed_l10_gaps,
        "scope": "2021-2025_REG_QB_RB_WR_TE_OFFENSE_SNAPS_GT_ZERO",
        "offensive_participant_games": int(len(snap)),
        "player_games_matched": int(len(checked) - len(missing)),
        "missing_player_games": int(len(missing)),
        "unmapped_pfr_player_games": int(len(unmapped)),
        "unmapped_distinct_pfr_ids": int(unmapped["pfr_player_id"].nunique()),
        "unmapped_gap_cause_player_games": gap_counts,
        "unmapped_gap_cause_distinct_ids": gap_distinct_ids,
        "unmapped_player_profiles": unmapped_profiles[:20],
        "unmapped_profile_total_pfr_ids": int(len(unmapped_profiles)),
        "missing_weekly_label_history_exposure": history_exposure,
        "participation_breakdown": strata,
        "missing_examples": examples.head(20).to_dict(orient="records"),
        "unmapped_examples": unknown_examples.head(20).to_dict(orient="records"),
        "status": "HOLD_UNRESOLVED_PARTICIPATION" if defects else "RESEARCH_COVERAGE_RECONCILED_ONLY",
        "independent_semantic_acceptance": False,
        "fitted_specialist_certified": False,
        "can_execute": False,
    }


def main() -> int:
    """Check all pinned source assets and print a bounded non-certifying receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats-dir", type=Path, required=True)
    parser.add_argument("--snap-dir", type=Path, required=True)
    parser.add_argument("--players-csv", type=Path, required=True)
    args = parser.parse_args()

    stats, provenance = load_source_files(args.stats_dir, expected_sha256=EXPECTED_RELEASE_SHA256)
    snaps = []
    for year in SEASONS:
        frame = _verified_csv(args.snap_dir / f"snap_counts_{year}.csv", SNAP_SHA256[str(year)])
        if not frame.empty:
            if "season" not in frame.columns or not pd.to_numeric(frame.season, errors="coerce").eq(year).all():
                raise ValueError("NFL_PROP_PARTICIPATION_SNAP_SEASON_MISMATCH:" + str(year))
        # A five-season report cannot treat an empty year as complete coverage.
        if frame.empty or not (
            frame["game_type"].astype(str).str.upper().eq("REG")
            & frame["position"].astype(str).str.upper().isin(OFFENSIVE_ROLES)
            & pd.to_numeric(frame["offense_snaps"], errors="coerce").gt(0)
        ).any():
            raise ValueError("NFL_PROP_PARTICIPATION_SNAP_EMPTY:" + str(year))
        snaps.append(frame)
    players = _verified_csv(args.players_csv, PLAYERS_SHA256)
    result = reconcile_player_games(stats, pd.concat(snaps, ignore_index=True), players)
    result["source_stats_bundle_sha256"] = provenance["bundle_sha256"]
    result["source_snap_sha256"] = SNAP_SHA256
    result["source_players_sha256"] = PLAYERS_SHA256
    result["source_rights_independently_approved"] = False
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 2 if result["status"] == "HOLD_UNRESOLVED_PARTICIPATION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
