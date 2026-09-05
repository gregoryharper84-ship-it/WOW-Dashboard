#!/usr/bin/env python3
"""Fit reproducible WNBA candidate artifacts from the WOW-owned source bundle.

This preserves the existing audited offset-Poisson fitting implementation while
changing only the evidence boundary, candidate lineage, and deterministic
serialization boundary. It does not register, promote, activate, publish, or
execute any model.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts import train_wnba_props as base
from scripts import train_wnba_props_offset as offset
from wnba_training_source import EXPECTED_SHA256, load_player_game_logs, source_metadata

ARTIFACT_DATE = "2026_09_05"
CERTIFICATION_DATE = "2026-09-05"
SOURCE_ID = "WOW_GIT_FROZEN_SPORTSDATAVERSE_WNBA_2026_20260904"
CANONICAL_FLOAT_DECIMALS = 12


def _canonicalize_numbers(value: Any) -> Any:
    """Return a JSON-compatible value with deterministic finite float precision."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeError("WNBA_NONFINITE_FLOAT_CANNOT_BE_CANONICALIZED")
        rounded = round(value, CANONICAL_FLOAT_DECIMALS)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, dict):
        return {key: _canonicalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize_numbers(item) for item in value]
    return value


def canonicalize_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize fitted numerics and recompute checksum from canonical payload."""
    out = _canonicalize_numbers(deepcopy(artifact))
    payload = out.get("artifact_payload")
    if not isinstance(payload, dict):
        raise RuntimeError("WNBA_ARTIFACT_PAYLOAD_MISSING_FOR_CANONICALIZATION")
    out["artifact_checksum"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    out["numeric_canonicalization_decimals"] = CANONICAL_FLOAT_DECIMALS
    return out


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
        "numeric_canonicalization_decimals": CANONICAL_FLOAT_DECIMALS,
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
        artifact, _metrics = offset.fit_one(route, games, source_meta)
        stat_short = base.STAT_ROUTES[route][0]
        artifact["certification_id"] = f"WNBA-{stat_short}-OFFLINE-{CERTIFICATION_DATE}"
        artifact["source_snapshot_bundle_id"] = snapshot_meta["bundle_id"]
        artifact["source_provider"] = snapshot_meta["provider"]
        artifact["source_license_id"] = snapshot_meta["license_id"]
        artifact["source_attribution_required"] = True
        artifact = canonicalize_artifact(artifact)
        metrics = artifact["validation_metrics"]
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
