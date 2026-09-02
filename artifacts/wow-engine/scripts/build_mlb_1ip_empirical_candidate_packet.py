"""Build a lineage-bound SHADOW candidate for the MLB 1IP empirical PMF.

Research only: no database writes, promotion, activation, publication, or
execution. The candidate is trained on a deterministic 2024 sample and
validated on a deterministic 2025 temporal holdout using only the line grid
actually evaluated in shadow research.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from mlb_1ip_empirical_pmf import (
    ARTIFACT_FORMAT,
    CALIBRATOR_VERSION,
    FEATURE_TRANSFORM_VERSION,
    MODEL_FAMILY,
    fit_empirical_pmf,
    score_empirical_pmf,
)
from mlb_1ip_training_dataset import game_training_rows, season_game_pks

TRAIN_SEASON = 2024
VALIDATION_SEASON = 2025
TRAIN_GAMES = 700
VALIDATION_GAMES = 700
VALIDATION_LINES = (11.5, 13.5, 15.5, 17.5, 19.5, 21.5)
MAX_BRIER = 0.25
MAX_ECE = 0.06
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
SPORT = "MLB"
STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
# The production prop registry currently resolves exact stat routes through the
# shared PROP_FEATURES_V1 schema key. 1IP-specific semantics remain pinned by
# feature_transform_version rather than inventing an unreachable schema key.
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
SPECIALIST_VERSION = "wow.mlb-first-inning-pitch-count-expert@1"


def _sha(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _even_sample(values: list[int], n: int) -> list[int]:
    if n >= len(values):
        return list(values)
    if n <= 1:
        return [values[0]]
    idx = [round(i * (len(values) - 1) / (n - 1)) for i in range(n)]
    return [values[i] for i in idx]


def _collect(season: int, games: int):
    selected = _even_sample(season_game_pks(season), games)
    rows = []
    manifests = []
    for i, game_pk in enumerate(selected, 1):
        game_rows, manifest = game_training_rows(game_pk)
        rows.extend(game_rows)
        manifests.append(manifest)
        if i % 50 == 0:
            print(f"season={season} games={i}/{len(selected)} rows={len(rows)}", flush=True)
    return rows, manifests, selected


def _metrics(actual: list[int], predicted: list[float]) -> dict:
    n = len(actual)
    brier = sum((y - p) ** 2 for y, p in zip(actual, predicted)) / n
    ece = 0.0
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        idx = [j for j, p in enumerate(predicted) if (lo <= p < hi) or (i == 9 and p == 1.0)]
        if not idx:
            continue
        confidence = sum(predicted[j] for j in idx) / len(idx)
        accuracy = sum(actual[j] for j in idx) / len(idx)
        ece += len(idx) / n * abs(confidence - accuracy)
    return {
        "validation_rows": n,
        "brier": brier,
        "ece": ece,
        "gates_passed": brier <= MAX_BRIER and ece <= MAX_ECE,
    }


def main() -> None:
    output_dir = Path(os.environ.get("MLB_1IP_RESEARCH_OUT", "research-output/mlb-1ip-empirical-candidate"))
    output_dir.mkdir(parents=True, exist_ok=True)
    code_sha = os.environ.get("GITHUB_SHA") or "0" * 40

    train_rows, train_manifests, train_games = _collect(TRAIN_SEASON, TRAIN_GAMES)
    validation_rows, validation_manifests, validation_games = _collect(VALIDATION_SEASON, VALIDATION_GAMES)
    artifact_payload = fit_empirical_pmf(train_rows)

    actual: list[int] = []
    predicted: list[float] = []
    assignments = []
    for idx, row in enumerate(validation_rows):
        line = VALIDATION_LINES[idx % len(VALIDATION_LINES)]
        scored = score_empirical_pmf(artifact_payload, line_value=line, side="MORE")
        p = float(scored["P_MORE"])
        if not 0.0 < p < 1.0:
            raise RuntimeError(f"MLB_1IP_EMPIRICAL_PROBABILITY_EXTREME line={line} p={p}")
        y = 1 if row.pitches > line else 0
        actual.append(y)
        predicted.append(p)
        assignments.append({"bf": row.bf, "pitches": row.pitches, "line": line, "actual_more": y, "p_more": p})

    metrics = _metrics(actual, predicted)
    train_manifest_hash = _sha(train_manifests)
    validation_manifest_hash = _sha(validation_manifests)
    split = {
        "train_season": TRAIN_SEASON,
        "validation_season": VALIDATION_SEASON,
        "train_games": train_games,
        "validation_games": validation_games,
        "validation_lines": VALIDATION_LINES,
    }
    split_hash = _sha(split)
    validation_lineage = {
        "artifact_checksum": artifact_payload["artifact_checksum"],
        "training_code_sha": code_sha,
        "scoring_code_sha": code_sha,
        "split_hash": split_hash,
        "source_snapshot_hashes": sorted([train_manifest_hash, validation_manifest_hash]),
        "validation_targets_hash": _sha(actual),
        "validation_probabilities_hash": _sha(predicted),
    }
    validation_lineage["validation_lineage_hash"] = _sha(validation_lineage)

    candidate = {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": f"MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1_{artifact_payload['artifact_checksum'][:12]}",
        "calibrator_version": CALIBRATOR_VERSION,
        "sport": SPORT,
        "stat_type": STAT_TYPE,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "certification_id": None,
        "lifecycle_state": "SHADOW" if metrics["gates_passed"] else "CANDIDATE",
        "training_dataset_hash": _sha([{"bf": r.bf, "pitches": r.pitches} for r in train_rows]),
        "training_code_sha": code_sha,
        "artifact_checksum": artifact_payload["artifact_checksum"],
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_payload": artifact_payload,
        "supported_line_min": min(VALIDATION_LINES),
        "supported_line_max": max(VALIDATION_LINES),
        "training_rows": len(train_rows),
        "validation_metrics": metrics,
        "validation_lineage": validation_lineage,
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    packet = {
        "purpose": "RESEARCH_ONLY_LINEAGE_BOUND_SHADOW_CANDIDATE",
        "candidate": candidate,
        "training": {"season": TRAIN_SEASON, "games": len(train_games), "rows": len(train_rows), "manifest_hash": train_manifest_hash},
        "validation": {"season": VALIDATION_SEASON, "games": len(validation_games), "rows": len(validation_rows), "manifest_hash": validation_manifest_hash, "line_grid": VALIDATION_LINES},
        "certification_ready": False,
        "certification_blockers": [
            "INDEPENDENT_PR_REVIEW_REQUIRED",
            "RUNTIME_ARTIFACT_CONTRACT_REVIEW_REQUIRED",
            "PROMOTION_REQUIRES_DISTINCT_REVIEWER_CONTEXT",
        ],
        "probability_publishable": False,
        "can_execute": False,
    }

    (output_dir / "candidate_packet.json").write_text(json.dumps(packet, indent=2, sort_keys=True))
    (output_dir / "validation_assignments.json").write_text(json.dumps(assignments, indent=2, sort_keys=True))
    (output_dir / "train_manifests.json").write_text(json.dumps(train_manifests, indent=2, sort_keys=True))
    (output_dir / "validation_manifests.json").write_text(json.dumps(validation_manifests, indent=2, sort_keys=True))
    print(json.dumps({
        "model_family": MODEL_FAMILY,
        "training_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "validation_metrics": metrics,
        "lifecycle_state": candidate["lifecycle_state"],
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "probability_publishable": False,
        "can_execute": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
