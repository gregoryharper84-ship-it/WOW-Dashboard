#!/usr/bin/env python3
"""Research-only NFL play-by-play actor-presence audit against frozen snap/stat sources.

Play-by-play records document ball involvement, NOT every offensive snap.
Even if a game's PBP contains no player actor, absence is UNKNOWN, never a
verified zero count. All results are non-serving and non-certifying.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

import pandas as pd

from acquire_nfl_count_prop_sources import OFFICIAL_SIZE_BYTES, acquire_one
from audit_nfl_count_prop_participation import (
    PLAYERS_SHA256, SNAP_SHA256, _verified_csv, reconcile_player_games,
    OFFENSIVE_ROLES,
)
from train_nfl_missing_prop_candidates import (
    EXPECTED_RELEASE_SHA256, SEASONS, load_source_files,
)

CAN_EXECUTE = False
PBP_GZ_SHA256 = {
    2021: "e8743a568f99667a8bdcd29ba12224ee1782ad2b7916bee78ee335c099383739",
    2022: "0c69a71eb39498956c7b1d5c1ca52ce7fe679934a95d1af249facb5ea9829ea4",
    2023: "4649804ee0f0a40b41e51ec75a1ce921949d7fab5459213488656b92f78560e8",
    2024: "23370d5d10f8104d80d46a1fc5e61f4f6f5a3263fe96fe2dd629913cfcb08c06",
    2025: "2f135887790a013fd004e609e37096bb4816d5cc80b9f19122e1bad478961978",
}
PBP_FIELDS = (
    "game_id", "season", "week", "season_type",
    "passer_player_id", "receiver_player_id", "rusher_player_id",
)
ACTOR_FIELDS = ("passer_player_id", "receiver_player_id", "rusher_player_id")


def pbp_assets() -> list[tuple[str, str, int]]:
    """Return the five immutably pinned upstream compressed PBP sources."""
    if set(PBP_GZ_SHA256) != set(SEASONS):
        raise ValueError("NFL_PROP_PBP_PIN_SET_INVALID")
    return [
        (f"pbp/play_by_play_{year}.csv.gz",
         PBP_GZ_SHA256[year],
         OFFICIAL_SIZE_BYTES["pbp"][year])
        for year in SEASONS
    ]


def read_verified_pbp_presence(
    path: Path, season: int,
) -> tuple[set[str], set[tuple[str, str]]]:
    """Parse the exact verified compressed bytes in bounded dataframe chunks."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"NFL_PROP_PBP_SOURCE_MISSING:{season}")
    blob = path.read_bytes()
    if (len(blob) != OFFICIAL_SIZE_BYTES["pbp"][season]
            or hashlib.sha256(blob).hexdigest() != PBP_GZ_SHA256[season]):
        raise ValueError(f"NFL_PROP_PBP_SOURCE_DIGEST_MISMATCH:{season}")
    games: set[str] = set()
    actors: set[tuple[str, str]] = set()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as decompressed:
            reader = pd.read_csv(
                decompressed, usecols=list(PBP_FIELDS), dtype=str,
                chunksize=50000, low_memory=False,
            )
            for chunk in reader:
                if (chunk["season"].isna().any()
                        or not chunk["season"].eq(str(season)).all()):
                    raise ValueError(f"NFL_PROP_PBP_EVENT_SCHEMA_INVALID:{season}:season")
                regular = chunk.loc[chunk["season_type"].eq("REG")].copy()
                if regular.empty:
                    continue
                if (regular["game_id"].isna().any()
                        or regular["game_id"].astype(str).str.strip().eq("").any()):
                    raise ValueError(f"NFL_PROP_PBP_EVENT_SCHEMA_INVALID:{season}:game_id")
                week = pd.to_numeric(regular["week"], errors="coerce")
                if (week.isna().any() or not week.between(1, 18).all()
                        or (week % 1 != 0).any()):
                    raise ValueError(f"NFL_PROP_PBP_EVENT_SCHEMA_INVALID:{season}:week")
                games.update(regular["game_id"].astype(str).str.strip())
                for field in ACTOR_FIELDS:
                    pairs = regular.loc[
                        regular[field].notna()
                        & regular[field].astype(str).str.strip().ne(""),
                        ["game_id", field],
                    ]
                    actors.update(zip(
                        pairs["game_id"].astype(str).str.strip(),
                        pairs[field].astype(str).str.strip(),
                    ))
    except ValueError as exc:
        if str(exc).startswith("NFL_PROP_PBP_"):
            raise
        raise ValueError(f"NFL_PROP_PBP_EVENT_SCHEMA_INVALID:{season}") from exc
    except (OSError, EOFError, pd.errors.ParserError, UnicodeError) as exc:
        raise ValueError(f"NFL_PROP_PBP_SOURCE_UNREADABLE:{season}") from exc
    if not games:
        raise ValueError(f"NFL_PROP_PBP_EVENT_SCHEMA_INVALID:{season}:empty_reg")
    return games, actors


def classify_missing_pbp_presence(
    stats: pd.DataFrame,
    snaps: pd.DataFrame,
    players: pd.DataFrame,
    games: set[str],
    actors: set[tuple[str, str]],
) -> dict[str, object]:
    """Diagnose weekly-stat omissions; no absence ever becomes a count-zero."""
    canonical = reconcile_player_games(stats, snaps, players)
    snap = snaps.loc[
        snaps["game_type"].astype(str).str.upper().eq("REG")
        & snaps["position"].astype(str).str.upper().isin(OFFENSIVE_ROLES)
        & pd.to_numeric(snaps["offense_snaps"], errors="coerce").gt(0),
    ].copy()
    snap["season"] = pd.to_numeric(snap["season"]).astype(int)
    snap["week"] = pd.to_numeric(snap["week"]).astype(int)
    cross = players.loc[:, ["pfr_id", "gsis_id"]].dropna().drop_duplicates()
    cross = cross.loc[cross["pfr_id"].isin(snap["pfr_player_id"])]
    known = snap.merge(
        cross, how="left", left_on="pfr_player_id", right_on="pfr_id",
        validate="many_to_one",
    ).dropna(subset=["gsis_id"])
    known["player_id"] = known["gsis_id"]
    keycols = ["game_id", "season", "week", "player_id"]
    keys = stats.loc[:, keycols].copy()
    keys["game_id"] = keys["game_id"].astype(str).str.strip()
    keys["player_id"] = keys["player_id"].astype(str).str.strip()
    missing = known.merge(
        keys, on=keycols, how="left", validate="one_to_one", indicator=True,
    )
    missing = missing.loc[missing["_merge"].eq("left_only")]
    if len(missing) != canonical["missing_player_games"]:
        raise ValueError("NFL_PROP_PBP_RECONCILIATION_DRIFT")
    counts = {"pbp_game_absent": 0, "pbp_actor_observed": 0, "pbp_actor_not_observed": 0}
    examples = []
    for row in missing.itertuples(index=False):
        game_id = str(row.game_id)
        player_id = str(row.gsis_id)
        if game_id not in games:
            category = "pbp_game_absent"
        elif (game_id, player_id) in actors:
            category = "pbp_actor_observed"
        else:
            category = "pbp_actor_not_observed"
        counts[category] += 1
        if category != "pbp_actor_not_observed" and len(examples) < 8:
            examples.append({"game_id": game_id, "gsis_id": player_id, "classification": category})
    if sum(counts.values()) != canonical["missing_player_games"]:
        raise ValueError("NFL_PROP_PBP_RECONCILIATION_DRIFT")
    return {
        "status": "HOLD_PBP_PRESENCE_ONLY",
        "scope": canonical["scope"],
        "mapped_missing_player_games": canonical["missing_player_games"],
        "unmapped_pfr_player_games": canonical["unmapped_pfr_player_games"],
        "pbp_presence_counts": counts,
        "pbp_examples": examples,
        "missing_actor_means_zero": False,
        "fitted_specialist_certified": False,
        "source_rights_independently_approved": False,
        "registration_attempted": False,
        "can_execute": False,
    }


def analyze_source_root(root: Path, *, download_pbp: bool = False) -> dict[str, object]:
    """Verify all required sources before returning a bounded research HOLD."""
    stats, provenance = load_source_files(
        root / "stats_player", expected_sha256=EXPECTED_RELEASE_SHA256,
    )
    snap = pd.concat([
        _verified_csv(root / "snap_counts" / f"snap_counts_{season}.csv",
                      SNAP_SHA256[str(season)])
        for season in SEASONS
    ], ignore_index=True)
    players = _verified_csv(root / "players" / "players.csv", PLAYERS_SHA256)
    games: set[str] = set()
    actors: set[tuple[str, str]] = set()
    receipts = []
    for relative, digest, size in pbp_assets():
        if download_pbp:
            receipts.append(acquire_one(relative, digest, size, root, timeout=20))
        else:
            # Same digest, size, symlink and content checks as the downloader.
            from acquire_nfl_count_prop_sources import verify_file
            verify_file(root / relative, digest, size)
            receipts.append({"file": relative, "sha256": digest, "size_bytes": size})
        year = int(relative.split("_")[-1].split(".")[0])
        one_games, one_actors = read_verified_pbp_presence(root / relative, year)
        games.update(one_games)
        actors.update(one_actors)
    result = classify_missing_pbp_presence(stats, snap, players, games, actors)
    result["pbp_assets_sha256_verified"] = len(receipts)
    result["pbp_bytes_sha256_verified"] = sum(x["size_bytes"] for x in receipts)
    result["stats_bundle_sha256"] = provenance["bundle_sha256"]
    result["pbp_game_count"] = len(games)
    return result


def main() -> int:
    """Run the local read-only corpus audit; HOLD exit code is intentional."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--download-pbp", action="store_true")
    args = parser.parse_args()
    result = analyze_source_root(args.source_root, download_pbp=args.download_pbp)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 2  # Research HOLD, never a certification success exit.


if __name__ == "__main__":
    raise SystemExit(main())
