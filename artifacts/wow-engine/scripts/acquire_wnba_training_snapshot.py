#!/usr/bin/env python3
"""Acquire the current SportsDataverse WNBA training evidence bundle.

The upstream SportsDataverse release tags are mutable. This acquisition-only
boundary therefore verifies the exact current bytes for all three 2026 inputs
used by the WNBA offline fit/readiness path and writes a provenance manifest.
Training/certification must consume a WOW-owned frozen snapshot of these bytes,
not the mutable upstream release URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LICENSE_ID = "CC-BY-4.0"
LICENSE_URL = "https://github.com/sportsdataverse/wehoop-wnba-stats-data/blob/main/LICENSE.md"

ASSETS = (
    {
        "name": "player_game_logs_2026.csv",
        "role": "PLAYER_GAME_CHRONOLOGY_AND_OUTCOMES",
        "url": (
            "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
            "wnba_stats_player_game_logs/player_game_logs_2026.csv"
        ),
        "release_tag": "wnba_stats_player_game_logs",
        "release_id": 320946352,
        "release_immutable": False,
        "asset_id": 544667346,
        "asset_created_at": "2026-09-04T17:29:09Z",
        "sha256": "e1d2ecfd3051953fe56ba0d6de59e2d300576337cd9f1a58157e8f43a9371098",
    },
    {
        "name": "player_boxscores_2026.csv",
        "role": "PLAYER_ROLE_AND_STARTER_EVIDENCE",
        "url": (
            "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
            "wnba_stats_player_boxscores/player_boxscores_2026.csv"
        ),
        "release_tag": "wnba_stats_player_boxscores",
        "release_id": 97828473,
        "release_immutable": False,
        "asset_id": 544667191,
        "asset_created_at": "2026-09-04T17:28:58Z",
        "sha256": "7984ab407933093f7462ce63d081cba5549f5ff5b6cca123d64f07803a682f66",
    },
    {
        "name": "wnba_schedule_2026.csv",
        "role": "EXACT_HISTORICAL_EVENT_START_TIME",
        "url": (
            "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
            "espn_wnba_schedules/wnba_schedule_2026.csv"
        ),
        "release_tag": "espn_wnba_schedules",
        "release_id": 97421041,
        "release_immutable": False,
        "asset_id": 544252913,
        "asset_created_at": "2026-09-04T11:40:28Z",
        "sha256": "e5553a01ef64b24ac88dd4283144aa29f7cf9facf61a40f1aba8ec04b037b244",
    },
)

BUNDLE_ID = "wnba-2026-20260904"


def _fetch(asset: dict[str, object]) -> bytes:
    request = urllib.request.Request(
        str(asset["url"]),
        headers={"User-Agent": "WOW-V17-WNBA-SNAPSHOT-ACQUISITION/1.0"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    expected = str(asset["sha256"])
    if digest != expected:
        raise RuntimeError(
            f"SOURCE_HASH_MISMATCH asset={asset['name']} expected={expected} actual={digest}"
        )
    return payload


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=root / "data" / "wnba-source-freeze" / BUNDLE_ID,
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    manifest_assets: list[dict[str, object]] = []
    for spec in ASSETS:
        payload = _fetch(spec)
        (args.out_dir / str(spec["name"])).write_bytes(payload)
        manifest_assets.append(
            {
                **spec,
                "source_size_bytes": len(payload),
                "source_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    manifest = {
        "schema_version": "WOW_WNBA_SOURCE_SNAPSHOT_V1",
        "bundle_id": BUNDLE_ID,
        "sport": "WNBA",
        "provider": "SPORTSDATAVERSE_WNBA_STATS",
        "evidence_domain": "SPORTING",
        "assets": manifest_assets,
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
