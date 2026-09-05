"""Immutable, fail-closed loader for the WOW-owned WNBA 2026 training bundle.

The upstream SportsDataverse release assets are mutable. Model development must
therefore consume the Git-pinned compressed snapshot committed under
``data/source_snapshots/wnba``. This module verifies the manifest, CC-BY
provenance, and raw SHA-256 before returning any bytes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

BUNDLE_ID = "wnba-2026-20260904"
PROVIDER = "SPORTSDATAVERSE_WNBA_STATS"
LICENSE_ID = "CC-BY-4.0"
SNAPSHOT_MANIFEST_ID = f"data/source_snapshots/wnba/{BUNDLE_ID}/snapshot_manifest.json"
SOURCE_DIR = Path(__file__).resolve().parent / "data" / "source_snapshots" / "wnba" / BUNDLE_ID

EXPECTED_SHA256 = {
    "player_game_logs_2026.csv": "e1d2ecfd3051953fe56ba0d6de59e2d300576337cd9f1a58157e8f43a9371098",
    "player_boxscores_2026.csv": "7984ab407933093f7462ce63d081cba5549f5ff5b6cca123d64f07803a682f66",
    "wnba_schedule_2026.csv": "e5553a01ef64b24ac88dd4283144aa29f7cf9facf61a40f1aba8ec04b037b244",
}


class WNBATrainingSourceError(RuntimeError):
    pass


def _manifest(source_dir: Path = SOURCE_DIR) -> dict[str, Any]:
    path = source_dir / "snapshot_manifest.json"
    if not path.is_file():
        raise WNBATrainingSourceError("WNBA_FROZEN_SOURCE_MANIFEST_MISSING")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WNBATrainingSourceError("WNBA_FROZEN_SOURCE_MANIFEST_INVALID") from exc

    checks = {
        "schema_version": "WOW_WNBA_SOURCE_SNAPSHOT_V1",
        "bundle_id": BUNDLE_ID,
        "sport": "WNBA",
        "provider": PROVIDER,
        "evidence_domain": "SPORTING",
        "license_id": LICENSE_ID,
    }
    for field, expected in checks.items():
        if str(doc.get(field) or "") != expected:
            raise WNBATrainingSourceError(f"WNBA_FROZEN_SOURCE_MANIFEST_{field.upper()}_INVALID")
    if doc.get("attribution_required") is not True:
        raise WNBATrainingSourceError("WNBA_FROZEN_SOURCE_ATTRIBUTION_NOT_ENFORCED")
    if doc.get("grants_model_capability") is not False:
        raise WNBATrainingSourceError("WNBA_FROZEN_SOURCE_CANNOT_GRANT_MODEL_CAPABILITY")
    if doc.get("probability_publishable") is not False or doc.get("can_execute") is not False:
        raise WNBATrainingSourceError("WNBA_FROZEN_SOURCE_GOVERNANCE_INVALID")

    assets = doc.get("assets")
    if not isinstance(assets, list):
        raise WNBATrainingSourceError("WNBA_FROZEN_SOURCE_ASSET_MANIFEST_INVALID")
    index = {str(item.get("name") or ""): item for item in assets if isinstance(item, dict)}
    if set(EXPECTED_SHA256) - set(index):
        raise WNBATrainingSourceError("WNBA_FROZEN_SOURCE_ASSET_MANIFEST_INCOMPLETE")
    for name, expected in EXPECTED_SHA256.items():
        item = index[name]
        manifest_sha = str(item.get("source_sha256") or item.get("sha256") or "")
        if manifest_sha != expected:
            raise WNBATrainingSourceError(f"WNBA_FROZEN_SOURCE_MANIFEST_HASH_MISMATCH:{name}")
    return doc


def _load(name: str, source_dir: Path = SOURCE_DIR) -> bytes:
    if name not in EXPECTED_SHA256:
        raise WNBATrainingSourceError(f"WNBA_FROZEN_SOURCE_UNKNOWN_ASSET:{name}")
    _manifest(source_dir)
    path = source_dir / f"{name}.gz"
    if not path.is_file():
        raise WNBATrainingSourceError(f"WNBA_FROZEN_SOURCE_ASSET_MISSING:{name}")
    try:
        payload = gzip.decompress(path.read_bytes())
    except (OSError, EOFError) as exc:
        raise WNBATrainingSourceError(f"WNBA_FROZEN_SOURCE_ASSET_CORRUPT:{name}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    expected = EXPECTED_SHA256[name]
    if actual != expected:
        raise WNBATrainingSourceError(
            f"WNBA_FROZEN_SOURCE_HASH_MISMATCH:{name}:expected={expected}:actual={actual}"
        )
    return payload


def load_player_game_logs(source_dir: Path = SOURCE_DIR) -> bytes:
    return _load("player_game_logs_2026.csv", source_dir)


def load_player_boxscores(source_dir: Path = SOURCE_DIR) -> bytes:
    return _load("player_boxscores_2026.csv", source_dir)


def load_schedule(source_dir: Path = SOURCE_DIR) -> bytes:
    return _load("wnba_schedule_2026.csv", source_dir)


def source_metadata(source_dir: Path = SOURCE_DIR) -> dict[str, Any]:
    doc = _manifest(source_dir)
    return {
        "bundle_id": BUNDLE_ID,
        "provider": PROVIDER,
        "evidence_domain": "SPORTING",
        "license_id": LICENSE_ID,
        "license_url": doc.get("license_url"),
        "attribution_required": True,
        "snapshot_manifest_path": SNAPSHOT_MANIFEST_ID,
        "asset_sha256": dict(EXPECTED_SHA256),
        "grants_model_capability": False,
        "probability_publishable": False,
        "can_execute": False,
    }
