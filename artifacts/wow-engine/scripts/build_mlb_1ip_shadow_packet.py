"""Build a research-only MLB 1IP temporal shadow validation packet.

This script performs no database writes, no artifact promotion, and no runtime
activation. It uses official MLB Stats API play-by-play, a deterministic
season-wide game sample, the exact candidate fitted constants, and the current
1IP event-tree simulator to produce an independently reviewable SHADOW packet.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random

from mlb_1ip_artifact_pipeline import fit_candidate, validate_candidate
from mlb_1ip_specialist import simulate_1ip_event_tree
from mlb_1ip_training_dataset import game_training_rows, season_game_pks

TRAIN_SEASON = 2024
VALIDATION_SEASON = 2025
TRAIN_GAMES = 700
VALIDATION_GAMES = 250
VALIDATION_LINES = (11.5, 13.5, 15.5, 17.5, 19.5, 21.5)
SIM_TRIALS = 100_000


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _even_sample(values: list[int], n: int) -> list[int]:
    if n >= len(values):
        return list(values)
    if n <= 1:
        return [values[0]]
    # Deterministic season-wide coverage, preserving chronology.
    idx = [round(i * (len(values) - 1) / (n - 1)) for i in range(n)]
    return [values[i] for i in idx]


def _collect(season: int, n_games: int):
    pks = season_game_pks(season)
    selected = _even_sample(pks, n_games)
    rows = []
    manifests = []
    for i, pk in enumerate(selected, 1):
        game_rows, manifest = game_training_rows(pk)
        rows.extend(game_rows)
        manifests.append(manifest)
        if i % 50 == 0:
            print(f"season={season} games={i}/{len(selected)} rows={len(rows)}", flush=True)
    return rows, manifests, selected


def main() -> None:
    out_dir = Path(os.environ.get("MLB_1IP_RESEARCH_OUT", "research-output/mlb-1ip"))
    out_dir.mkdir(parents=True, exist_ok=True)
    code_sha = os.environ.get("GITHUB_SHA") or ("0" * 40)

    train_rows, train_manifests, train_games = _collect(TRAIN_SEASON, TRAIN_GAMES)
    validation_rows, validation_manifests, validation_games = _collect(VALIDATION_SEASON, VALIDATION_GAMES)

    candidate = fit_candidate(train_rows, training_code_sha=code_sha)

    constants = candidate["artifact_payload"]
    probability_by_line = {}
    for line in VALIDATION_LINES:
        # Seed each line deterministically so the packet is reproducible.
        random.seed(f"{candidate['artifact_checksum']}:{line}:{SIM_TRIALS}")
        scored = simulate_1ip_event_tree(
            bf_distribution=constants["bf_distribution"],
            pitches_per_batter_dist=constants["pitches_per_batter"],
            line_value=line,
            side="MORE",
            n_trials=SIM_TRIALS,
        )
        probability_by_line[str(line)] = scored["P_MORE"]

    actual = []
    predicted = []
    validation_assignments = []
    for idx, row in enumerate(validation_rows):
        line = VALIDATION_LINES[idx % len(VALIDATION_LINES)]
        p = float(probability_by_line[str(line)])
        # validate_candidate intentionally rejects exact 0/1 inputs. The
        # simulator should not normally produce them on this bounded line grid;
        # fail instead of silently clipping if it ever does.
        if not 0.0 < p < 1.0:
            raise RuntimeError(f"MLB_1IP_SHADOW_PROBABILITY_EXTREME line={line} p={p}")
        y = 1 if row.pitches > line else 0
        actual.append(y)
        predicted.append(p)
        validation_assignments.append({"bf": row.bf, "pitches": row.pitches, "line": line, "actual_more": y, "p_more": p})

    train_manifest_hash = _sha(train_manifests)
    validation_manifest_hash = _sha(validation_manifests)
    split_material = {
        "train_season": TRAIN_SEASON,
        "validation_season": VALIDATION_SEASON,
        "train_games": train_games,
        "validation_games": validation_games,
        "validation_lines": VALIDATION_LINES,
    }
    split_hash = _sha(split_material)

    validated = validate_candidate(
        candidate,
        actual,
        predicted,
        scoring_code_sha=code_sha,
        split_hash=split_hash,
        source_snapshot_hashes=[train_manifest_hash, validation_manifest_hash],
    )

    packet = {
        "purpose": "RESEARCH_ONLY_TEMPORAL_SHADOW",
        "training": {
            "season": TRAIN_SEASON,
            "games_sampled": len(train_games),
            "rows": len(train_rows),
            "manifest_hash": train_manifest_hash,
        },
        "validation": {
            "season": VALIDATION_SEASON,
            "games_sampled": len(validation_games),
            "rows": len(validation_rows),
            "manifest_hash": validation_manifest_hash,
            "line_grid": VALIDATION_LINES,
            "probability_by_line": probability_by_line,
        },
        "split_hash": split_hash,
        "candidate": candidate,
        "validated_candidate": validated,
        "certification_ready": False,
        "certification_blockers": [
            "INDEPENDENT_PR_REVIEW_REQUIRED",
            "LINE_SUPPORT_CERTIFICATION_REVIEW_REQUIRED",
            "PROMOTION_REQUIRES_DISTINCT_REVIEWER_CONTEXT",
        ],
        "probability_publishable": False,
        "can_execute": False,
    }

    (out_dir / "shadow_packet.json").write_text(json.dumps(packet, indent=2, sort_keys=True))
    (out_dir / "train_manifests.json").write_text(json.dumps(train_manifests, indent=2, sort_keys=True))
    (out_dir / "validation_manifests.json").write_text(json.dumps(validation_manifests, indent=2, sort_keys=True))
    (out_dir / "validation_assignments.json").write_text(json.dumps(validation_assignments, indent=2, sort_keys=True))
    print(json.dumps({
        "training_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "metrics": validated["validation_metrics"],
        "lifecycle_state": validated["lifecycle_state"],
        "probability_publishable": False,
        "can_execute": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
