#!/usr/bin/env python3
"""Two digest-pinned 2025 nflverse roster crosswalk diagnostics, read-only.

Only assets with independently published release SHA-256 values are admitted. Historical
roster assets lacking an independently reviewed checksum are not silently
substituted. Candidate ID bridges NEVER alter fitted/serving player identity.
"""
from __future__ import annotations

import hashlib
import io
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from acquire_nfl_count_prop_sources import (
    ARCHIVE_OBSERVATION, RELEASE_HOSTS, _trusted_release_urlopen,
)

CAN_EXECUTE = False
ROSTER_2025_PATH = "rosters/roster_2025.csv"
ROSTER_2025_SIZE = 1010039
ROSTER_2025_SHA256 = "531ee5de386037bb17de3e3e3d50f7b4ed08789526bbaae9876fdd3dfb4d04ad"
WEEKLY_ROSTER_2025_PATH = "weekly_rosters/roster_weekly_2025.csv"
WEEKLY_ROSTER_2025_SIZE = 15385661
WEEKLY_ROSTER_2025_SHA256 = "c2f7a1ffebe06058400af1989d1cd2900cc5c9659f084623708a06d4e28de35b"
ROSTER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    + ROSTER_2025_PATH
)
CROSSWALK_FIELDS = ("pfr_id", "gsis_id", "season", "position", "team")
# Historical GitHub release metadata publishes asset IDs and byte sizes
# but no SHA-256 for these files. This separate two-read research path can
# yield provisional exact-pair evidence only; never a governed identity.
HISTORICAL_ROSTER_SIZES = {
    2021: 953822, 2022: 980115, 2023: 967354, 2024: 1005701,
}



def read_pinned_2025_roster(
    *, open_url=None, timeout: int = 20, weekly: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Verify exact published bytes before parsing; no identity mutation."""
    asset = WEEKLY_ROSTER_2025_PATH if weekly else ROSTER_2025_PATH
    size = WEEKLY_ROSTER_2025_SIZE if weekly else ROSTER_2025_SIZE
    digest = WEEKLY_ROSTER_2025_SHA256 if weekly else ROSTER_2025_SHA256
    url = ("https://github.com/nflverse/nflverse-data/releases/download/"
           + asset)
    req = urllib.request.Request(
        url, headers={
            "User-Agent": "WOW-V17-public-source-research/1.0",
            "Accept": "application/octet-stream",
        },
    )
    stream = (open_url or _trusted_release_urlopen)(req, timeout=timeout)
    payload = bytearray()
    sha = hashlib.sha256()
    with stream:
        final = urlparse(stream.geturl())
        if final.scheme != "https" or final.hostname not in RELEASE_HOSTS:
            raise ValueError("NFL_PROP_ROSTER_REDIRECT_UNTRUSTED")
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
            sha.update(chunk)
            if len(payload) > size:
                raise ValueError("NFL_PROP_ROSTER_DIGEST_MISMATCH")
    if len(payload) != size or sha.hexdigest() != digest:
        raise ValueError("NFL_PROP_ROSTER_DIGEST_MISMATCH")
    try:
        roster = pd.read_csv(io.BytesIO(payload), dtype=str, low_memory=False)
    except (UnicodeError, pd.errors.ParserError) as exc:
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID") from exc
    if set(CROSSWALK_FIELDS) - set(roster.columns):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID")
    if (roster["season"].isna().any()
            or not roster["season"].eq("2025").all()):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID")
    return roster, {
        "asset": asset,
        "sha256": sha.hexdigest(),
        "bytes_sha256_verified": len(payload),
        "release_digest_published": True,
        "independent_semantic_acceptance": False,
        "can_execute": False,
    }


def read_two_copy_historical_roster(
    season: int, *, open_url=None, timeout: int = 20,
) -> tuple[pd.DataFrame, dict]:
    """Read an unsigned historic release twice; always return a HOLD."""
    if season not in HISTORICAL_ROSTER_SIZES:
        raise ValueError("NFL_PROP_ROSTER_YEAR_UNPINNED")
    expected_size = HISTORICAL_ROSTER_SIZES[season]
    asset = f"rosters/roster_{season}.csv"
    url = ("https://github.com/nflverse/nflverse-data/releases/download/"
           + asset)

    def one_read() -> bytes:
        request = urllib.request.Request(
            url, headers={
                "User-Agent": "WOW-V17-public-source-research/1.0",
                "Accept": "application/octet-stream",
            },
        )
        stream = (open_url or _trusted_release_urlopen)(request, timeout=timeout)
        contents = bytearray()
        with stream:
            final = urlparse(stream.geturl())
            if final.scheme != "https" or final.hostname not in RELEASE_HOSTS:
                raise ValueError("NFL_PROP_ROSTER_REDIRECT_UNTRUSTED")
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                contents.extend(block)
                if len(contents) > expected_size:
                    raise ValueError("NFL_PROP_ROSTER_SOURCE_SIZE_MISMATCH")
        if len(contents) != expected_size:
            raise ValueError("NFL_PROP_ROSTER_SOURCE_SIZE_MISMATCH")
        return bytes(contents)

    first = one_read()
    second = one_read()
    digest = hashlib.sha256(first).hexdigest()
    if digest != hashlib.sha256(second).hexdigest() or first != second:
        raise ValueError("NFL_PROP_ROSTER_TWO_READ_DRIFT")
    try:
        roster = pd.read_csv(io.BytesIO(first), dtype=str, low_memory=False)
    except (UnicodeError, pd.errors.ParserError) as exc:
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID") from exc
    if set(CROSSWALK_FIELDS) - set(roster.columns):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID")
    if (roster["season"].isna().any()
            or not roster["season"].eq(str(season)).all()):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID")
    return roster, {
        "status": "HOLD_HISTORICAL_ROSTER_UNPUBLISHED_DIGEST",
        "asset": asset,
        "source_sha256_observed_twice": digest,
        "bytes_observed_twice": expected_size,
        "release_digest_published": False,
        "identical_read_count": 2,
        "candidate_crosswalk_applied": False,
        "source_rights_independently_approved": False,
        "can_execute": False,
    }


def inspect_roster_id_candidates(
    roster: pd.DataFrame, target_pfr_ids: list[str], *, expected_season: int = 2025,
) -> dict[str, object]:
    """Enumerate exact ID pairs, never invent or apply a player identity."""
    if set(CROSSWALK_FIELDS) - set(roster.columns):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID")
    # This function is public to other research-only callers; do not trust
    # that they went through the pinned 2025 reader first.
    if expected_season not in (*HISTORICAL_ROSTER_SIZES, 2025):
        raise ValueError("NFL_PROP_ROSTER_YEAR_UNPINNED")
    season = roster["season"].astype("string")
    if not season.eq(str(expected_season)).fillna(False).all():
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID")
    targets = sorted(set(target_pfr_ids))
    if not targets or any(not x or x != x.strip() for x in targets):
        raise ValueError("NFL_PROP_ROSTER_TARGET_INVALID")
    copy = roster.loc[:, CROSSWALK_FIELDS].copy()
    copy["pfr_id"] = copy["pfr_id"].astype("string").str.strip()
    nonblank_pfr = copy["pfr_id"].notna() & copy["pfr_id"].ne("")
    valid_gsis = (
        copy["gsis_id"].astype("string")
        .str.fullmatch(r"00-[0-9]{7}").fillna(False)
    )
    selected = copy.loc[copy["pfr_id"].isin(targets)]
    # A valid-looking target is ambiguous if any other source PFR code claims
    # its GSIS ID, including PFR codes outside this review sample.
    source_pfr_by_gsis = (
        copy.loc[nonblank_pfr & valid_gsis]
        .groupby("gsis_id")["pfr_id"].nunique()
    )
    out = []
    for pfr in targets:
        rows = selected.loc[selected["pfr_id"].eq(pfr)]
        # A valid GSIS row cannot mask an invalid row for the same PFR ID.
        # Keep source inconsistency as a typed HOLD before constructing candidates.
        if not valid_gsis.loc[rows.index].all():
            raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:target_gsis")
        valid = rows.loc[
            rows["gsis_id"].notna()
            & rows["gsis_id"].astype("string").str.fullmatch(r"00-[0-9]{7}").fillna(False)
        ]
        ids = sorted(set(valid["gsis_id"].astype(str)))
        if len(ids) > 1 or any(source_pfr_by_gsis.get(gsis, 0) > 1 for gsis in ids):
            raise ValueError("NFL_PROP_ROSTER_CROSSWALK_CONFLICT:" + pfr)
        out.append({
            "pfr_player_id": pfr,
            "gsis_candidates": ids,
            "source_rows": int(len(rows)),
            "source_gsis_rows": int(len(valid)),
            "positions": sorted(set(rows["position"].dropna().astype(str))),
            "teams": sorted(set(rows["team"].dropna().astype(str))),
            "status": "CANDIDATE_EXACT_SOURCE_PAIR_NOT_ADOPTED" if ids
                      else "HOLD_NO_ROSTER_GSIS_PAIR",
        })
    return {
        "status": "HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW",
        "season": expected_season,
        "target_pfr_count": len(targets),
        "source_exact_pair_candidate_count": sum(bool(row["gsis_candidates"]) for row in out),
        "source_rows_total": int(len(copy)),
        "source_nonblank_pfr_rows": int(nonblank_pfr.sum()),
        "source_valid_gsis_rows": int(valid_gsis.sum()),
        "source_both_exact_id_rows": int((nonblank_pfr & valid_gsis).sum()),
        "targets": out,
        "candidate_crosswalk_applied": False,
        "source_rights_independently_approved": False,
        "fitted_specialist_certified": False,
        "can_execute": False,
    }


def inspect_manual_pfr_overrides(
    records: list[dict[str, object]], target_pfr_ids: list[str],
) -> dict[str, object]:
    """Examine exact PFR/GSIS pairs in nflverse manual corrections, never adopt IDs.

    Manual overrides are sparse corrections, NOT the full players universe.
    Their lack of a PFR ID cannot establish that a player has no GSIS ID.
    Only a separate authenticated, source-reviewed snapshot can establish
    provenance; this function makes no source-rights or fitting assertion.
    """
    import re

    if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:manual_overrides")
    if (not isinstance(target_pfr_ids, list)
            or not target_pfr_ids
            or any(not isinstance(x, str)
                   or not re.fullmatch(r"[A-Za-z0-9]{8}", x)
                   for x in target_pfr_ids)):
        raise ValueError("NFL_PROP_ROSTER_TARGET_INVALID")
    targets = sorted(set(target_pfr_ids))
    chosen = {key: [] for key in targets}
    for record in records:
        source_pfr = record.get("pfr_id")
        if source_pfr is not None and not isinstance(source_pfr, str):
            raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:manual_override_pfr")
        if source_pfr not in chosen:
            continue
        source_gsis = record.get("gsis_id")
        if not isinstance(source_gsis, str) or not re.fullmatch(r"00-[0-9]{7}", source_gsis):
            raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:manual_override_gsis")
        chosen[source_pfr].append(source_gsis)
    rows = []
    for pfr in targets:
        identities = sorted(set(chosen[pfr]))
        if len(identities) > 1:
            raise ValueError("NFL_PROP_ROSTER_CROSSWALK_CONFLICT:" + pfr)
        rows.append({
            "pfr_player_id": pfr,
            "manual_override_rows": len(chosen[pfr]),
            "gsis_candidates": identities,
            "status": "CANDIDATE_EXACT_SOURCE_PAIR_NOT_ADOPTED" if identities
                      else "HOLD_NO_MANUAL_OVERRIDE_PAIR",
        })
    return {
        "status": "HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW",
        "source_kind": "nflverse_players_manual_overwrite",
        "manual_overrides_are_not_full_player_universe": True,
        "no_exact_override_does_not_prove_no_player": True,
        "target_pfr_count": len(rows),
        "target_exact_override_count": sum(bool(x["gsis_candidates"]) for x in rows),
        "source_override_records": len(records),
        "targets": rows,
        "candidate_crosswalk_applied": False,
        "source_rights_independently_approved": False,
        "fitted_specialist_certified": False,
        "can_execute": False,
    }


def inspect_archived_players_rds(
    path: Path, target_pfr_ids: list[str], *, read_rds=None,
) -> dict[str, object]:
    """Inspect a locally pinned Nov-05 identity master; never adopt identities.

    The archive is comparison evidence only. A GSIS/PFR pair may originate in
    name-based upstream joins and requires independent semantic acceptance.
    This function cannot fit, register, calibrate, publish, or approve use.
    """
    import importlib
    import re
    import tempfile

    if (not isinstance(target_pfr_ids, list) or not target_pfr_ids
            or any(not isinstance(x, str)
                   or re.fullmatch(r"[A-Za-z0-9]{8}", x) is None
                   for x in target_pfr_ids)):
        raise ValueError("NFL_PROP_ROSTER_TARGET_INVALID")
    targets = sorted(set(target_pfr_ids))
    frozen = Path(path)
    if frozen.is_symlink() or not frozen.is_file():
        raise ValueError("NFL_PROP_ACQUIRE_FILE_MISSING:players.rds")
    try:
        payload = frozen.read_bytes()
    except OSError as exc:
        raise ValueError("NFL_PROP_ACQUIRE_FILE_MISSING:players.rds") from exc
    _asset_id, expected_size, expected_sha = ARCHIVE_OBSERVATION["assets"]["players.rds"]
    actual_sha = hashlib.sha256(payload).hexdigest()
    if len(payload) != expected_size or actual_sha != expected_sha:
        raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:players.rds")
    if read_rds is None:
        try:
            read_rds = importlib.import_module("pyreadr").read_r
        except (ImportError, AttributeError) as exc:
            raise ValueError("NFL_PROP_ROSTER_RDS_READER_UNAVAILABLE") from exc

    # The decoder never sees mutable caller-owned bytes. Source and snapshot
    # are both checked again after parsing; a changed file cannot pass.
    with tempfile.TemporaryDirectory(prefix="wow-nfl-archive-rds-") as scratch:
        snapshot = Path(scratch) / "players.rds"
        snapshot.write_bytes(payload)
        try:
            parsed = read_rds(str(snapshot))
        except Exception as exc:
            raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:archive_rds_parse") from exc
        if snapshot.read_bytes() != payload:
            raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:archive_snapshot")
    try:
        if frozen.read_bytes() != payload:
            raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:source_changed")
    except OSError as exc:
        raise ValueError("NFL_PROP_ACQUIRE_FILE_MISSING:players.rds") from exc
    if (not isinstance(parsed, dict) or len(parsed) != 1
            or not isinstance(next(iter(parsed.values())), pd.DataFrame)):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:archive_rds_shape")
    frame = next(iter(parsed.values()))
    if {"pfr_id", "gsis_id"} - set(frame.columns):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:archive_identity_columns")
    source = frame.loc[:, ["pfr_id", "gsis_id"]].copy()
    source["pfr_id"] = source["pfr_id"].astype("string").str.strip()
    source["gsis_id"] = source["gsis_id"].astype("string").str.strip()
    nonblank_pfr = source["pfr_id"].notna() & source["pfr_id"].ne("")
    valid_gsis = source["gsis_id"].str.fullmatch(r"00-[0-9]{7}").fillna(False)
    # Check against the whole master, not just the selected four players.
    pfr_count_by_gsis = (
        source.loc[nonblank_pfr & valid_gsis]
        .groupby("gsis_id")["pfr_id"].nunique()
    )
    results = []
    for pfr in targets:
        matching = source.loc[source["pfr_id"].eq(pfr).fillna(False)]
        if not matching.empty and not valid_gsis.loc[matching.index].all():
            raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID:archive_target_gsis")
        candidates = sorted(set(matching["gsis_id"].dropna().astype(str)))
        if (len(candidates) > 1
                or any(pfr_count_by_gsis.get(gsis, 0) > 1 for gsis in candidates)):
            raise ValueError("NFL_PROP_ROSTER_CROSSWALK_CONFLICT:" + pfr)
        results.append({
            "pfr_player_id": pfr,
            "source_rows": int(len(matching)),
            "gsis_candidates": candidates,
            "status": ("CANDIDATE_EXACT_SOURCE_PAIR_NOT_ADOPTED" if candidates
                       else "HOLD_ARCHIVE_PFR_ID_NOT_FOUND"),
        })
    return {
        "status": "HOLD_ARCHIVED_IDENTITY_INDEPENDENT_REVIEW",
        "source": "nflverse/nflverse-data-archives/players.rds",
        "archive_tag": ARCHIVE_OBSERVATION["tag_name"],
        "source_sha256": actual_sha,
        "source_bytes_sha256_verified": True,
        "source_rows": int(len(frame)),
        "target_pfr_count": len(results),
        "target_exact_candidate_count": sum(bool(x["gsis_candidates"]) for x in results),
        "targets": results,
        "source_absence_does_not_prove_player_absence": True,
        "crosswalk_applied": False,
        "independent_identity_accepted": False,
        "source_rights_independently_approved": False,
        "training_authorized": False,
        "probability_publishable": False,
        "can_execute": False,
    }
