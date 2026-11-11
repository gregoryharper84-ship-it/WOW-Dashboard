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
