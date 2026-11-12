#!/usr/bin/env python3
"""One digest-pinned 2025 nflverse roster crosswalk diagnostic, read-only.

The only source admitted here has a published release SHA-256. Historical
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
    RELEASE_HOSTS, _trusted_release_urlopen,
)

CAN_EXECUTE = False
ROSTER_2025_PATH = "rosters/roster_2025.csv"
ROSTER_2025_SIZE = 1010039
ROSTER_2025_SHA256 = "531ee5de386037bb17de3e3e3d50f7b4ed08789526bbaae9876fdd3dfb4d04ad"
ROSTER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    + ROSTER_2025_PATH
)
CROSSWALK_FIELDS = ("pfr_id", "gsis_id", "season", "position", "team")


def read_pinned_2025_roster(*, open_url=None, timeout: int = 20) -> tuple[pd.DataFrame, dict]:
    """Verify exact published bytes before parsing; no source mutation."""
    req = urllib.request.Request(
        ROSTER_URL, headers={
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
            if len(payload) > ROSTER_2025_SIZE:
                raise ValueError("NFL_PROP_ROSTER_DIGEST_MISMATCH")
    if len(payload) != ROSTER_2025_SIZE or sha.hexdigest() != ROSTER_2025_SHA256:
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
        "asset": ROSTER_2025_PATH,
        "sha256": sha.hexdigest(),
        "bytes_sha256_verified": len(payload),
        "release_digest_published": True,
        "independent_semantic_acceptance": False,
        "can_execute": False,
    }


def inspect_roster_id_candidates(
    roster: pd.DataFrame, target_pfr_ids: list[str],
) -> dict[str, object]:
    """Enumerate exact ID pairs, never invent or apply a player identity."""
    if set(CROSSWALK_FIELDS) - set(roster.columns):
        raise ValueError("NFL_PROP_ROSTER_SCHEMA_INVALID")
    targets = sorted(set(target_pfr_ids))
    if not targets or any(not x or x != x.strip() for x in targets):
        raise ValueError("NFL_PROP_ROSTER_TARGET_INVALID")
    copy = roster.loc[:, CROSSWALK_FIELDS].copy()
    copy["pfr_id"] = copy["pfr_id"].astype("string").str.strip()
    selected = copy.loc[copy["pfr_id"].isin(targets)]
    out = []
    for pfr in targets:
        rows = selected.loc[selected["pfr_id"].eq(pfr)]
        valid = rows.loc[
            rows["gsis_id"].notna()
            & rows["gsis_id"].astype("string").str.fullmatch(r"00-[0-9]{7}").fillna(False)
        ]
        ids = sorted(set(valid["gsis_id"].astype(str)))
        if len(ids) > 1:
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
        "season": 2025,
        "target_pfr_count": len(targets),
        "source_exact_pair_candidate_count": sum(bool(row["gsis_candidates"]) for row in out),
        "targets": out,
        "candidate_crosswalk_applied": False,
        "source_rights_independently_approved": False,
        "fitted_specialist_certified": False,
        "can_execute": False,
    }
