#!/usr/bin/env python3
"""Fit new WNBA candidate artifacts from the WOW-owned immutable source bundle.

This preserves the existing audited offset-Poisson fitting implementation while
changing only the evidence boundary and candidate lineage. It does not register,
promote, activate, publish, or execute any model.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from scripts import train_wnba_props as base
from scripts import train_wnba_props_offset as offset
from wnba_training_source import EXPECTED_SHA256, load_player_game_logs, source_metadata

ARTIFACT_DATE = "2026_09_05"
CERTIFICATION_DATE = "2026-09-05"
SOURCE_ID = "WOW_GIT_FROZEN_SPORTSDATAVERSE_WNBA_2026_20260904"


def main() -> int:
    out_dir = Path(
        os.environ.get(
            "WNBA_ARTIFACT_OUT_DIR",
            Path(__file__).resolve().parent.parent / "data" / "wnba-frozen-candidate",
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # The existing fitter reads these constants at fit time when deriving the
    # dataset hash, artifact version, and payload provenance. Point them at the
    # immutable WOW snapshot rather than the superseded mutable release bytes.
    base.PLAYER_LOG_SHA256 = EXPECTED_SHA256["player_game_logs_2026.csv"]
    base.PLAYER_LOG_URL = SOURCE_ID
    base.ARTIFACT_DATE = ARTIFACT_DATE

    payload = load_player_game_logs()
    snapshot_meta = source_metadata()
    artifacts: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "model_family": base.MODEL_FAMILY,
        "source": SOURCE_ID,
        "source_bundle_id": snapshot_meta["bundle_id"],
        "source_provider": snapshot_meta["provider"],
        "source_license_id": snapshot_meta["license_id"],
        "source_sha256": base.PLAYER_LOG_SHA256,
        "training_code_sha": os.environ.get("GITHUB_SHA", "UNRESOLVED_TRAINING_CODE_SHA"),
        "routes": {},
        "artifact_registration_status": "NOT_ATTEMPTED",
        "runtime_model_status": "MODEL_UNAVAILABLE",
        "probability_publishable": False,
        "can_execute": False,
    }

    all_pass = True
    for route, (_, aliases) in base.STAT_ROUTES.items():
        games, source_meta = base._extract_games(payload, aliases)
        source_meta = {**source_meta, "source_snapshot": snapshot_meta}
        artifact, metrics = offset.fit_one(route, games, source_meta)
        stat_short = base.STAT_ROUTES[route][0]
        artifact["certification_id"] = f"WNBA-{stat_short}-OFFLINE-{CERTIFICATION_DATE}"
        artifact["source_snapshot_bundle_id"] = snapshot_meta["bundle_id"]
        artifact["source_provider"] = snapshot_meta["provider"]
        artifact["source_license_id"] = snapshot_meta["license_id"]
        artifact["source_attribution_required"] = True
        artifacts.append(artifact)
        report["routes"][route] = metrics
        all_pass = all_pass and metrics["validation_status"] == "PASS"

    report["training_status"] = "PASS" if all_pass else "BLOCKED"
    (out_dir / "wow_wnba_prop_artifacts_v1.json").write_text(
        json.dumps(artifacts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "wow_wnba_prop_training_report_v1.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all_pass else 3


if __name__ == "__main__":
    sys.exit(main())
