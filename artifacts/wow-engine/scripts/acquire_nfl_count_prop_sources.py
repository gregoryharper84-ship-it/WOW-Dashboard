#!/usr/bin/env python3
"""Download 11 digest-pinned nflverse CSVs for offline NFL prop research only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from train_nfl_missing_prop_candidates import EXPECTED_RELEASE_SHA256
from audit_nfl_count_prop_participation import SNAP_SHA256, PLAYERS_SHA256

CAN_EXECUTE = False
OFFICIAL_SIZE_BYTES = {
    "stats_player": {2021: 8477784, 2022: 8408729, 2023: 8332874, 2024: 8470040, 2025: 8656387},
    "snap_counts": {2021: 2388493, 2022: 2379719, 2023: 2394875, 2024: 2402841, 2025: 2401290},
    "players": 7306971,
    "pbp": {2021: 19022201, 2022: 19093961, 2023: 19169807, 2024: 19362351, 2025: 19105296},
}
RELEASE_HOSTS = frozenset((
    "github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com", "github-releases.githubusercontent.com",
))


class _OfficialReleaseRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject untrusted Location destinations before urllib sends a request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in RELEASE_HOSTS:
            raise ValueError("NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _trusted_release_urlopen(request, *, timeout):
    """Preserve HTTPS and allowlisted hosts over the full redirect chain."""
    url = urlparse(request.full_url)
    if url.scheme != "https" or url.hostname not in RELEASE_HOSTS:
        raise ValueError("NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED")
    return urllib.request.build_opener(_OfficialReleaseRedirectHandler()).open(
        request, timeout=timeout,
    )


def frozen_assets() -> list[tuple[str, str, int]]:
    """Use reviewed hard-coded digests, never a caller-editable JSON manifest."""
    assets = []
    for year in range(2021, 2026):
        assets.append((f"stats_player/stats_player_week_{year}.csv",
                       EXPECTED_RELEASE_SHA256[str(year)], OFFICIAL_SIZE_BYTES["stats_player"][year]))
        assets.append((f"snap_counts/snap_counts_{year}.csv",
                       SNAP_SHA256[str(year)], OFFICIAL_SIZE_BYTES["snap_counts"][year]))
    assets.append(("players/players.csv", PLAYERS_SHA256, OFFICIAL_SIZE_BYTES["players"]))
    if len(assets) != 11 or len({a[0] for a in assets}) != 11:
        raise ValueError("NFL_PROP_ACQUIRE_PINS_INVALID")
    return assets


def verify_official_release_metadata(assets: list[tuple[str, str, int]],
                                     releases: dict[str, dict]) -> None:
    """Detect a moved GitHub release asset before costly offline downloads.

    Metadata is only an early HOLD signal; the original source SHA/size pins
    remain authoritative and every acquired byte must still pass verify_file.
    This function never updates a pin, supplies a target, or authorizes fitting.
    """
    families = {relative.split("/")[0] for relative, _, _ in assets}
    if not isinstance(releases, dict) or set(releases) != families:
        raise ValueError("NFL_PROP_ACQUIRE_PINS_INVALID:release_metadata")
    for relative, digest, size in assets:
        family, name = relative.split("/")
        release = releases[family]
        if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
            raise ValueError("NFL_PROP_ACQUIRE_PINS_INVALID:release_metadata")
        matches = [item for item in release["assets"]
                   if isinstance(item, dict) and item.get("name") == name]
        if len(matches) != 1:
            raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:" + name
                             + ":release_asset_missing_or_ambiguous")
        observed = matches[0]
        if (type(observed.get("size")) is not int
                or observed["size"] != size
                or observed.get("digest") != "sha256:" + digest):
            raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:" + name
                             + ":upstream_release_metadata")


# Dated official nflverse archive observation, NOT a replacement for frozen CSV pins.
# Values observed in upstream release metadata at 2026-11-12; the RDS format
# is not interchangeable with any existing stats_player or players.csv bytes.
ARCHIVE_OBSERVATION = {
    "repository": "nflverse/nflverse-data-archives",
    "tag_name": "archive-2026-11-05",
    "id": 435763302,
    "assets": {
        "players.rds": (714885750, 2316624, "ca240520205478165be1876ef0c1714beddf2b71f2acd9d145d646fd1fe471d9"),
        "pfr_rosters.rds": (714885106, 1388682, "2f9ee1d330f1e61f27f93f702ada87feaa1f711bb1b0486b9a5fd09e835ca93b"),
        "stats_player_week_2021.rds": (714898115, 918843, "ebbb39717338ae52be894c35c7e76e27517863a22bf229e83ae670ea2bff8907"),
        "stats_player_week_2022.rds": (714898134, 903737, "31e4902b0237fabf8696389640fb43da0e595660ae50111fa793342d03512654"),
        "stats_player_week_2023.rds": (714898137, 902405, "647af0155ab260845214e9f5a6fdc6e190ef0ef497fd7d65140318826726aa3e"),
        "stats_player_week_2024.rds": (714898152, 905522, "7cb3f8f10fecd9ba0fac72be60c4e18442a03248ed13e9a50f51128ae30cde67"),
        "stats_player_week_2025.rds": (714884067, 992521, "f658cb209d65af4a0521cd482fc9c6da24ebdfd3a61b77c9275076d6f2eaecb7"),
    },
}


def observe_official_archive_metadata(release: dict) -> dict:
    """Check an independently dated RDS archive without adopting any source rows.

    This is release *metadata*, not a byte-level proof of its RDS payload,
    permission to convert RDS to CSV, or verified player-game labels. A
    mismatch is a typed HOLD; no source pin or training path may be changed.
    """
    expected = ARCHIVE_OBSERVATION
    if (not isinstance(release, dict)
            or type(release.get("id")) is not int
            or release["id"] != expected["id"]
            or release.get("tag_name") != expected["tag_name"]
            or not isinstance(release.get("assets"), list)):
        raise ValueError("NFL_PROP_ACQUIRE_PINS_INVALID:archive_metadata")
    for name, (asset_id, size, digest) in expected["assets"].items():
        matching = [a for a in release["assets"]
                    if isinstance(a, dict) and a.get("name") == name]
        if len(matching) != 1:
            raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:archive:" + name
                             + ":missing_or_ambiguous")
        actual = matching[0]
        if (type(actual.get("id")) is not int or actual["id"] != asset_id
                or type(actual.get("size")) is not int or actual["size"] != size
                or actual.get("digest") != "sha256:" + digest):
            raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:archive:" + name)
    return {
        "status": "HOLD_ARCHIVE_RDS_NOT_EQUIVALENT_TO_FROZEN_CSV",
        "archive_repository": expected["repository"],
        "archive_tag": expected["tag_name"],
        "archive_release_id": expected["id"],
        "metadata_matched_asset_count": len(expected["assets"]),
        "release_metadata_matched": True,
        "source_bytes_sha256_verified": False,
        "equivalent_frozen_csv_proven": False,
        "historical_player_identities_reconciled": False,
        "source_rights_independently_approved": False,
        "training_authorized": False,
        "certification_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }



def verify_file(path: Path, digest: str, size: int) -> None:
    """Verify exact downloaded bytes; never accept a symlink or guessed zero."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("NFL_PROP_ACQUIRE_FILE_MISSING:" + path.name)
    hashed = hashlib.sha256()
    count = 0
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            hashed.update(chunk)
            count += len(chunk)
    if count != size or hashed.hexdigest() != digest:
        raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:" + path.name)


def acquire_one(relative: str, digest: str, size: int, dest: Path, *,
                verify_only: bool = False, open_url=None, timeout: int = 120) -> dict:
    """Acquire official bytes without replacing existing or racing frozen targets."""
    parts = relative.split("/")
    if len(parts) != 2 or parts[0] not in OFFICIAL_SIZE_BYTES or "/" in parts[1] or ".." in relative:
        raise ValueError("NFL_PROP_ACQUIRE_PATH_INVALID")
    dataset, name = parts
    if dataset == "pbp" and name not in {f"play_by_play_{year}.csv.gz" for year in range(2021, 2026)}:
        raise ValueError("NFL_PROP_ACQUIRE_PATH_INVALID")
    target = dest / relative
    if target.exists() or target.is_symlink():
        verify_file(target, digest, size)
        return {"file": relative, "size_bytes": size, "sha256": digest, "action": "verified_existing"}
    if verify_only:
        raise ValueError("NFL_PROP_ACQUIRE_FILE_MISSING:" + name)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(prefix=".frozen-source-", suffix=".part", dir=target.parent)
    os.close(fd)
    temp = Path(part)
    try:
        url = f"https://github.com/nflverse/nflverse-data/releases/download/{dataset}/{name}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "WOW-V17-public-source-research/1.0", "Accept": "application/octet-stream",
        })
        try:
            response = (open_url or _trusted_release_urlopen)(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise ValueError("NFL_PROP_ACQUIRE_HTTP_ERROR:" + name + ":" + str(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError("NFL_PROP_ACQUIRE_NETWORK_UNAVAILABLE:" + name) from exc
        with response, temp.open("wb") as dst:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in RELEASE_HOSTS:
                raise ValueError("NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED:" + name)
            hashed = hashlib.sha256()
            count = 0
            while True:
                try:
                    chunk = response.read(1024 * 1024)
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    raise ValueError("NFL_PROP_ACQUIRE_NETWORK_UNAVAILABLE:" + name) from exc
                if not chunk:
                    break
                count += len(chunk)
                if count > size:
                    raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:" + name)
                hashed.update(chunk)
                dst.write(chunk)
        if count != size or hashed.hexdigest() != digest:
            raise ValueError("NFL_PROP_ACQUIRE_DIGEST_MISMATCH:" + name)
        try:
            os.link(temp, target)
        except FileExistsError as exc:
            raise ValueError("NFL_PROP_ACQUIRE_TARGET_RACE:" + name) from exc
        verify_file(target, digest, size)
        return {"file": relative, "size_bytes": size, "sha256": digest, "action": "downloaded_verified"}
    finally:
        temp.unlink(missing_ok=True)


def main() -> int:
    """Print success only after all 11 immutable source assets have passed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    receipts = [
        acquire_one(name, digest, size, args.dest, verify_only=args.verify_only)
        for name, digest, size in frozen_assets()
    ]
    print(json.dumps({
        "schema_version": "WOW_NFL_OFFLINE_SOURCE_ACQUISITION_V1",
        "asset_count": len(receipts), "verified_assets": receipts,
        "total_bytes_verified": sum(x["size_bytes"] for x in receipts),
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "all_sources_sha256_verified": True,
        "source_rights_independently_approved": False,
        "candidate_models_fitted": False, "specialists_certified": False,
        "registration_attempted": False, "can_execute": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
