"""Research-only MLB 1IP temporal model comparison.

No database writes, artifact promotion, runtime activation, or publication
authority are permitted here. The workflow compares:
1. the current Gaussian per-batter event tree;
2. an aggregate empirical total-pitch PMF baseline; and
3. a simple pitcher-specific empirical shrinkage challenger.

The shrinkage hyperparameter is selected on a later-2024 tuning window after
fitting the league prior on earlier 2024 rows. The untouched 2025 sample is the
prospective-style validation set. This is deliberately simpler than layering a
post-hoc calibrator over a misspecified tail model.
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
VALIDATION_GAMES = 700
VALIDATION_LINES = (11.5, 13.5, 15.5, 17.5, 19.5, 21.5)
SIM_TRIALS = 100_000
MAX_BRIER = 0.25
MAX_ECE = 0.06
ALPHA_CANDIDATES = (4.0, 8.0, 12.0, 20.0, 30.0)
RECENT_START_LIMIT = 10


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
    identity_rows = []
    for i, pk in enumerate(selected, 1):
        game_rows, manifest = game_training_rows(pk)
        rows.extend(game_rows)
        manifests.append(manifest)
        for detail in manifest.get("rows_detail") or []:
            identity_rows.append({"game_pk": pk, **detail})
        if i % 50 == 0:
            print(f"season={season} games={i}/{len(selected)} rows={len(rows)}", flush=True)
    return rows, manifests, selected, identity_rows


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
    return {"validation_rows": n, "brier": brier, "ece": ece, "gates_passed": brier <= MAX_BRIER and ece <= MAX_ECE}


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
    return p


def _league_probability(rows: list[dict], line: float) -> float:
    return sum(1 for r in rows if int(r["pitches"]) > line) / len(rows)


def _shrunk_probability(*, league_p: float, recent_pitches: list[int], line: float, alpha: float) -> float:
    hits = sum(1 for pitches in recent_pitches if pitches > line)
    return (alpha * league_p + hits) / (alpha + len(recent_pitches))


def _rolling_shrinkage_predictions(*, base_rows: list[dict], evaluation_rows: list[dict], alpha: float) -> tuple[list[int], list[float], list[dict]]:
    league_by_line = {line: _league_probability(base_rows, line) for line in VALIDATION_LINES}
    history: dict[int, list[int]] = {}
    actual, predicted, details = [], [], []
    for idx, row in enumerate(evaluation_rows):
        line = VALIDATION_LINES[idx % len(VALIDATION_LINES)]
        pitcher_id = int(row["pitcher_id"])
        recent = list(history.get(pitcher_id, []))[-RECENT_START_LIMIT:]
        p = _shrunk_probability(league_p=league_by_line[line], recent_pitches=recent, line=line, alpha=alpha)
        y = 1 if int(row["pitches"]) > line else 0
        actual.append(y)
        predicted.append(p)
        details.append({**row, "line": line, "actual_more": y, "p_more": p, "prior_start_n": len(recent)})
        history.setdefault(pitcher_id, []).append(int(row["pitches"]))
    return actual, predicted, details


def _select_alpha(train_identity_rows: list[dict]) -> dict:
    split = max(1, int(len(train_identity_rows) * 0.70))
    fit_rows = train_identity_rows[:split]
    tune_rows = train_identity_rows[split:]
    trials = []
    for alpha in ALPHA_CANDIDATES:
        y, p, _ = _rolling_shrinkage_predictions(base_rows=fit_rows, evaluation_rows=tune_rows, alpha=alpha)
        metrics = _metrics(y, p)
        trials.append({"alpha": alpha, **metrics})
    # Hyperparameter choice is entirely within 2024. Prefer lowest Brier, then ECE.
    selected = min(trials, key=lambda x: (x["brier"], x["ece"]))
    return {"fit_rows": len(fit_rows), "tune_rows": len(tune_rows), "trials": trials, "selected_alpha": selected["alpha"]}


def main() -> None:
    out_dir = Path(os.environ.get("MLB_1IP_RESEARCH_OUT", "research-output/mlb-1ip"))
    out_dir.mkdir(parents=True, exist_ok=True)
    code_sha = os.environ.get("GITHUB_SHA") or ("0" * 40)

    train_rows, train_manifests, train_games, train_identity = _collect(TRAIN_SEASON, TRAIN_GAMES)
    validation_rows, validation_manifests, validation_games, validation_identity = _collect(VALIDATION_SEASON, VALIDATION_GAMES)

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

    aggregate = _conditional_pmf_model(train_rows)
    aggregate_probability_by_line = {str(line): _pmf_probability_more(aggregate, line) for line in VALIDATION_LINES}

    actual, current_predicted, aggregate_predicted = [], [], []
    for idx, row in enumerate(validation_rows):
        line = VALIDATION_LINES[idx % len(VALIDATION_LINES)]
        y = 1 if row.pitches > line else 0
        actual.append(y)
        current_predicted.append(float(current_probability_by_line[str(line)]))
        aggregate_predicted.append(float(aggregate_probability_by_line[str(line)]))

    alpha_selection = _select_alpha(train_identity)
    selected_alpha = float(alpha_selection["selected_alpha"])
    shrink_actual, shrink_predicted, shrink_assignments = _rolling_shrinkage_predictions(
        base_rows=train_identity,
        evaluation_rows=validation_identity,
        alpha=selected_alpha,
    )

    train_manifest_hash = _sha(train_manifests)
    validation_manifest_hash = _sha(validation_manifests)
    split_material = {
        "train_season": TRAIN_SEASON,
        "validation_season": VALIDATION_SEASON,
        "train_games": train_games,
        "validation_games": validation_games,
        "validation_lines": VALIDATION_LINES,
        "alpha_selection": alpha_selection,
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
    aggregate_metrics = _metrics(actual, aggregate_predicted)
    shrink_metrics = _metrics(shrink_actual, shrink_predicted)

    shrink_artifact = {
        "model_family": "MLB_1IP_PITCHER_SHRUNK_EMPIRICAL_PMF_V1",
        "league_total_pitches": [int(r["pitches"]) for r in train_identity],
        "recent_start_limit": RECENT_START_LIMIT,
        "shrinkage_alpha": selected_alpha,
        "alpha_selection": alpha_selection,
        "training_rows": len(train_identity),
        "artifact_checksum": _sha({"league_total_pitches": [int(r["pitches"]) for r in train_identity], "recent_start_limit": RECENT_START_LIMIT, "shrinkage_alpha": selected_alpha}),
        "probability_publishable": False,
        "can_execute": False,
    }

    comparison = {
        "current": current_validated["validation_metrics"],
        "aggregate_empirical": aggregate_metrics,
        "pitcher_shrunk_empirical": shrink_metrics,
        "alpha_selection": alpha_selection,
        "preferred_research_model": (
            shrink_artifact["model_family"]
            if shrink_metrics["gates_passed"] and shrink_metrics["brier"] <= aggregate_metrics["brier"]
            else aggregate["model_family"] if aggregate_metrics["gates_passed"] else "NO_CERTIFIABLE_RESEARCH_MODEL"
        ),
    }

    packet = {
        "purpose": "RESEARCH_ONLY_TEMPORAL_SHADOW_MODEL_COMPARISON",
        "training": {"season": TRAIN_SEASON, "games_sampled": len(train_games), "rows": len(train_rows), "identity_rows": len(train_identity), "manifest_hash": train_manifest_hash},
        "validation": {"season": VALIDATION_SEASON, "games_sampled": len(validation_games), "rows": len(validation_rows), "identity_rows": len(validation_identity), "manifest_hash": validation_manifest_hash, "line_grid": VALIDATION_LINES},
        "split_hash": split_hash,
        "current_candidate": current_candidate,
        "current_validated_candidate": current_validated,
        "aggregate_research_artifact": aggregate,
        "pitcher_shrunk_research_artifact": shrink_artifact,
        "comparison": comparison,
        "certification_ready": False,
        "certification_blockers": [
            "INDEPENDENT_PR_REVIEW_REQUIRED",
            "PREFERRED_CHALLENGER_REQUIRES_FORMAL_ARTIFACT_AND_RUNTIME_CONTRACT",
            "LINE_SUPPORT_CERTIFICATION_REVIEW_REQUIRED",
            "PROMOTION_REQUIRES_DISTINCT_REVIEWER_CONTEXT",
        ],
        "probability_publishable": False,
        "can_execute": False,
    }

    (out_dir / "shadow_packet.json").write_text(json.dumps(packet, indent=2, sort_keys=True))
    (out_dir / "train_manifests.json").write_text(json.dumps(train_manifests, indent=2, sort_keys=True))
    (out_dir / "validation_manifests.json").write_text(json.dumps(validation_manifests, indent=2, sort_keys=True))
    (out_dir / "pitcher_shrunk_validation_assignments.json").write_text(json.dumps(shrink_assignments, indent=2, sort_keys=True))
    print(json.dumps({"training_rows": len(train_rows), "validation_rows": len(validation_rows), "comparison": comparison, "probability_publishable": False, "can_execute": False}, sort_keys=True))


if __name__ == "__main__":
    main()
