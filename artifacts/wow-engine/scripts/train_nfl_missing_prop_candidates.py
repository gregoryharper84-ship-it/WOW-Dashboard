#!/usr/bin/env python3
"""Train five independent NFL count-prop *research candidates* from local nflverse CSVs.

This is an offline production-source builder, not a scorer, a certification engine,
a runtime adapter, or a promotion path. No odds or LLM estimates enter fitting.
All outputs are always CANDIDATE/NOT_PUBLISHABLE and can_execute=False.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
MODEL_FAMILY = "NFL_COUNT_PROP_FITTED_CANDIDATE_V1"
MODEL_KIND = "POISSON_COUNT_CANDIDATE_V1"
SEASONS = (2021, 2022, 2023, 2024, 2025)
MIN_PRIOR_GAMES = 10
FEATURES = (
    "l10_stat_mean", "l5_stat_mean", "last_stat",
    "l10_opportunity_mean", "l5_opportunity_mean", "last_opportunity",
)
ROUTES: dict[str, dict[str, str]] = {
    "COMPLETIONS": {"target": "completions", "opportunity": "attempts", "roles": "QB", "model_kind": MODEL_KIND},
    "PASSING_TOUCHDOWNS": {"target": "passing_tds", "opportunity": "attempts", "roles": "QB", "model_kind": MODEL_KIND},
    "PASS_ATTEMPTS": {"target": "attempts", "opportunity": "attempts", "roles": "QB", "model_kind": MODEL_KIND},
    "RECEPTIONS": {"target": "receptions", "opportunity": "targets", "roles": "WR,RB,TE", "model_kind": MODEL_KIND},
    "RUSH_ATTEMPTS": {"target": "carries", "opportunity": "carries", "roles": "QB,RB,WR,TE", "model_kind": MODEL_KIND},
}
COUNT_COLUMNS = ("attempts", "completions", "passing_tds", "receptions", "targets", "carries")
REQUIRED_COLUMNS = (
    "player_id", "player_display_name", "position", "season", "week",
    "game_id", "season_type", *COUNT_COLUMNS,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_digest(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def load_source_files(directory: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load only frozen local files, recording actual bytes; never download in a scorer."""
    directory = Path(directory)
    frames = []
    assets = {}
    for season in SEASONS:
        path = directory / f"stats_player_week_{season}.csv"
        if not path.is_file():
            raise ValueError(f"NFL_PROP_SOURCE_FILE_MISSING:{season}")
        blob = path.read_bytes()
        frame = pd.read_csv(path, low_memory=False)
        assets[str(season)] = {"file": path.name, "sha256": _sha256(blob), "rows": len(frame)}
        frames.append(frame)
    provenance = {
        "provider": "NFLVERSE_STATS_PLAYER", "license_claim": "CC-BY-4.0",
        "source_rights_independently_approved": False,
        "assets": assets, "bundle_sha256": _json_digest(assets),
        "can_execute": False,
    }
    return validate_frame(pd.concat(frames, ignore_index=True, sort=False)), provenance


def validate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Never turn a missing/nonfinite real stat into an observed zero."""
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError("NFL_PROP_SOURCE_COLUMNS_MISSING:" + ",".join(missing))
    result = frame.copy()
    result = result[result["season_type"].astype(str).str.upper().isin(("REG", "REGULAR"))].copy()
    if result.empty:
        raise ValueError("NFL_PROP_SOURCE_REGULAR_SEASON_EMPTY")
    for field in COUNT_COLUMNS:
        count = pd.to_numeric(result[field], errors="coerce")
        if (not np.isfinite(count.to_numpy(dtype=float)).all()
                or count.lt(0).any() or count.ne(np.floor(count)).any()):
            raise ValueError(f"NFL_PROP_SOURCE_COUNT_INVALID:{field}")
        result[field] = count.astype(int)
    for field in ("season", "week"):
        count = pd.to_numeric(result[field], errors="coerce")
        if not np.isfinite(count.to_numpy(dtype=float)).all() or count.ne(np.floor(count)).any():
            raise ValueError(f"NFL_PROP_SOURCE_DATE_INVALID:{field}")
        result[field] = count.astype(int)
    if not result["season"].isin(SEASONS).all() or not result["week"].between(1, 22).all():
        raise ValueError("NFL_PROP_SOURCE_DATE_OUT_OF_SCOPE")
    for field in ("player_id", "game_id", "position", "player_display_name"):
        if result[field].isna().any() or (result[field].astype(str).str.strip() == "").any():
            raise ValueError(f"NFL_PROP_SOURCE_IDENTITY_INVALID:{field}")
        result[field] = result[field].astype(str).str.strip()
    result["position"] = result["position"].str.upper()
    if result.duplicated(["player_id", "game_id"]).any():
        raise ValueError("NFL_PROP_SOURCE_DUPLICATE_PLAYER_EVENT")
    for target, opportunity in (("completions", "attempts"), ("receptions", "targets"), ("passing_tds", "attempts")):
        if result[target].gt(result[opportunity]).any():
            raise ValueError(f"NFL_PROP_SOURCE_COUNT_EXCEEDS_OPPORTUNITY:{target}")
    result["event_index"] = result["season"] * 100 + result["week"]
    return result.sort_values(["player_id", "event_index", "game_id"]).reset_index(drop=True)


def build_feature_rows(frame: pd.DataFrame, stat_type: str) -> pd.DataFrame:
    """Exact-stat/role L10 and L5 features frozen *before* current week's result."""
    cfg = ROUTES.get(stat_type)
    if cfg is None:
        raise ValueError("NFL_PROP_STAT_NOT_DECLARED")
    target, opportunity = cfg["target"], cfg["opportunity"]
    allowed_roles = set(cfg["roles"].split(","))
    rows = []
    for player_id, group in frame.loc[frame["position"].isin(allowed_roles)].groupby("player_id", sort=False):
        history: list[tuple[int, int]] = []
        group = group.sort_values(["event_index", "game_id"])
        for row in group.itertuples(index=False):
            current = int(getattr(row, target))
            op = int(getattr(row, opportunity))
            if op <= 0:
                continue
            if len(history) >= MIN_PRIOR_GAMES:
                recent10 = history[-10:]
                recent5 = recent10[-5:]
                stat10, opp10 = zip(*recent10)
                stat5, opp5 = zip(*recent5)
                rows.append({
                    "player_id": player_id, "position": row.position,
                    "season": int(row.season), "week": int(row.week),
                    "event_index": int(row.event_index), "game_id": str(row.game_id),
                    "target": current,
                    "l10_stat_mean": float(np.mean(stat10)),
                    "l5_stat_mean": float(np.mean(stat5)),
                    "last_stat": float(stat10[-1]),
                    "l10_opportunity_mean": float(np.mean(opp10)),
                    "l5_opportunity_mean": float(np.mean(opp5)),
                    "last_opportunity": float(opp10[-1]),
                })
            history.append((current, op))
    if not rows:
        raise ValueError(f"NFL_PROP_FEATURE_ROWS_EMPTY:{stat_type}")
    return pd.DataFrame(rows).sort_values(["event_index", "game_id", "player_id"]).reset_index(drop=True)


def build_candidate(
    rows: pd.DataFrame, stat_type: str, provenance: dict[str, Any],
    *, min_rows: tuple[int, int, int] = (1000, 300, 300),
) -> dict[str, Any]:
    """Fit a separate count candidate; any successful fit remains research-only."""
    if stat_type not in ROUTES:
        raise ValueError("NFL_PROP_STAT_NOT_DECLARED")
    for season, minimum, error in (
        ((2021, 2022, 2023), min_rows[0], "TRAIN"),
        ((2024,), min_rows[1], "CAL"),
        ((2025,), min_rows[2], "HOLDOUT"),
    ):
        if len(rows.loc[rows["season"].isin(season)]) < minimum:
            raise ValueError(f"NFL_PROP_{error}_ROWS_BELOW_MINIMUM")
    train = rows.loc[rows["season"].isin((2021, 2022, 2023))]
    cal = rows.loc[rows["season"] == 2024]
    holdout = rows.loc[rows["season"] == 2025]
    if train.event_index.max() >= cal.event_index.min() or cal.event_index.max() >= holdout.event_index.min():
        raise ValueError("NFL_PROP_TRAINING_CHRONOLOGY_INVALID")
    x_train, y_train = train.loc[:, FEATURES].to_numpy(float), train.target.to_numpy(float)
    x_cal, y_cal = cal.loc[:, FEATURES].to_numpy(float), cal.target.to_numpy(float)
    x_test, y_test = holdout.loc[:, FEATURES].to_numpy(float), holdout.target.to_numpy(float)
    if not all(np.isfinite(a).all() for a in (x_train, y_train, x_cal, y_cal, x_test, y_test)):
        raise ValueError("NFL_PROP_TRAINING_NONFINITE")
    scaler = StandardScaler()
    z_train = scaler.fit_transform(x_train)
    z_cal, z_test = scaler.transform(x_cal), scaler.transform(x_test)
    model = PoissonRegressor(alpha=0.5, max_iter=500)
    model.fit(z_train, y_train)
    mu_cal = model.predict(z_cal)
    factor = float(y_cal.sum() / mu_cal.sum()) if mu_cal.sum() > 0 else float("nan")
    if not math.isfinite(factor) or not 0.1 <= factor <= 10:
        raise ValueError("NFL_PROP_CALIBRATION_FACTOR_UNSUPPORTED")
    predicted = np.maximum(model.predict(z_test) * factor, 1e-9)
    baseline = np.maximum(x_test[:, 0], 1e-9)
    # Freeze evaluation lines from calibration outcomes, not from holdout outcomes.
    lines = sorted({float(math.floor(float(np.quantile(y_cal, q))) + 0.5) for q in (0.25, 0.5, 0.75)})
    brier_by_line = {}
    for line in lines:
        observed = (y_test > line).astype(float)
        fitted = poisson.sf(math.floor(line), predicted)
        naive = poisson.sf(math.floor(line), baseline)
        brier_by_line[str(line)] = {
            "candidate_brier": float(np.mean((fitted - observed) ** 2)),
            "rolling_baseline_brier": float(np.mean((naive - observed) ** 2)),
        }
    ood_rate = float(np.mean(np.max(np.abs(z_test), axis=1) > 6.0))
    payload = {
        "model_kind": MODEL_KIND, "stat_type": stat_type,
        "feature_names": list(FEATURES),
        "feature_mean": [float(v) for v in scaler.mean_],
        "feature_scale": [float(v) for v in scaler.scale_],
        "coef": [float(v) for v in model.coef_],
        "intercept": float(model.intercept_),
        "calibration_factor_candidate": factor,
        "trained_input_seasons": [2021, 2022, 2023],
        "calibration_season": 2024,
        "holdout_season": 2025,
    }
    metrics = {
        "train_rows": len(train), "calibration_rows": len(cal), "holdout_rows": len(holdout),
        "holdout_season": 2025,
        "holdout_mae": float(mean_absolute_error(y_test, predicted)),
        "rolling_baseline_mae": float(mean_absolute_error(y_test, baseline)),
        "holdout_ood_z_gt_6_rate": ood_rate,
        "brier_by_calibration_fixed_line": brier_by_line,
        "forward_graded": False,
        "independent_calibration_certified": False,
        "source_rights_approved": False,
        "validation_status": "RESEARCH_CANDIDATE_NOT_CERTIFIED",
    }
    return {
        "provider_identity": "WOW_PROP_FITTED_RESEARCH_V1",
        "sport": "NFL", "stat_type": stat_type,
        "model_family": MODEL_FAMILY, "model_kind": MODEL_KIND,
        "model_artifact_version": f"NFL_{stat_type}_COUNT_CANDIDATE_V1",
        "specialist_version": f"wow.nfl-{stat_type.lower().replace('_', '-')}-prop-research-expert@1",
        "feature_schema_version": "NFL_COUNT_PROP_L10_FEATURES_V1",
        "training_code_sha": (os.getenv("GITHUB_SHA") if len(os.getenv("GITHUB_SHA", "")) == 40
                              and all(ch in "0123456789abcdefABCDEF" for ch in os.getenv("GITHUB_SHA", "")) else None),
        "source_bundle_sha256": provenance["bundle_sha256"],
        "artifact_payload": payload, "artifact_checksum": _json_digest(payload),
        "validation_metrics": metrics,
        "lifecycle_state": "CANDIDATE", "active": False,
        "promoted": False, "probability_publishable": False,
        "certification_eligible": False, "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Local frozen nflverse stats_player CSVs 2021-2025")
    parser.add_argument("--out", type=Path, required=True, help="Candidate artifact JSON output path")
    args = parser.parse_args()
    frame, provenance = load_source_files(args.input_dir)
    candidates = [build_candidate(build_feature_rows(frame, stat), stat, provenance) for stat in ROUTES]
    output = {
        "schema_version": "WOW_NFL_MISSING_COUNT_PROP_CANDIDATES_V1",
        "model_family": MODEL_FAMILY,
        "source": provenance,
        "candidates": candidates,
        "registration_attempted": False, "runtime_scoreable": False,
        "probability_publishable": False, "can_execute": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_stat_types": list(ROUTES), "output": str(args.out), "registration_attempted": False, "can_execute": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
