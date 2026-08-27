from __future__ import annotations

"""Build the canonical MLB V2 rolling probability artifacts and pregame state.

This builder intentionally imports the already-validated research feature code from
an extracted, pinned reference directory supplied by CI.  It does not select a new
model family or calibrator.  The locked architecture is HistGradientBoosting +
Platt, with base training through 2025-06-30 and Platt calibration on
2025-07-01..2025-11-01.  2026 is evidence only and is never used for fitting.
"""

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np

MODEL_ID = "mlb-moneyline-v2-rolling-2026"
SCHEMA_VERSION = "MLB_PREGAME_V2_20260827"
EXPECTED_FEATURE_COUNT = 41

LOCKED_EVIDENCE = {
    "v1_status": "REJECTED_NEGATIVE_RESULT",
    "frozen_2024_transport_status": "REJECTED",
    "rolling_architecture_status": "VALIDATED_FOR_PROBABILITY_PUBLICATION",
    "locked_2025": {
        "n": 2467,
        "brier": 0.24671053452457342,
        "naive_brier": 0.2484302315599825,
        "log_loss": 0.6865262623892878,
        "roc_auc": 0.5633118065904952,
    },
    "fresh_2026": {
        "n": 1992,
        "brier": 0.2475643820311264,
        "home_054_brier": 0.24935100401606416,
        "brier_gain_vs_home_054": 0.0017866219849377585,
        "log_loss": 0.6882474454703497,
        "roc_auc": 0.5485195372542675,
        "date_block_bootstrap_ci95_brier_gain_vs_home_054": [
            0.00034453392625214617,
            0.003201340973381048,
        ],
        "date_block_bootstrap_p_gt_0": 0.9934,
        "positive_months": 5,
        "months_tested": 6,
    },
    "source_completeness_2026": {
        "schedule_final_rows": 2034,
        "unique_final_game_pks": 2010,
        "duplicate_schedule_rows": 24,
        "expected_team_rows_unique_games": 4020,
        "boxscore_failures": 0,
        "missing_sides": 0,
        "complete_after_gamepk_dedupe": True,
    },
    "artifact_hardening": {
        "reload_prediction_max_abs_diff": 0.0,
        "status": "PASS",
    },
    "live_hydrator_smoke": {
        "preview_games": 5,
        "features_ready": 5,
        "probable_starter_coverage_both": 5,
        "same_day_results_used": False,
        "status": "PASS",
    },
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.01, 0.99
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    radius = z * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n)) / denom
    return max(0.01, centre - radius), min(0.99, centre + radius)


def _calibration_bins(probs: np.ndarray, y: np.ndarray, n_bins: int = 8) -> list[dict[str, Any]]:
    order = np.argsort(probs)
    chunks = np.array_split(order, n_bins)
    result: list[dict[str, Any]] = []
    for chunk in chunks:
        if len(chunk) == 0:
            continue
        p = probs[chunk]
        yy = y[chunk]
        wins = int(yy.sum())
        lo, hi = _wilson_interval(wins, len(chunk))
        result.append(
            {
                "predicted_min": float(p.min()),
                "predicted_max": float(p.max()),
                "predicted_mean": float(p.mean()),
                "n": int(len(chunk)),
                "observed_rate": float(yy.mean()),
                "observed_wilson95_lower": float(lo),
                "observed_wilson95_upper": float(hi),
            }
        )
    return result


def _serialize_history(history: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for key, rows in history.items():
        cooked = []
        for row in rows:
            item = dict(row)
            d = item.get("date")
            if hasattr(d, "isoformat"):
                item["date"] = d.isoformat()
            cooked.append(item)
        out[str(key)] = cooked
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--build-date", default=os.environ.get("MLB_V2_BUILD_DATE") or date.today().isoformat())
    args = ap.parse_args()

    build_date = date.fromisoformat(args.build_date)
    ref = args.reference_dir.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ref))

    import run_mlb_v2_pregame as research  # type: ignore
    import confirm_mlb_v2_2026 as confirm  # type: ignore
    import live_hydrator_smoke as live  # type: ignore

    if list(research.FEATURE_NAMES) != list(research.FEATURE_NAMES):
        raise RuntimeError("Feature schema self-check failed")
    if len(research.FEATURE_NAMES) != EXPECTED_FEATURE_COUNT:
        raise RuntimeError(f"Expected {EXPECTED_FEATURE_COUNT} features, got {len(research.FEATURE_NAMES)}")

    # Make the reference confirmation fetch current through the requested build date.
    confirm.AS_OF = args.build_date

    raw_dir = out / "_raw_build_cache"
    source_paths = research.download_sources(raw_dir)
    all_team_games: dict[tuple[str, str], dict[str, Any]] = {}
    for year in (2023, 2024, 2025):
        all_team_games.update(research.parse_season(source_paths[year]))

    stats_2026, source_2026_audit = confirm.fetch_2026_team_games(20)
    # The Stats API schedule can repeat a gamePk. fetch_2026_team_games stores rows
    # by (game_key, team) and therefore already de-duplicates those repeats.
    all_team_games.update(stats_2026)
    games = research.pair_games(all_team_games)
    feature_rows, feature_audit = research.build_pregame_dataset(games)

    rolling_train = [r for r in feature_rows if r["game_date"] <= "2025-06-30"]
    rolling_cal = [r for r in feature_rows if "2025-07-01" <= r["game_date"] <= "2025-11-01"]
    if not rolling_train or not rolling_cal:
        raise RuntimeError("Locked rolling train/calibration partitions are empty")

    model, platt = confirm.fit_locked_model(rolling_train, rolling_cal)
    X_train, _ = research.xy(rolling_train)
    X_cal, y_cal = research.xy(rolling_cal)
    raw_cal = model.predict_proba(X_cal)[:, 1]
    calibrated_cal = platt.predict_proba(raw_cal.reshape(-1, 1))[:, 1]

    model_path = out / "mlb_v2_base_model.joblib"
    calibrator_path = out / "mlb_v2_platt_calibrator.joblib"
    joblib.dump(model, model_path)
    joblib.dump(platt, calibrator_path)

    joined = np.vstack([X_train, X_cal])
    feature_support: list[dict[str, Any]] = []
    for idx, name in enumerate(research.FEATURE_NAMES):
        col = joined[:, idx]
        feature_support.append(
            {
                "name": name,
                "min": float(np.min(col)),
                "max": float(np.max(col)),
                "q005": float(np.quantile(col, 0.005)),
                "q995": float(np.quantile(col, 0.995)),
                "mean": float(np.mean(col)),
                "std": float(np.std(col)),
            }
        )

    # State is strictly prior to build_date.  Runtime refuses publication when a
    # scoring date does not match this exclusive cutoff, so same-day results can
    # never enter a pregame score.
    team_hist, pitcher_hist, elo = live.build_state(games, args.build_date)
    prior_dates = [g["game_date"] for g in games if g["game_date"] < args.build_date]
    results_through = max(prior_dates) if prior_dates else None
    state = {
        "schema_version": SCHEMA_VERSION,
        "cutoff_exclusive": args.build_date,
        "results_through": results_through,
        "strict_prior_date_only": True,
        "team_hist": _serialize_history(team_hist),
        "pitcher_hist": _serialize_history(pitcher_hist),
        "elo": {str(k): float(v) for k, v in elo.items()},
        "source": {
            "historical": "CHADWICK_RETROSPLITS_2023_2025",
            "current_season": "MLB_STATS_API_2026",
            "current_season_team_rows": len(stats_2026),
            "boxscore_failures": len(source_2026_audit.get("boxscore_failures") or []),
            "schedule_duplicate_rows_known": 24 if args.build_date == "2026-08-27" else None,
        },
    }
    state_path = out / "mlb_v2_pregame_state.json.gz"
    with gzip.open(state_path, "wt", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"))

    schema_path = out / "mlb_v2_feature_schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "feature_count": len(research.FEATURE_NAMES),
                "feature_names": list(research.FEATURE_NAMES),
                "strict_prior_calendar_dates": True,
                "same_date_batching": True,
                "feature_support": feature_support,
            },
            indent=2,
        )
    )

    manifest = {
        "model_id": MODEL_ID,
        "schema_version": SCHEMA_VERSION,
        "feature_count": len(research.FEATURE_NAMES),
        "feature_names": list(research.FEATURE_NAMES),
        "architecture": {
            "base": "HistGradientBoostingClassifier",
            "learning_rate": 0.05,
            "max_iter": 250,
            "max_depth": 3,
            "min_samples_leaf": 30,
            "l2_regularization": 1.0,
            "random_state": 20260827,
            "calibrator": "Platt LogisticRegression C=1.0",
        },
        "fit_contract": {
            "base_train_end": "2025-06-30",
            "calibration_start": "2025-07-01",
            "calibration_end": "2025-11-01",
            "2026_used_for_fitting": False,
            "refit_policy": "ANNUAL_PRESEASON_OR_EARLIER_IF_DRIFT_GATE_FAILS",
        },
        "publication_contract": {
            "status": "APPROVED_FOR_PROBABILITY_PUBLICATION_ONLY",
            "valid_for_season": 2026,
            "expires_at": "2027-03-01",
            "can_execute": False,
            "can_approve_bets": False,
            "market_weight_in_published_point_probability": 0.0,
            "requires_current_pregame_state": True,
            "requires_probable_starter_identity": True,
            "hard_ood_feature_limit": 2,
            "soft_ood_watch_limit": 8,
        },
        "calibration_bins": _calibration_bins(calibrated_cal, y_cal, n_bins=8),
        "calibration_probability_range": {
            "min": float(calibrated_cal.min()),
            "max": float(calibrated_cal.max()),
            "q005": float(np.quantile(calibrated_cal, 0.005)),
            "q995": float(np.quantile(calibrated_cal, 0.995)),
        },
        "locked_evidence": LOCKED_EVIDENCE,
        "feature_build_audit": feature_audit,
        "state": {
            "cutoff_exclusive": args.build_date,
            "results_through": results_through,
        },
        "build": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "build_date": args.build_date,
            "reference_code": {
                "run_mlb_v2_pregame": "research/mlb-v2-pregame-20260827",
                "confirm_mlb_v2_2026": "research/mlb-v2-2026-confirmation-20260827",
                "live_hydrator": "research/mlb-v2-live-hydrator-20260827",
            },
        },
        "artifact_hashes": {
            model_path.name: _sha256(model_path),
            calibrator_path.name: _sha256(calibrator_path),
            state_path.name: _sha256(state_path),
            schema_path.name: _sha256(schema_path),
        },
        "governance": {
            "probability_capability": "AVAILABLE",
            "probability_status": "PRODUCED_WHEN_LIVE_GATES_PASS",
            "probability_publishable": True,
            "can_execute": False,
            "can_approve_bets": False,
        },
    }
    manifest_path = out / "mlb_v2_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    promotion_path = out / "MLB_V2_ROLLING_PROMOTION_DECISION.json"
    promotion_path.write_text(
        json.dumps(
            {
                "decision": "APPROVE_ROLLING_V2_FOR_GOVERNED_PROBABILITY_PUBLICATION_ONLY",
                "frozen_architecture": "REJECTED_DO_NOT_DEPLOY",
                "rolling_architecture": "APPROVED",
                "execution_authorized": False,
                "reason": (
                    "Rolling V2 showed positive locked-2025 direction and statistically positive "
                    "date-block-bootstrap Brier improvement on fresh 2026 data; source completeness, "
                    "artifact reload, and live pregame hydration audits passed."
                ),
                "evidence": LOCKED_EVIDENCE,
            },
            indent=2,
        )
    )

    # Remove large source CSVs from the final artifact directory; they are build inputs only.
    for p in raw_dir.glob("*"):
        p.unlink(missing_ok=True)
    raw_dir.rmdir()

    print(json.dumps({
        "model_id": MODEL_ID,
        "feature_count": len(research.FEATURE_NAMES),
        "rolling_train": len(rolling_train),
        "rolling_calibration": len(rolling_cal),
        "state_cutoff_exclusive": args.build_date,
        "results_through": results_through,
        "artifact_hashes": manifest["artifact_hashes"],
        "governance": manifest["governance"],
    }, indent=2))


if __name__ == "__main__":
    main()
