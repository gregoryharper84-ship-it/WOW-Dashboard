#!/usr/bin/env python3
"""Acquire and freeze the pinned SportsDataverse NHL raw research corpus.

The downloader is intentionally narrow: only assets declared in
`nhl_sportsdataverse_snapshot.PINNED_ASSETS` are allowed, every download is
SHA-256 verified before atomic promotion, and the output manifest remains
source-review-pending / research-only / non-publishable / can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nhl_sportsdataverse_snapshot import (  # noqa: E402
    CAN_EXECUTE,
    PINNED_ASSETS,
    PROBABILITY_PUBLISHABLE,
    RESEARCH_ONLY,
    SOURCE_REVIEW_REQUIRED,
    NHLSportsDataverseSnapshotError,
    snapshot_manifest,
    verify_asset_file,
)

USER_AGENT = "WOW-Betting-Intelligence-V17-NHL-SDV-Research/1.0"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_ATTEMPTS = 3


def _download_bytes(url: str, *, timeout: int, attempts: int) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise NHLSportsDataverseSnapshotError(
        "NHL_SDV_DOWNLOAD_FAILED", f"{url}:{type(last_error).__name__}:{last_error}"
    )


def _write_verified_asset(output_dir: Path, asset, payload: bytes) -> None:
    """Write into an isolated temp directory using the exact pinned filename.

    The verifier intentionally rejects filename drift. Keeping the canonical
    basename in a temporary directory lets us verify both identity and digest
    before the atomic rename into the final snapshot directory.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / asset.filename
    temp_dir = Path(tempfile.mkdtemp(prefix=".nhl-sdv-", dir=output_dir))
    temp_path = temp_dir / asset.filename
    try:
        with temp_path.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        verify_asset_file(temp_path, asset)
        os.replace(temp_path, final_path)
        verify_asset_file(final_path, asset)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def acquire_snapshot(
    output_dir: str | Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    reuse_verified: bool = True,
) -> dict[str, object]:
    if CAN_EXECUTE or PROBABILITY_PUBLISHABLE or not RESEARCH_ONLY or not SOURCE_REVIEW_REQUIRED:
        raise NHLSportsDataverseSnapshotError(
            "NHL_SDV_GOVERNANCE_INVARIANT_BROKEN",
            "raw acquisition must remain research-only and source-review-pending",
        )
    if timeout <= 0 or attempts <= 0:
        raise NHLSportsDataverseSnapshotError("NHL_SDV_DOWNLOAD_CONFIG_INVALID", f"{timeout}:{attempts}")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    for asset in PINNED_ASSETS:
        path = root / asset.filename
        if reuse_verified and path.is_file():
            try:
                verify_asset_file(path, asset)
                continue
            except NHLSportsDataverseSnapshotError:
                pass
        payload = _download_bytes(asset.url, timeout=timeout, attempts=attempts)
        _write_verified_asset(root, asset, payload)

    retrieved_at = datetime.now(timezone.utc).isoformat()
    manifest = snapshot_manifest(retrieved_at=retrieved_at)
    manifest_path = root / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "nhl_sportsdataverse_raw",
        help="Destination for pinned raw CSV assets and snapshot_manifest.json",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="Redownload even if a local asset already matches its pinned SHA-256",
    )
    args = parser.parse_args()
    manifest = acquire_snapshot(
        args.output_dir,
        timeout=args.timeout,
        attempts=args.attempts,
        reuse_verified=not args.no_reuse,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
