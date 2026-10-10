"""NCAAB public-source-pinned numeric challenger replay (Issue #1644).

Deliberately SEPARATE from the frozen Supabase row replay. Rebuilds original
source assets (CC-BY-4.0), validates each published byte SHA, refits the
unchanged result-form V1 trainer and compares the research candidate's frozen
dataset, fitted artifact, calibrator and untouched holdout metrics.

This independent source challenger does NOT imply that persisted DB rows were
re-read or that the candidate is certified or publishable.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from v17.binary_candidate_lifecycle import BinaryCandidate, train_binary_candidate
from v17.ncaab_sportsdataverse_candidate import (
    FEATURE_NAMES, MODEL_FAMILY, SEASONS, SOURCE_LICENSE,
    _json_hash, build_training_rows, fetch_games,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
EXPECTED_CANDIDATE_ID = "e874cd6d-5ea3-4b85-a1f0-ac239e285704"
EXPECTED_DATASET_HASH = "c5e99b10dbb4b676670feb3af943fa4666349fb396ec5ef344f966ec04239033"
EXPECTED_ARTIFACT_CHECKSUM = "c86e11cf76f07deca94b6e3fe539071487d052b2e4587f2a9ce9547805b07801"
EXPECTED_CALIBRATOR_SHA256 = "31845f68c734ab797d00b0b4f4070e5bd04cc0b50c9748b04553f2a092238ad2"
EXPECTED_SOURCE_HASHES = {
    2022: "1b498227c26a40e2793c906fd293c070081d9d8a9308d0247cda537388d268b7",
    2023: "eaf4772791f86eb8bc3b9df30e5260d5d6deec319928eca7daada86b68e7383a",
    2024: "817917bf2424ad91021ce4b09a98244b5fdb6dae821c9d385607cfda70aca0ae",
    2025: "790b3fa4387870a13f6f9bbb9841a43ca94a4d96c2a33dc50708f54b428d7172",
    2026: "14fe7c7b1617c1ba2fc8fe555cc0123403fc229e2afc8bf537fe36615ae6ff7e",
}
EXPECTED_SOURCE_ROW_COUNTS = {
    2022: 4637, 2023: 5740, 2024: 5834, 2025: 5938, 2026: 5993,
}
EXPECTED_METRICS = {
    "train_n": 16885, "calibration_n": 5628, "test_n": 5629,
    "calibrated_brier": 0.19414882129646888,
    "ece": 0.032225461158707815,
    "raw_brier": 0.19414882129646888,
    "baseline_brier": 0.23103613629269773,
}
METRIC_TOLERANCE = 1e-9
RECEIPT_VERSION = "NCAAB_PUBLIC_SOURCE_PINNED_NUMERICAL_REPLAY_V1"


def verify_public_source(
    sources: Sequence[Mapping[str, Any]],
    metadata: Sequence[Mapping[str, Any]],
    fitted: BinaryCandidate,
    *,
    expected_hashes: Mapping[int, str] = EXPECTED_SOURCE_HASHES,
    expected_counts: Mapping[int, int] = EXPECTED_SOURCE_ROW_COUNTS,
    expected_dataset_hash: str = EXPECTED_DATASET_HASH,
    expected_artifact_checksum: str = EXPECTED_ARTIFACT_CHECKSUM,
    expected_calibrator_sha256: str = EXPECTED_CALIBRATOR_SHA256,
    expected_metrics: Mapping[str, int | float] = EXPECTED_METRICS,
) -> dict[str, Any]:
    mismatches = []
    def hold(code: str, detail: str) -> None:
        mismatches.append({"code": code, "detail": detail})

    if len(sources) != len(expected_hashes):
        hold("NCAAB_SOURCE_ASSET_COUNT_MISMATCH", str(len(sources)))
    seen_seasons = set()
    for asset in sources:
        season = asset.get("season")
        if season in seen_seasons:
            hold("NCAAB_SOURCE_DUPLICATE_SEASON", str(season))
        seen_seasons.add(season)
        if season not in expected_hashes or asset.get("sha256") != expected_hashes.get(season):
            hold("NCAAB_SOURCE_PIN_MISMATCH", str(season))
        if asset.get("license") != SOURCE_LICENSE:
            hold("NCAAB_SOURCE_LICENSE_MISMATCH", str(season))
        url = str(asset.get("url") or "")
        if url != (
            "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
            f"espn_mens_college_basketball_team_boxscores/team_box_{season}.csv"
        ):
            hold("NCAAB_SOURCE_URL_MISMATCH", str(season))
    for season in expected_hashes:
        if season not in seen_seasons:
            hold("NCAAB_SOURCE_SEASON_MISSING", str(season))

    counts: Counter[int] = Counter()
    pin_to_season = {digest: season for season, digest in expected_hashes.items()}
    for item in metadata:
        manifest = item.get("source_manifest") or {}
        season = pin_to_season.get(manifest.get("source_sha256"))
        if season is None or manifest.get("market_features_used") is not False:
            hold("NCAAB_SOURCE_ROW_PROVENANCE_MISMATCH", str(manifest.get("game_id")))
        else:
            counts[season] += 1
    for season, expected in expected_counts.items():
        if counts[season] != expected:
            hold("NCAAB_SOURCE_ROW_COUNT_MISMATCH", f"{season}:{counts[season]}!={expected}")
    expected_rows = sum(expected_counts.values())
    if len(metadata) != expected_rows:
        hold("NCAAB_SOURCE_TOTAL_ROWS_MISMATCH", str(len(metadata)))

    metrics = asdict(fitted.metrics)
    checksum = _json_hash(dict(fitted.artifact_payload))
    calibrator_sha = _json_hash(dict(fitted.calibrator_payload))
    if fitted.model_family != MODEL_FAMILY or tuple(fitted.feature_names) != FEATURE_NAMES:
        hold("NCAAB_SOURCE_SPECIALIST_MISMATCH", fitted.model_family)
    if fitted.dataset_hash != expected_dataset_hash:
        hold("NCAAB_SOURCE_DATASET_HASH_MISMATCH", fitted.dataset_hash)
    if checksum != expected_artifact_checksum:
        hold("NCAAB_SOURCE_ARTIFACT_CHECKSUM_MISMATCH", checksum)
    if calibrator_sha != expected_calibrator_sha256:
        hold("NCAAB_SOURCE_CALIBRATOR_CHECKSUM_MISMATCH", calibrator_sha)
    for name, expected in expected_metrics.items():
        if name not in metrics or abs(float(metrics[name]) - float(expected)) > METRIC_TOLERANCE:
            hold("NCAAB_SOURCE_METRIC_MISMATCH", name)
    if (fitted.can_execute is not False or fitted.probability_publishable is not False
            or fitted.automatic_certification is not False or fitted.automatic_promotion is not False):
        hold("NCAAB_SOURCE_GOVERNANCE_VIOLATION", "candidate")

    receipt = {
        "receipt_version": RECEIPT_VERSION, "incident": 1644,
        "candidate_id": EXPECTED_CANDIDATE_ID,
        "source_replay_not_db_frozen_replay": True, "research_only": True,
        "status": "SOURCE_PINNED_REPLAY_REPRODUCED" if not mismatches else "SOURCE_PINNED_REPLAY_MISMATCH_HOLD",
        "observed": {
            "source_hashes": {str(a["season"]): str(a.get("sha256")) for a in sources},
            "row_counts_by_season": {str(k): counts[k] for k in sorted(counts)},
            "training_rows": len(metadata),
            "dataset_hash": fitted.dataset_hash,
            "artifact_checksum": checksum,
            "calibrator_sha256": calibrator_sha, "metrics": metrics,
            "research_screen_pass": fitted.research_screen_pass,
        },
        "expected": {
            "dataset_hash": expected_dataset_hash,
            "artifact_checksum": expected_artifact_checksum,
            "calibrator_sha256": expected_calibrator_sha256,
            "source_hashes": {str(k): v for k, v in expected_hashes.items()},
        },
        "mismatches": mismatches,
        "certification": False, "promotion": False,
        "probability_publishable": False, "can_execute": False,
    }
    receipt["receipt_sha256"] = sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return receipt


def main() -> int:
    try:
        games, sources = fetch_games(seasons=SEASONS)
        # No refit until all five source assets match their exact persisted SHA.
        if len(sources) != len(EXPECTED_SOURCE_HASHES) or any(
            item.get("sha256") != EXPECTED_SOURCE_HASHES.get(item.get("season"))
            for item in sources
        ):
            print(json.dumps({
                "status": "SOURCE_PINNED_REPLAY_MISMATCH_HOLD",
                "code": "NCAAB_SOURCE_PIN_MISMATCH", "research_only": True,
                "can_execute": False, "probability_publishable": False,
            }, sort_keys=True))
            return 1
        rows, metadata = build_training_rows(games)
        fitted = train_binary_candidate(
            rows, model_family=MODEL_FAMILY, feature_names=FEATURE_NAMES, min_rows=500
        )
        receipt = verify_public_source(sources, metadata, fitted)
    except Exception as exc:
        # Never print payloads, tokens or URLs that could contain provider metadata.
        print(json.dumps({"status": "BLOCKED_WITH_EXACT_REASON",
                          "code": "NCAAB_SOURCE_REPLAY_INPUT_OR_RUNTIME_BLOCKED",
                          "exception_type": type(exc).__name__,
                          "can_execute": False, "probability_publishable": False}, sort_keys=True))
        return 3
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "SOURCE_PINNED_REPLAY_REPRODUCED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
