#!/usr/bin/env python3
"""Acquire the current SportsDataverse WNBA player-game corpus for freezing.

This is an acquisition-only boundary. It proves the exact upstream bytes and
writes them plus a provenance manifest to an artifact directory. Training must
consume a WOW-owned frozen snapshot, not the mutable upstream release URL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "wnba_stats_player_game_logs/player_game_logs_2026.csv"
)
EXPECTED_SHA256 = "e1d2ecfd3051953fe56ba0d6de59e2d300576337cd9f1a58157e8f43a9371098"
UPSTREAM_RELEASE_ID = 320946352
UPSTREAM_ASSET_ID = 544667346
UPSTREAM_ASSET_CREATED_AT = "2026-09-04T17:29:09Z"
LICENSE_ID = "CC-BY-4.0"
LICENSE_URL = "https://github.com/sportsdataverse/wehoop-wnba-stats-data/blob/main/LICENSE.md"


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=root / "data" / "wnba-source-freeze" / EXPECTED_SHA256[:12],
    )
    args = parser.parse_args()

    request = urllib.request.Request(
        UPSTREAM_URL,
        headers={"User-Agent": "WOW-V17-WNBA-SNAPSHOT-ACQUISITION/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()

    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(
            f"SOURCE_HASH_MISMATCH expected={EXPECTED_SHA256} actual={digest}"
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = args.out_dir / "player_game_logs_2026.csv"
    snapshot.write_bytes(payload)

    manifest = {
        "schema_version": "WOW_WNBA_SOURCE_SNAPSHOT_V1",
        "sport": "WNBA",
        "provider": "SPORTSDATAVERSE_WNBA_STATS",
        "evidence_domain": "SPORTING",
        "upstream_url": UPSTREAM_URL,
        "upstream_release_id": UPSTREAM_RELEASE_ID,
        "upstream_release_immutable": False,
        "upstream_asset_id": UPSTREAM_ASSET_ID,
        "upstream_asset_created_at": UPSTREAM_ASSET_CREATED_AT,
        "source_sha256": digest,
        "source_size_bytes": len(payload),
        "license_id": LICENSE_ID,
        "license_url": LICENSE_URL,
        "attribution_required": True,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "WOW-owned immutable freeze candidate; no model capability granted",
        "grants_model_capability": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    (args.out_dir / "snapshot_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
