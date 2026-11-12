#!/usr/bin/env python3
"""Train five independent NFL count-prop *research candidates* from local nflverse CSVs.

This is an offline production-source builder, not a scorer, a certification engine,
a runtime adapter, or a promotion path. No odds or LLM estimates enter fitting.
All outputs are always CANDIDATE/NOT_PUBLISHABLE and can_execute=False.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
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
# Frozen public source digests from https://api.github.com/repos/nflverse/nflverse-data/releases/tags/stats_player
# Changes to the upstream release require source review; never silently accept drift.
EXPECTED_RELEASE_SHA256 = {
    "2021": "41915fb49238902ad1f129ebf0405b11a1e710454ae0fe8f7b3e4f9145875f48",
    "2022": "ad426c3fe5bf1cc30c3f137fdfe96d054e19d400879ee4413129da49fa7b54be",
    "2023": "f19cb71a5de0dce7fd09376026237c9ee9d5a93fe13815a2ea3ec2d37204cb17",
    "2024": "3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759",
    "2025": "e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2",
}
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
    """Return SHA-256 for the exact source bytes."""
    return hashlib.sha256(payload).hexdigest()


def _json_digest(value: Any) -> str:
    """Canonical SHA-256 of a JSON object."""
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _is_missing_identity(value: Any) -> bool:
    """Classify only genuinely absent identity tokens; never synthesize a player."""
    return bool(pd.isna(value) or str(value).strip().upper() in {"", "NA", "NAN", "NONE", "NULL"})


def _quarantine_unattributed_zero_stat_rows(
    frame: pd.DataFrame, season: int
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Preserve an audit ledger for non-route, all-zero records with absent IDs.

    Never quarantine a QB/RB/WR/TE, named person, malformed event, or a row
    with nonzero/missing count-prop fields. Unknown participation and source
    row meaning remain explicitly UNREVIEWED even after this research filter.
    """
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError("NFL_PROP_SOURCE_COLUMNS_MISSING:" + ",".join(missing))
    regular = frame["season_type"].astype(str).str.upper().isin(("REG", "REGULAR"))
    absent = frame["player_id"].map(_is_missing_identity)
    quarantine: list[dict[str, Any]] = []
    for row_index, row in frame.loc[regular & absent].iterrows():
        position = str(row["position"]).strip().upper() if not _is_missing_identity(row["position"]) else ""
        if (not _is_missing_identity(row["player_display_name"])
                or position in {"QB", "RB", "WR", "TE"}
                or _is_missing_identity(row["game_id"])):
            raise ValueError("NFL_PROP_SOURCE_IDENTITY_INVALID:player_id")
        try:
            parsed_season = float(row["season"])
            week = float(row["week"])
        except (TypeError, ValueError):
            raise ValueError("NFL_PROP_SOURCE_DATE_INVALID:unattributed") from None
        if (not math.isfinite(parsed_season) or parsed_season != season
                or not math.isfinite(week) or not week.is_integer() or not 1 <= week <= 18):
            raise ValueError("NFL_PROP_SOURCE_DATE_OUT_OF_SCOPE")
        # Anonymous records still require a canonical, self-consistent event.
        # All-zero fields cannot legitimize a malformed or stale game identity.
        game_id = str(row["game_id"]).strip()
        if not re.fullmatch(
            rf"{season}_{int(week):02d}_[A-Z0-9]+_[A-Z0-9]+", game_id
        ):
            raise ValueError("NFL_PROP_SOURCE_IDENTITY_INVALID:game_id")
        counts = pd.to_numeric(pd.Series({field: row[field] for field in COUNT_COLUMNS}), errors="coerce")
        if not np.isfinite(counts.to_numpy(dtype=float)).all() or counts.ne(0).any():
            raise ValueError("NFL_PROP_SOURCE_UNATTRIBUTED_COUNT_NONZERO_OR_MISSING")
        record = {
            "source_line": int(row_index) + 2, "source_season": season,
            "game_id": game_id, "week": int(week),
            "position_token": position, "count_fields_all_zero": True,
            "source_identity_unresolved": True,
        }
        record["audit_sha256"] = _json_digest(record)
        quarantine.append(record)
    return frame.drop(index=[entry["source_line"] - 2 for entry in quarantine]), quarantine


def load_source_files(
    directory: Path, *, expected_sha256: dict[str, str] | None = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load only frozen local files, recording actual bytes; never download in a scorer."""
    directory = Path(directory)
    frames = []
    assets = {}
    identity_quarantine: list[dict[str, Any]] = []
    for season in SEASONS:
        path = directory / f"stats_player_week_{season}.csv"
        if not path.is_file():
            raise ValueError(f"NFL_PROP_SOURCE_FILE_MISSING:{season}")
        blob = path.read_bytes()
        actual_sha256 = _sha256(blob)
        if expected_sha256 is not None and actual_sha256 != expected_sha256.get(str(season)):
            raise ValueError(f"NFL_PROP_SOURCE_DIGEST_MISMATCH:{season}")
        frame = pd.read_csv(io.BytesIO(blob), low_memory=False)
        # A filename does not establish season coverage. Validate *each* snapshot
        # before combining seasons so an empty/mislabelled file cannot be masked.
        eligible, unattributed = _quarantine_unattributed_zero_stat_rows(frame, season)
        checked = validate_frame(eligible)
        identity_quarantine.extend(unattributed)
        if not checked["season"].eq(season).all():
            raise ValueError(f"NFL_PROP_SOURCE_SEASON_MISMATCH:{season}")
        assets[str(season)] = {"file": path.name, "sha256": actual_sha256, "rows": len(frame), "model_rows": len(checked), "unattributed_identity_rows": len(unattributed)}
        frames.append(checked)
    provenance = {
        "provider": "NFLVERSE_STATS_PLAYER", "license_claim": "CC-BY-4.0",
        "source_rights_independently_approved": False,
        "caller_expected_hashes_matched": expected_sha256 is not None,
        "release_hashes_verified": (expected_sha256 is not None
                                    and expected_sha256 == EXPECTED_RELEASE_SHA256),
        "unattributed_identity_quarantine": identity_quarantine,
        "source_identity_quarantine_independently_reviewed": False,
        "candidate_training_authorized": False,
        "assets": assets, "bundle_sha256": _json_digest(assets),
        "can_execute": False,
    }
    return validate_frame(pd.concat(frames, ignore_index=True, sort=False)), provenance



def observe_anonymous_weekly_source_shape(
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Describe verified-source anonymous group shape without approving labels.

    The original weekly generator groups by season/week/player_id and may
    collapse absent player IDs into a single group. A matching shape is not
    evidence that those rows identify real player-games or have usable labels.
    This observation cannot activate a source, fitted model or execution.
    """
    invalid = "NFL_PROP_SOURCE_IDENTITY_INVALID:anonymous_group_provenance"
    if not isinstance(provenance, dict) or provenance.get("release_hashes_verified") is not True:
        raise ValueError(invalid)
    assets = provenance.get("assets")
    if not isinstance(assets, dict) or set(assets) != {str(y) for y in SEASONS}:
        raise ValueError(invalid)
    for year in SEASONS:
        asset = assets[str(year)]
        if (not isinstance(asset, dict)
                or asset.get("sha256") != EXPECTED_RELEASE_SHA256[str(year)]):
            raise ValueError(invalid)
    quarantined = provenance.get("unattributed_identity_quarantine")
    if not isinstance(quarantined, list):
        raise ValueError(invalid)

    by_season = {str(y): 0 for y in SEASONS}
    weeks = {str(y): [] for y in SEASONS}
    keys = {
        "source_line", "source_season", "game_id", "week",
        "position_token", "count_fields_all_zero",
        "source_identity_unresolved", "audit_sha256",
    }
    for row in quarantined:
        if not isinstance(row, dict) or set(row) != keys:
            raise ValueError(invalid)
        evidence = {k: v for k, v in row.items() if k != "audit_sha256"}
        if row["audit_sha256"] != _json_digest(evidence):
            raise ValueError(invalid)
        year, week = row["source_season"], row["week"]
        if (type(row["source_line"]) is not int or row["source_line"] < 2
                or type(year) is not int or year not in SEASONS
                or type(week) is not int or not 1 <= week <= 18
                or not isinstance(row["position_token"], str)
                or row["position_token"].strip().upper() in {"QB", "RB", "WR", "TE"}
                or row["count_fields_all_zero"] is not True
                or row["source_identity_unresolved"] is not True
                or not isinstance(row["game_id"], str)
                or not re.fullmatch(
                    rf"{year}_{week:02d}_[A-Z0-9]+_[A-Z0-9]+",
                    row["game_id"],
                )):
            raise ValueError(invalid)
        by_season[str(year)] += 1
        weeks[str(year)].append(week)
    shape = all(len(weeks[str(y)]) == 18 and
                set(weeks[str(y)]) == set(range(1, 19)) for y in SEASONS)
    return {
        "status": "HOLD_ANONYMOUS_WEEKLY_SOURCE_SEMANTICS",
        "unattributed_rows": len(quarantined),
        "by_season": by_season,
        "one_unattributed_group_per_week_shape": shape,
        "source_generator_equivalence_proven": False,
        "individual_player_outcomes_verified": False,
        "source_rights_independently_approved": False,
        "fitted_training_authorized": False,
        "can_execute": False,
    }


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
    if not result["season"].isin(SEASONS).all() or not result["week"].between(1, 18).all():
        raise ValueError("NFL_PROP_SOURCE_DATE_OUT_OF_SCOPE")
    for field in ("player_id", "game_id", "position", "player_display_name"):
        if result[field].isna().any() or (result[field].astype(str).str.strip() == "").any():
            raise ValueError(f"NFL_PROP_SOURCE_IDENTITY_INVALID:{field}")
        result[field] = result[field].astype(str).str.strip()
    result["position"] = result["position"].str.upper()
    if (result.duplicated(["player_id", "game_id"]).any()
            or result.duplicated(["player_id", "season", "week"]).any()):
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
    """Fit five held candidates only after validating input/output path separation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Local frozen nflverse stats_player CSVs 2021-2025")
    parser.add_argument("--out", type=Path, required=True, help="Candidate artifact JSON output path")
    args = parser.parse_args()
    # Resolve symbolic links, relative aliases and hard links before any output
    # write. An output collision otherwise destroys the training evidence.
    resolved_output = args.out.resolve()
    for season in SEASONS:
        source = args.input_dir / f"stats_player_week_{season}.csv"
        if resolved_output == source.resolve() or (args.out.exists() and source.exists()
                                                    and os.path.samefile(args.out, source)):
            raise ValueError(f"NFL_PROP_SOURCE_OUTPUT_COLLISION:{season}")
    frame, provenance = load_source_files(args.input_dir, expected_sha256=EXPECTED_RELEASE_SHA256)
    # The hash check alone cannot adjudicate anonymous groups, missing
    # participant games or upstream rights. Fail closed before any model fit.
    if not (provenance.get("release_hashes_verified") is True
            and provenance.get("source_identity_quarantine_independently_reviewed") is True
            and provenance.get("source_rights_independently_approved") is True
            and provenance.get("candidate_training_authorized") is True):
        raise ValueError("NFL_PROP_FITTING_SOURCE_ADMISSION_HOLD")
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
