from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from wnba_training_source import (
    EXPECTED_SHA256,
    WNBATrainingSourceError,
    load_player_boxscores,
    load_player_game_logs,
    load_schedule,
    source_metadata,
)


def _write_bundle(tmp_path: Path, *, corrupt: str | None = None, manifest_override: dict | None = None) -> Path:
    source = Path(__file__).resolve().parent / "data" / "source_snapshots" / "wnba" / "wnba-2026-20260904"
    target = tmp_path / "bundle"
    target.mkdir()
    manifest = json.loads((source / "snapshot_manifest.json").read_text(encoding="utf-8"))
    if manifest_override:
        manifest.update(manifest_override)
    (target / "snapshot_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in EXPECTED_SHA256:
        raw = gzip.decompress((source / f"{name}.gz").read_bytes())
        if name == corrupt:
            raw += b"tamper"
        (target / f"{name}.gz").write_bytes(gzip.compress(raw, mtime=0))
    return target


def test_committed_bundle_loads_and_preserves_governance() -> None:
    assert load_player_game_logs()
    assert load_player_boxscores()
    assert load_schedule()
    meta = source_metadata()
    assert meta["provider"] == "SPORTSDATAVERSE_WNBA_STATS"
    assert meta["license_id"] == "CC-BY-4.0"
    assert meta["attribution_required"] is True
    assert meta["grants_model_capability"] is False
    assert meta["probability_publishable"] is False
    assert meta["can_execute"] is False


def test_tampered_raw_asset_fails_closed(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, corrupt="player_game_logs_2026.csv")
    with pytest.raises(WNBATrainingSourceError, match="HASH_MISMATCH"):
        load_player_game_logs(bundle)


def test_manifest_cannot_claim_model_capability(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, manifest_override={"grants_model_capability": True})
    with pytest.raises(WNBATrainingSourceError, match="CANNOT_GRANT_MODEL_CAPABILITY"):
        source_metadata(bundle)


def test_manifest_requires_cc_by_attribution(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, manifest_override={"attribution_required": False})
    with pytest.raises(WNBATrainingSourceError, match="ATTRIBUTION_NOT_ENFORCED"):
        source_metadata(bundle)
