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

import numpy as np
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



# FTN Data via nflverse, CC-BY-SA-4.0. 2025 post-season research ONLY;
# never an automatic prop target, a zero-stat label or a publishing input.
FTN_PARTICIPATION_2025_SHA256 = "59069adfee7b0f464befba8a5e8be331e523633cc6a7ab403d37bcbcdfbe66ac"
FTN_PARTICIPATION_2025_BYTES = 49094943
FTN_PARTICIPATION_2025_PATH = "pbp_participation/pbp_participation_2025.csv"


def read_verified_ftn_2025_offense_presence(*, open_url=None, timeout: int = 60):
    """Scrape one official, digest-pinned release asset without storing it.

    Field semantics: offense_players are GSIS IDs on a documented play.
    A missing player in this source NEVER establishes zero opportunities.
    """
    import csv
    import re
    import urllib.request
    from urllib.parse import urlparse
    from acquire_nfl_count_prop_sources import _trusted_release_urlopen, RELEASE_HOSTS

    url = ("https://github.com/nflverse/nflverse-data/releases/download/"
           + FTN_PARTICIPATION_2025_PATH)
    request = urllib.request.Request(url, headers={
        "User-Agent": "WOW-V17-public-source-research/1.0",
        "Accept": "application/octet-stream",
    })
    try:
        stream = (open_url or _trusted_release_urlopen)(request, timeout=timeout)
    except ValueError as exc:
        # The trusted GitHub redirect handler fails before returning a stream.
        # Preserve its cause, but expose the FTN-scoped canonical failure.
        if str(exc).startswith("NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED"):
            raise ValueError("NFL_PROP_FTN_PARTICIPATION_REDIRECT_UNTRUSTED") from exc
        raise
    except (OSError, TimeoutError) as exc:
        raise ValueError("NFL_PROP_FTN_PARTICIPATION_NETWORK_UNAVAILABLE") from exc
    payload = bytearray()
    with stream:
        endpoint = urlparse(stream.geturl())
        if endpoint.scheme != "https" or endpoint.hostname not in RELEASE_HOSTS:
            raise ValueError("NFL_PROP_FTN_PARTICIPATION_REDIRECT_UNTRUSTED")
        while True:
            try:
                piece = stream.read(1024 * 1024)
            except (OSError, TimeoutError) as exc:
                raise ValueError("NFL_PROP_FTN_PARTICIPATION_NETWORK_UNAVAILABLE") from exc
            if not piece:
                break
            payload.extend(piece)
            if len(payload) > FTN_PARTICIPATION_2025_BYTES:
                raise ValueError("NFL_PROP_FTN_PARTICIPATION_DIGEST_MISMATCH")
    if (len(payload) != FTN_PARTICIPATION_2025_BYTES
            or hashlib.sha256(payload).hexdigest() != FTN_PARTICIPATION_2025_SHA256):
        raise ValueError("NFL_PROP_FTN_PARTICIPATION_DIGEST_MISMATCH")
    games, offense = set(), set()
    blank_offense_rows = 0
    try:
        with io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8-sig", newline="") as f:
            rows = csv.DictReader(f)
            if not {"nflverse_game_id", "play_id", "offense_players"}.issubset(rows.fieldnames or []):
                raise ValueError("NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:columns")
            for row in rows:
                game = (row.get("nflverse_game_id") or "").strip()
                if not re.fullmatch(r"2025_[0-9]{2}_[A-Z0-9]{2,4}_[A-Z0-9]{2,4}", game):
                    raise ValueError("NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:game_id")
                play = (row.get("play_id") or "").strip()
                if not play.isascii() or not play.isdigit():
                    raise ValueError("NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:play_id")
                games.add(game)
                raw = (row.get("offense_players") or "").strip()
                if not raw:
                    blank_offense_rows += 1
                    continue
                ids = [player.strip() for player in raw.split(";")]
                if (len(ids) != len(set(ids))
                        or any(not re.fullmatch(r"00-[0-9]{7}", player) for player in ids)):
                    raise ValueError("NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:offense_players")
                offense.update((game, player) for player in ids)
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:csv") from exc
    if not games or not offense:
        raise ValueError("NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:empty")
    return games, offense, {
        "status": "HOLD_FTN_OFFENSE_PRESENCE_RESEARCH_ONLY",
        "file": FTN_PARTICIPATION_2025_PATH,
        "source_sha256_verified": FTN_PARTICIPATION_2025_SHA256,
        "bytes_verified": len(payload),
        "license": "CC-BY-SA-4.0",
        "attribution": "FTN Data via nflverse",
        "blank_offense_play_rows": blank_offense_rows,
        "offense_player_games_seen": len(offense),
        "participation_proves_missing_count_zero": False,
        "source_rights_independently_approved": False,
        "fitted_specialist_certified": False,
        "can_execute": False,
    }


def require_ftn_positive_identity_control(observed: dict[str, object]) -> None:
    """Reject any FTN absence breakdown before GSIS positive-controls align.

    This is a research reporting preflight only. It cannot prove count-zero,
    amend a player-game label or authorize training under any circumstance.
    """
    control = observed.get("pbp_positive_opportunity_control")
    if (observed.get("pbp_actor_id_alignment_observed") is not True
            or not isinstance(control, dict)
            or type(control.get("pbp_actor_observed")) is not int
            or control["pbp_actor_observed"] <= 0):
        raise ValueError(
            "HOLD_FTN_PARTICIPATION_SOURCE_OR_IDENTITY:positive_control_unaligned"
        )


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
                # A game identity must refer to the same season/week as
                # its own event row; a bare game_id join otherwise mislabels
                # a different week's player as observed.
                parsed = regular["game_id"].astype(str).str.strip().str.extract(
                    r"^(20[0-9]{2})_([0-9]{2})_[A-Z0-9]+_[A-Z0-9]+$"
                )
                if (parsed.isna().any().any()
                        or not parsed[0].eq(str(season)).all()
                        or not pd.to_numeric(parsed[1], errors="coerce").eq(week).all()):
                    raise ValueError(
                        f"NFL_PROP_PBP_EVENT_SCHEMA_INVALID:{season}:game_id"
                    )
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
    cross = players.loc[:, ["pfr_id", "gsis_id"]].dropna().copy()
    for field in ("pfr_id", "gsis_id"):
        cross[field] = cross[field].astype(str).str.strip()
    cross = cross[(cross["pfr_id"] != "") & (cross["gsis_id"] != "")].drop_duplicates()
    snap["pfr_player_id"] = snap["pfr_player_id"].astype(str).str.strip()
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
    checked = known.merge(
        keys, on=keycols, how="left", validate="one_to_one", indicator=True,
    )
    missing = checked.loc[checked["_merge"].eq("left_only")]
    matched = checked.loc[checked["_merge"].eq("both")]
    if len(matched) != canonical["player_games_matched"]:
        raise ValueError("NFL_PROP_PBP_RECONCILIATION_DRIFT")
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
    control = {"pbp_actor_observed": 0, "pbp_actor_not_observed": 0, "pbp_game_absent": 0}
    for row in matched.itertuples(index=False):
        game_id, player_id = str(row.game_id), str(row.gsis_id)
        if game_id not in games:
            control["pbp_game_absent"] += 1
        elif (game_id, player_id) in actors:
            control["pbp_actor_observed"] += 1
        else:
            control["pbp_actor_not_observed"] += 1
    if sum(control.values()) != canonical["player_games_matched"]:
        raise ValueError("NFL_PROP_PBP_RECONCILIATION_DRIFT")
    # A PBP actor omission is informative only if rows with REAL positive
    # modeled opportunities are actually represented by the actor fields.
    # Matched weekly-stat rows may themselves contain all-zero opportunity
    # statistics; never count those as proof that PBP omitted activity.
    positive_control = None
    opportunity_fields = ("attempts", "targets", "carries")
    if set(opportunity_fields).issubset(stats.columns):
        activity = stats.loc[:, keycols + list(opportunity_fields)].copy()
        activity["game_id"] = activity["game_id"].astype(str).str.strip()
        activity["player_id"] = activity["player_id"].astype(str).str.strip()
        for field in opportunity_fields:
            values = pd.to_numeric(activity[field], errors="coerce")
            if (not np.isfinite(values.to_numpy(dtype=float)).all()
                    or values.lt(0).any() or (values % 1 != 0).any()):
                raise ValueError("NFL_PROP_SOURCE_COUNT_INVALID:" + field)
            activity[field] = values
        positive_keys = activity.loc[
            activity.loc[:, list(opportunity_fields)].gt(0).any(axis=1),
            keycols,
        ]
        positive = matched.merge(
            positive_keys, on=keycols, how="inner", validate="one_to_one",
        )
        positive_control = {
            "pbp_actor_observed": 0, "pbp_actor_not_observed": 0,
            "pbp_game_absent": 0,
        }
        for row in positive.itertuples(index=False):
            game_id, player_id = str(row.game_id), str(row.gsis_id)
            if game_id not in games:
                positive_control["pbp_game_absent"] += 1
            elif (game_id, player_id) in actors:
                positive_control["pbp_actor_observed"] += 1
            else:
                positive_control["pbp_actor_not_observed"] += 1
        if sum(positive_control.values()) != len(positive):
            raise ValueError("NFL_PROP_PBP_RECONCILIATION_DRIFT")
    return {
        "status": "HOLD_PBP_PRESENCE_ONLY",
        "scope": canonical["scope"],
        "mapped_missing_player_games": canonical["missing_player_games"],
        "unmapped_pfr_player_games": canonical["unmapped_pfr_player_games"],
        "pbp_presence_counts": counts,
        "pbp_matched_positive_control": control,
        "pbp_positive_opportunity_control": positive_control,
        "pbp_actor_id_alignment_observed": (
            positive_control is not None
            and positive_control["pbp_actor_observed"] > 0
        ),
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
            # The parser performs the original PBP-specific symlink, SHA and
            # byte-size validation itself; don't mask it with acquisition codes.
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
