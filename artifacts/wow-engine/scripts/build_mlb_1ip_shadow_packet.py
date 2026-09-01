"""Build a research-only MLB 1IP temporal shadow validation packet.

No database writes, artifact promotion, runtime activation, or publication
authority are permitted here. The workflow compares the current Gaussian
per-batter event tree against a simpler empirical conditional-total-pitches
PMF challenger using a clean 2024 -> 2025 temporal holdout.
"""
from __future__ import annotations

import hashlib
import json
import math
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
MAX_BRIER = 0.25
MAX_ECE = 0.06


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _even_sample(values: list[int], n: int) -> list[int]:
    if n >= len(values):
        return list(values)
    if n <= 1:
        return [values[0]]
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


def _metrics(actual: list[int], predicted: list[float]) -> dict:
    n = len(actual)
    brier = sum((y - p) ** 2 for y, p in zip(actual, predicted)) / n
    ece = 0.0
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        idx = [j for j, p in enumerate(predicted) if (lo <= p < hi) or (i == 9 and p == 1.0)]
        if not idx:
            continue
        conf = sum(predicted[j] for j in idx) / len(idx)
        acc = sum(actual[j] for j in idx) / len(idx)
        ece += len(idx) / n * abs(conf - acc)
    return {
        "validation_rows": n,
        "brier": brier,
        "ece": ece,
        "gates_passed": brier <= MAX_BRIER and ece <= MAX_ECE,
    }


def _bf_bucket(bf: int) -> str:
    return "3" if bf == 3 else "4" if bf == 4 else "5_PLUS"


def _conditional_pmf_model(train_rows) -> dict:
    groups = {"3": [], "4": [], "5_PLUS": []}
    for row in train_rows:
        groups[_bf_bucket(row.bf)].append(int(row.pitches))
    total = sum(len(v) for v in groups.values())
    return {
        "model_family": "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1",
        "bf_weights": {k: len(v) / total for k, v in groups.items()},
        "conditional_total_pitches": groups,
        "training_rows": total,
        "artifact_checksum": _sha({"groups": groups}),
        "probability_publishable": False,
        "can_execute": False,
    }


def _pmf_probability_more(model: dict, line: float) -> float:
    p = 0.0
    for group, values in model["conditional_total_pitches"].items():
        if not values:
            continue
        conditional = sum(1 for x in values if x > line) / len(values)
        p += model["bf_weights"][group] * conditional
    # Half-point lines avoid pushes. Exact 0/1 would indicate unsupported tail.
    return p


def main() -> None:
    out_dir = Path(os.environ.get("MLB_1IP_RESEARCH_OUT", "research-output/mlb-1ip"))
    out_dir.mkdir(parents=True, exist_ok=True)
    code_sha = os.environ.get("GITHUB_SHA") or ("0" * 40)

    train_rows, train_manifests, train_games = _collect(TRAIN_SEASON, TRAIN_GAMES)
    validation_rows, validation_manifests, validation_games = _collect(VALIDATION_SEASON, VALIDATION_GAMES)

    current_candidate = fit_candidate(train_rows, training_code_sha=code_sha)
    constants = current_candidate["artifact_payload"]

    current_probability_by_line = {}
    for line in VALIDATION_LINES:
        random.seed(f"{current_candidate['artifact_checksum']}:{line}:{SIM_TRIALS}")
        scored = simulate_1ip_event_tree(
            bf_distribution=constants["bf_distribution"],
            pitches_per_batter_dist=constants["pitches_per_batter"],
            line_value=line,
            side="MORE",
            n_trials=SIM_TRIALS,
        )
        current_probability_by_line[str(line)] = scored["P_MORE"]

    challenger = _conditional_pmf_model(train_rows)
    challenger_probability_by_line = {
        str(line): _pmf_probability_more(challenger, line) for line in VALIDATION_LINES
    }

    actual = []
    current_predicted = []
    challenger_predicted = []
    validation_assignments = []
    for idx, row in enumerate(validation_rows):
        line = VALIDATION_LINES[idx % len(VALIDATION_LINES)]
        current_p = float(current_probability_by_line[str(line)])
        challenger_p = float(challenger_probability_by_line[str(line)])
        if not 0.0 < current_p < 1.0:
            raise RuntimeError(f"MLB_1IP_CURRENT_PROBABILITY_EXTREME line={line} p={current_p}")
        if not 0.0 < challenger_p < 1.0:
            raise RuntimeError(f"MLB_1IP_CHALLENGER_PROBABILITY_EXTREME line={line} p={challenger_p}")
        y = 1 if row.pitches > line else 0
        actual.append(y)
        current_predicted.append(current_p)
        challenger_predicted.append(challenger_p)
        validation_assignments.append({
            "bf": row.bf,
            "pitches": row.pitches,
            "line": line,
            "actual_more": y,
            "current_p_more": current_p,
            "challenger_p_more": challenger_p,
        })

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

    current_validated = validate_candidate(
        current_candidate,
        actual,
        current_predicted,
        scoring_code_sha=code_sha,
        split_hash=split_hash,
        source_snapshot_hashes=[train_manifest_hash, validation_manifest_hash],
    )
    challenger_metrics = _metrics(actual, challenger_predicted)

    comparison = {
        "current": current_validated["validation_metrics"],
        "challenger": challenger_metrics,
        "challenger_brier_delta": challenger_metrics["brier"] - current_validated["validation_metrics"]["brier"],
        "challenger_ece_delta": challenger_metrics["ece"] - current_validated["validation_metrics"]["ece"],
        "preferred_research_model": (
            challenger["model_family"]
            if challenger_metrics["brier"] < current_validated["validation_metrics"]["brier"]
            and challenger_metrics["ece"] < current_validated["validation_metrics"]["ece"]
            else "NO_AUTOMATIC_REPLACEMENT"
        ),
    }

    packet = {
        "purpose": "RESEARCH_ONLY_TEMPORAL_SHADOW_MODEL_COMPARISON",
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
            "current_probability_by_line": current_probability_by_line,
            "challenger_probability_by_line": challenger_probability_by_line,
        },
        "split_hash": split_hash,
        "current_candidate": current_candidate,
        "current_validated_candidate": current_validated,
        "challenger_research_artifact": challenger,
        "comparison": comparison,
        "certification_ready": False,
        "certification_blockers": [
            "INDEPENDENT_PR_REVIEW_REQUIRED",
            "CHALLENGER_REQUIRES_FORMAL_ARTIFACT_CONTRACT_IF_SELECTED",
            "LINE_SUPPORT_CERTIFICATION_REVIEW_REQUIRED",
            "PROMOTION_REQUIRES_DISTINCT_REVIEWER_CONTEXT",
        ],
        "probability_publishable": False,
        "can_execute": False,
    }

    (out_dir / "shadow_packet.json").write_text(json.dumps(packet, indent=2, sort_keys=True))
    (out_dir / "train_rows.json").write_text(json.dumps([{"bf": r.bf, "pitches": r.pitches} for r in train_rows], indent=2))
    (out_dir / "train_manifests.json").write_text(json.dumps(train_manifests, indent=2, sort_keys=True))
    (out_dir / "validation_manifests.json").write_text(json.dumps(validation_manifests, indent=2, sort_keys=True))
    (out_dir / "validation_assignments.json").write_text(json.dumps(validation_assignments, indent=2, sort_keys=True))
    print(json.dumps({
        "training_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "comparison": comparison,
        "probability_publishable": False,
        "can_execute": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
