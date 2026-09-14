"""Build a leakage-safe historical shadow packet for MLB 1IP batters faced.

2024 is development data: early 2024 fits the league prior/history and later
2024 chooses the Dirichlet shrinkage alpha. After alpha selection, the model is
refit on all 2024 rows and evaluated exactly once on untouched chronological
2025 rows. Each 2025 prediction is made before that row is admitted into the
rolling pitcher history.

Research only: no publication, promotion, database writes, or execution.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from mlb_1ip_bf_model import (
    BFObservation,
    MODEL_FAMILY,
    bf_bucket,
    binary_metrics,
    fit_league_prior,
    history_counts,
    multiclass_brier,
    multiclass_log_loss,
    score_pitcher,
    update_history,
)
from mlb_1ip_training_dataset import game_training_rows
from prop_auto_hydration import MLB_STATS_API_BASE, _int, _request_json

TRAIN_SEASON = 2024
VALIDATION_SEASON = 2025
TRAIN_GAMES = 700
VALIDATION_GAMES = 700
DEV_SPLIT = 0.70
ALPHA_CANDIDATES = (2.0, 4.0, 8.0, 12.0, 20.0, 30.0, 50.0)
MAX_ECE = 0.06
MAX_CALIBRATION_BIAS = 0.04
CAN_EXECUTE = False


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _season_games(season: int) -> list[tuple[str, int]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={"sportId": "1", "season": str(season), "gameType": "R"},
        http_get=httpx.get,
    )
    games: list[tuple[str, int]] = []
    for block in payload.get("dates") or []:
        block_date = str(block.get("date") or "")
        for game in (block or {}).get("games") or []:
            pk = _int(game.get("gamePk"))
            status = str(((game.get("status") or {}).get("abstractGameState") or "")).upper()
            event_time = str(game.get("gameDate") or (block_date + "T00:00:00Z"))
            if pk > 0 and status == "FINAL":
                games.append((event_time, pk))
    games.sort(key=lambda x: (x[0], x[1]))
    return games


def _even_sample(values: list[tuple[str, int]], n: int) -> list[tuple[str, int]]:
    if n >= len(values):
        return list(values)
    if n <= 1:
        return [values[0]]
    idx = [round(i * (len(values) - 1) / (n - 1)) for i in range(n)]
    return [values[i] for i in idx]


def _collect(season: int, n_games: int) -> tuple[list[BFObservation], list[dict[str, Any]]]:
    selected = _even_sample(_season_games(season), n_games)
    rows: list[BFObservation] = []
    manifests: list[dict[str, Any]] = []
    for i, (event_time, game_pk) in enumerate(selected, 1):
        _, manifest = game_training_rows(game_pk)
        manifests.append({
            "event_time": event_time,
            "game_pk": game_pk,
            "source_sha256": manifest.get("source_sha256"),
            "rows_detail": manifest.get("rows_detail") or [],
            "selection_rule": manifest.get("selection_rule"),
        })
        for detail in manifest.get("rows_detail") or []:
            pitcher_id = int(detail.get("pitcher_id") or 0)
            bf = int(detail.get("bf") or 0)
            if pitcher_id > 0 and bf >= 3:
                rows.append(BFObservation(
                    pitcher_id=pitcher_id,
                    bf=bf,
                    event_time=event_time,
                    game_pk=game_pk,
                ))
        if i % 50 == 0:
            print(f"season={season} games={i}/{len(selected)} rows={len(rows)}", flush=True)
    rows.sort(key=lambda r: (r.event_time, r.game_pk, r.pitcher_id))
    return rows, manifests


def _rolling_predictions(
    *,
    evaluation_rows: list[BFObservation],
    league_prior: dict[str, float],
    initial_history: dict[int, dict[str, int]],
    alpha: float,
) -> dict[str, Any]:
    history = {pid: dict(counts) for pid, counts in initial_history.items()}
    actual_bucket: list[str] = []
    predicted_bucket: list[dict[str, float]] = []
    actual_35: list[int] = []
    predicted_35: list[float] = []
    actual_45: list[int] = []
    predicted_45: list[float] = []
    assignments: list[dict[str, Any]] = []

    for row in evaluation_rows:
        scored = score_pitcher(
            pitcher_id=row.pitcher_id,
            league_prior=league_prior,
            pitcher_counts=history,
            alpha=alpha,
        )
        y_bucket = bf_bucket(row.bf)
        probs = {
            "3": scored["P_BF_3"],
            "4": scored["P_BF_4"],
            "5_PLUS": scored["P_BF_GE_5"],
        }
        y35 = 1 if row.bf >= 4 else 0
        y45 = 1 if row.bf >= 5 else 0

        actual_bucket.append(y_bucket)
        predicted_bucket.append(probs)
        actual_35.append(y35)
        predicted_35.append(scored["P_MORE_3_5"])
        actual_45.append(y45)
        predicted_45.append(scored["P_MORE_4_5"])
        assignments.append({
            "event_time": row.event_time,
            "game_pk": row.game_pk,
            "pitcher_id": row.pitcher_id,
            "actual_bf": row.bf,
            "actual_bucket": y_bucket,
            "pitcher_history_n": scored["pitcher_history_n"],
            "P_BF_3": scored["P_BF_3"],
            "P_BF_4": scored["P_BF_4"],
            "P_BF_GE_5": scored["P_BF_GE_5"],
            "P_MORE_3_5": scored["P_MORE_3_5"],
            "P_MORE_4_5": scored["P_MORE_4_5"],
        })
        update_history(history, row)

    return {
        "multiclass_brier": multiclass_brier(actual_bucket, predicted_bucket),
        "multiclass_log_loss": multiclass_log_loss(actual_bucket, predicted_bucket),
        "line_3_5_more": binary_metrics(actual_35, predicted_35),
        "line_4_5_more": binary_metrics(actual_45, predicted_45),
        "assignments": assignments,
    }


def _constant_baseline(rows: list[BFObservation], prior: dict[str, float]) -> dict[str, Any]:
    actual = [bf_bucket(r.bf) for r in rows]
    predicted = [dict(prior) for _ in rows]
    actual35 = [1 if r.bf >= 4 else 0 for r in rows]
    p35 = [prior["4"] + prior["5_PLUS"] for _ in rows]
    actual45 = [1 if r.bf >= 5 else 0 for r in rows]
    p45 = [prior["5_PLUS"] for _ in rows]
    return {
        "multiclass_brier": multiclass_brier(actual, predicted),
        "multiclass_log_loss": multiclass_log_loss(actual, predicted),
        "line_3_5_more": binary_metrics(actual35, p35),
        "line_4_5_more": binary_metrics(actual45, p45),
    }


def _select_alpha(fit_rows: list[BFObservation], tune_rows: list[BFObservation]) -> dict[str, Any]:
    prior = fit_league_prior(fit_rows)
    history = history_counts(fit_rows)
    trials = []
    for alpha in ALPHA_CANDIDATES:
        result = _rolling_predictions(
            evaluation_rows=tune_rows,
            league_prior=prior,
            initial_history=history,
            alpha=alpha,
        )
        trials.append({
            "alpha": alpha,
            "multiclass_brier": result["multiclass_brier"],
            "multiclass_log_loss": result["multiclass_log_loss"],
            "line_3_5_brier": result["line_3_5_more"]["brier"],
            "line_4_5_brier": result["line_4_5_more"]["brier"],
        })
    selected = min(
        trials,
        key=lambda x: (
            x["multiclass_brier"],
            x["multiclass_log_loss"],
            x["line_3_5_brier"] + x["line_4_5_brier"],
        ),
    )
    return {
        "fit_rows": len(fit_rows),
        "tune_rows": len(tune_rows),
        "trials": trials,
        "selected_alpha": selected["alpha"],
    }


def _gate(candidate: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if candidate["multiclass_brier"] > baseline["multiclass_brier"]:
        reasons.append("MULTICLASS_BRIER_NOT_BETTER_THAN_LEAGUE_BASELINE")
    if candidate["multiclass_log_loss"] > baseline["multiclass_log_loss"]:
        reasons.append("MULTICLASS_LOG_LOSS_NOT_BETTER_THAN_LEAGUE_BASELINE")
    for key in ("line_3_5_more", "line_4_5_more"):
        if candidate[key]["brier"] > baseline[key]["brier"]:
            reasons.append(f"{key.upper()}_BRIER_NOT_BETTER_THAN_BASELINE")
        if candidate[key]["ece"] > MAX_ECE:
            reasons.append(f"{key.upper()}_ECE_ABOVE_LIMIT")
        if abs(candidate[key]["calibration_bias"]) > MAX_CALIBRATION_BIAS:
            reasons.append(f"{key.upper()}_CALIBRATION_BIAS_ABOVE_LIMIT")
    return not reasons, reasons


def main() -> None:
    out_dir = Path(os.environ.get("MLB_1IP_BF_RESEARCH_OUT", "research-output/mlb-1ip-bf"))
    out_dir.mkdir(parents=True, exist_ok=True)

    train_rows, train_manifests = _collect(TRAIN_SEASON, TRAIN_GAMES)
    validation_rows, validation_manifests = _collect(VALIDATION_SEASON, VALIDATION_GAMES)

    if len(train_rows) < 1000 or len(validation_rows) < 500:
        raise RuntimeError("MLB_1IP_BF_RESEARCH_SAMPLE_INSUFFICIENT")

    split = max(100, int(len(train_rows) * DEV_SPLIT))
    fit_rows = train_rows[:split]
    tune_rows = train_rows[split:]
    alpha_selection = _select_alpha(fit_rows, tune_rows)
    alpha = float(alpha_selection["selected_alpha"])

    final_prior = fit_league_prior(train_rows)
    initial_history = history_counts(train_rows)
    candidate = _rolling_predictions(
        evaluation_rows=validation_rows,
        league_prior=final_prior,
        initial_history=initial_history,
        alpha=alpha,
    )
    baseline = _constant_baseline(validation_rows, final_prior)
    passed, blockers = _gate(candidate, baseline)

    artifact_payload = {
        "model_family": MODEL_FAMILY,
        "alpha": alpha,
        "league_prior": final_prior,
        "training_rows": len(train_rows),
        "training_season": TRAIN_SEASON,
        "validation_season": VALIDATION_SEASON,
        "development_split": DEV_SPLIT,
        "alpha_candidates": list(ALPHA_CANDIDATES),
        "training_manifest_hash": _sha(train_manifests),
        "validation_manifest_hash": _sha(validation_manifests),
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    artifact_payload["artifact_checksum"] = _sha(artifact_payload)

    packet = {
        "purpose": "MLB_1IP_BATTERS_FACED_LEAKAGE_SAFE_HISTORICAL_SHADOW",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "temporal_contract": {
            "development": "2024 only",
            "fit_window": "early 2024",
            "tuning_window": "later 2024",
            "untouched_validation": "2025",
            "rolling_rule": "predict each row before adding that row to pitcher history",
            "test_reuse_prohibited": True,
        },
        "training_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "alpha_selection": alpha_selection,
        "artifact": artifact_payload,
        "baseline_metrics": baseline,
        "candidate_metrics": {k: v for k, v in candidate.items() if k != "assignments"},
        "historical_validation_passed": passed,
        "historical_validation_blockers": blockers,
        "forward_shadow_required": True,
        "forward_shadow_minimums": {
            "overall_settled_predictions": 100,
            "per_line_direction_cohort": 30,
        },
        "certification_ready": False,
        "promotion_status": "FORWARD_SHADOW_REQUIRED" if passed else "HISTORICAL_VALIDATION_FAILED",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }

    (out_dir / "bf_shadow_packet.json").write_text(json.dumps(packet, indent=2, sort_keys=True))
    (out_dir / "bf_artifact_candidate.json").write_text(json.dumps(artifact_payload, indent=2, sort_keys=True))
    (out_dir / "bf_validation_assignments.json").write_text(
        json.dumps(candidate["assignments"], indent=2, sort_keys=True)
    )
    (out_dir / "train_manifests.json").write_text(json.dumps(train_manifests, indent=2, sort_keys=True))
    (out_dir / "validation_manifests.json").write_text(json.dumps(validation_manifests, indent=2, sort_keys=True))

    print(json.dumps({
        "historical_validation_passed": passed,
        "blockers": blockers,
        "selected_alpha": alpha,
        "training_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "candidate_metrics": {k: v for k, v in candidate.items() if k != "assignments"},
        "probability_publishable": False,
        "can_execute": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
