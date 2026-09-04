"""Offline training pipeline for WOW_PROP_FITTED_MODEL_V1, model families
MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1 and
MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1 (MLB starting-pitcher pitch
composition).

Real data only. Same source as scripts/train_mlb_pitching_outs.py: Supabase
project `wow-engine-validation` (iczfhsmjrrafhvcpmqhr), table
`wow_mlb_retrosplits_rows`, p_gs > 0, season_phase = 'R'. Reuses the already-
extracted data/mlb_pitcher_starts_2024_2025_regular_season_full.json
(9,718 rows) -- no new extraction needed, p_pitch and p_strike were already
pulled alongside p_out.

    p_strike  -> Strikes Thrown target, directly.
    p_pitch - p_strike -> Balls Thrown target, derived.

This script does NOT touch any runtime/production module and does NOT
register anything in the certified model registry. Two artifact JSON
payloads plus two metrics reports; a human/governance step decides whether
to register either one, matching the pattern already established for the
strikeouts and pitching-outs artifacts.

Design, per target (two-regime discrete count model, same family used for
Pitching Outs -- pitch composition scales with how long a pitcher stays in
the game, so the same shortened/normal outing regime split applies):

    P(Strikes = k) = p_short * NB(k; mu_short_strikes, r_strikes)
                    + (1 - p_short) * NB(k; mu_normal_strikes, r_strikes)

    P(Balls = k)   = p_short * NB(k; mu_short_balls, r_balls)
                    + (1 - p_short) * NB(k; mu_normal_balls, r_balls)

  * mu_normal_* / mu_short_*: this pitcher's own shrunk mean strikes/balls
    in normal-length vs shortened outings respectively (empirical-Bayes
    shrinkage toward the league mean for that regime, weight growing with
    prior start count).
  * p_short: this pitcher's own shrunk historical shortened-outing rate,
    computed identically to the Pitching Outs model (regime membership is
    defined from each historical row's own p_out, never from strikes/balls
    themselves, so there is no target leakage into the regime split for
    either target).
  * r_strikes, r_balls: two SEPARATE negative-binomial dispersions, each
    fit by its own method-of-moments pass on TRAIN residuals. Strikes and
    Balls are correlated (they sum to total pitches) but are fit and
    evaluated as fully independent targets -- no shared calibration, per
    the original remediation spec's explicit requirement that these stay
    "separately calibrated targets."

v1 scope note (explicit, not hidden): no opponent contact/patience
adjustment, same limitation as the Pitching Outs v1 artifact. Documented
v2 candidate, not silently skipped.

Every per-pitcher quantity is computed strictly from rows with an earlier
(game_date, game_number) than the row being featurized -- same leakage-safe
forward-pass construction as train_mlb_pitching_outs.py.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__) + "/..")
from prop_model_adapters import nb_pmf, shrink as _shrink  # shared w/ runtime adapters once wired

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SOURCE_JSON = os.path.join(DATA_DIR, "mlb_pitcher_starts_2024_2025_regular_season_full.json")
STRIKES_ARTIFACT_OUT = os.path.join(DATA_DIR, "wow_mlb_strikes_thrown_artifact_v1.json")
STRIKES_METRICS_OUT = os.path.join(DATA_DIR, "wow_mlb_strikes_thrown_training_report_v1.json")
BALLS_ARTIFACT_OUT = os.path.join(DATA_DIR, "wow_mlb_balls_thrown_artifact_v1.json")
BALLS_METRICS_OUT = os.path.join(DATA_DIR, "wow_mlb_balls_thrown_training_report_v1.json")

FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
SHRINKAGE_K_RATE = 8.0
SHRINKAGE_K_REGIME = 8.0
SHORTENED_OUTS_THRESHOLD = 15   # identical definition to the Pitching Outs artifact
MIN_PRIOR_STARTS = 1
MAX_SUPPORT_K_STRIKES = 90      # generous truncation; tail folded into max bucket
MAX_SUPPORT_K_BALLS = 60
TRAIN_END = "2025-01-01"        # identical split boundary to the SO / Outs artifacts
TEST_START = "2025-07-18"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


@dataclass
class PitcherStart:
    person_key: str
    game_date: str
    game_number: int
    p_out: int
    p_pitch: int
    p_strike: int

    @property
    def balls(self) -> int:
        return self.p_pitch - self.p_strike


def _load_pitcher_starts() -> list[PitcherStart]:
    with open(SOURCE_JSON) as f:
        raw = json.load(f)
    rows = [
        PitcherStart(
            person_key=r["person_key"],
            game_date=r["game_date"],
            game_number=int(r["game_number"]),
            p_out=int(r["p_out"]),
            p_pitch=int(r["p_pitch"]),
            p_strike=int(r["p_strike"]),
        )
        for r in raw
    ]
    rows.sort(key=lambda x: (x.person_key, x.game_date, x.game_number))
    return rows


@dataclass
class FeaturedRow:
    person_key: str
    game_date: str
    n_prior_starts: int
    prior_shortened_rate: float
    prior_mean_strikes_normal: float
    prior_mean_strikes_short: float
    prior_mean_balls_normal: float
    prior_mean_balls_short: float
    actual_strikes: int
    actual_balls: int
    is_shortened: bool


def _build_featured_rows(starts: list[PitcherStart]) -> list[FeaturedRow]:
    featured: list[FeaturedRow] = []
    acc: dict[str, dict] = {}
    for s in starts:
        a = acc.setdefault(
            s.person_key,
            {
                "n": 0, "short_n": 0,
                "normal_strikes_sum": 0, "normal_n": 0,
                "short_strikes_sum": 0, "short_n2": 0,
                "normal_balls_sum": 0, "short_balls_sum": 0,
            },
        )
        n_prior = a["n"]
        prior_shortened_rate = (a["short_n"] / n_prior) if n_prior > 0 else float("nan")
        prior_mean_strikes_normal = (a["normal_strikes_sum"] / a["normal_n"]) if a["normal_n"] > 0 else float("nan")
        prior_mean_strikes_short = (a["short_strikes_sum"] / a["short_n2"]) if a["short_n2"] > 0 else float("nan")
        prior_mean_balls_normal = (a["normal_balls_sum"] / a["normal_n"]) if a["normal_n"] > 0 else float("nan")
        prior_mean_balls_short = (a["short_balls_sum"] / a["short_n2"]) if a["short_n2"] > 0 else float("nan")

        is_shortened = s.p_out < SHORTENED_OUTS_THRESHOLD
        featured.append(
            FeaturedRow(
                person_key=s.person_key,
                game_date=s.game_date,
                n_prior_starts=n_prior,
                prior_shortened_rate=prior_shortened_rate,
                prior_mean_strikes_normal=prior_mean_strikes_normal,
                prior_mean_strikes_short=prior_mean_strikes_short,
                prior_mean_balls_normal=prior_mean_balls_normal,
                prior_mean_balls_short=prior_mean_balls_short,
                actual_strikes=s.p_strike,
                actual_balls=s.balls,
                is_shortened=is_shortened,
            )
        )
        a["n"] += 1
        a["short_n"] += int(is_shortened)
        if is_shortened:
            a["short_strikes_sum"] += s.p_strike
            a["short_balls_sum"] += s.balls
            a["short_n2"] += 1
        else:
            a["normal_strikes_sum"] += s.p_strike
            a["normal_balls_sum"] += s.balls
            a["normal_n"] += 1
    return featured


@dataclass
class GlobalConstants:
    league_shortened_rate: float
    league_mean_normal: float
    league_mean_short: float
    dispersion_r: float


def _fit_target(
    train_rows: list[FeaturedRow],
    prior_normal_attr: str,
    prior_short_attr: str,
    actual_attr: str,
) -> GlobalConstants:
    eligible = [r for r in train_rows if r.n_prior_starts >= MIN_PRIOR_STARTS]

    normal_priors = [getattr(r, prior_normal_attr) for r in eligible if not math.isnan(getattr(r, prior_normal_attr))]
    short_priors = [getattr(r, prior_short_attr) for r in eligible if not math.isnan(getattr(r, prior_short_attr))]
    league_mean_normal = sum(normal_priors) / len(normal_priors) if normal_priors else 1.0
    league_mean_short = sum(short_priors) / len(short_priors) if short_priors else 1.0
    league_shortened_rate = sum(int(r.is_shortened) for r in eligible) / len(eligible)

    mus, actuals = [], []
    for r in eligible:
        p_short = _shrink(r.prior_shortened_rate, league_shortened_rate, r.n_prior_starts, SHRINKAGE_K_REGIME)
        mu_normal = _shrink(getattr(r, prior_normal_attr), league_mean_normal, r.n_prior_starts, SHRINKAGE_K_RATE)
        mu_short = _shrink(getattr(r, prior_short_attr), league_mean_short, r.n_prior_starts, SHRINKAGE_K_RATE)
        mus.append(p_short * mu_short + (1 - p_short) * mu_normal)
        actuals.append(getattr(r, actual_attr))

    mean_mu = sum(mus) / len(mus)
    var = sum((a - m) ** 2 for a, m in zip(actuals, mus)) / len(mus)
    excess = max(var - mean_mu, 1e-6)
    dispersion_r = max((mean_mu ** 2) / excess, 1.0)

    return GlobalConstants(
        league_shortened_rate=league_shortened_rate,
        league_mean_normal=league_mean_normal,
        league_mean_short=league_mean_short,
        dispersion_r=dispersion_r,
    )


def _predict_pmf(
    row: FeaturedRow, gc: GlobalConstants, prior_normal_attr: str, prior_short_attr: str, max_k: int
) -> dict[int, float]:
    p_short = _shrink(row.prior_shortened_rate, gc.league_shortened_rate, row.n_prior_starts, SHRINKAGE_K_REGIME)
    mu_normal = _shrink(getattr(row, prior_normal_attr), gc.league_mean_normal, row.n_prior_starts, SHRINKAGE_K_RATE)
    mu_short = _shrink(getattr(row, prior_short_attr), gc.league_mean_short, row.n_prior_starts, SHRINKAGE_K_RATE)
    pmf_normal = nb_pmf(mu_normal, gc.dispersion_r, max_k)
    pmf_short = nb_pmf(mu_short, gc.dispersion_r, max_k)
    return {k: p_short * pmf_short.get(k, 0.0) + (1 - p_short) * pmf_normal.get(k, 0.0) for k in range(max_k + 1)}


def _log_loss(
    rows: list[FeaturedRow], gc: GlobalConstants, prior_normal_attr: str, prior_short_attr: str,
    actual_attr: str, max_k: int,
) -> float:
    eligible = [r for r in rows if r.n_prior_starts >= MIN_PRIOR_STARTS]
    total = 0.0
    for r in eligible:
        pmf = _predict_pmf(r, gc, prior_normal_attr, prior_short_attr, max_k)
        k = min(getattr(r, actual_attr), max_k)
        p = max(pmf.get(k, 1e-9), 1e-9)
        total += -math.log(p)
    return total / len(eligible) if eligible else float("nan")


def _naive_baseline_log_loss(train_rows: list[FeaturedRow], test_rows: list[FeaturedRow], actual_attr: str, max_k: int) -> float:
    train_actuals = [getattr(r, actual_attr) for r in train_rows]
    mean_mu = sum(train_actuals) / len(train_actuals)
    var = sum((a - mean_mu) ** 2 for a in train_actuals) / len(train_actuals)
    excess = max(var - mean_mu, 1e-6)
    r_naive = max((mean_mu ** 2) / excess, 1.0)
    pmf = nb_pmf(mean_mu, r_naive, max_k)
    total, n = 0.0, 0
    for row in test_rows:
        k = min(getattr(row, actual_attr), max_k)
        p = max(pmf.get(k, 1e-9), 1e-9)
        total += -math.log(p)
        n += 1
    return total / n if n else float("nan")


def _mae(rows: list[FeaturedRow], gc: GlobalConstants, prior_normal_attr: str, prior_short_attr: str, actual_attr: str, max_k: int) -> float:
    eligible = [r for r in rows if r.n_prior_starts >= MIN_PRIOR_STARTS]
    total = 0.0
    for r in eligible:
        pmf = _predict_pmf(r, gc, prior_normal_attr, prior_short_attr, max_k)
        mean_pred = sum(k * p for k, p in pmf.items())
        total += abs(mean_pred - getattr(r, actual_attr))
    return total / len(eligible) if eligible else float("nan")


def _train_one_target(
    train_rows, test_rows, *, model_family, model_artifact_version, calibrator_version,
    feature_transform_version, specialist_version, prior_normal_attr, prior_short_attr,
    actual_attr, max_k, supported_stat_type, supported_line_min, supported_line_max,
    source_hash, code_hash,
) -> dict:
    gc = _fit_target(train_rows, prior_normal_attr, prior_short_attr, actual_attr)
    train_log_loss = _log_loss(train_rows, gc, prior_normal_attr, prior_short_attr, actual_attr, max_k)
    test_log_loss = _log_loss(test_rows, gc, prior_normal_attr, prior_short_attr, actual_attr, max_k)
    test_mae = _mae(test_rows, gc, prior_normal_attr, prior_short_attr, actual_attr, max_k)
    naive_test_log_loss = _naive_baseline_log_loss(train_rows, test_rows, actual_attr, max_k)

    return {
        "model_family": model_family,
        "artifact_format": "PROP_NB_MIXTURE_V1",
        "training_rows": len(train_rows),
        "artifact_payload": {
            "league_mean_normal": gc.league_mean_normal,
            "league_mean_short": gc.league_mean_short,
            "league_shortened_rate": gc.league_shortened_rate,
            "dispersion_r": gc.dispersion_r,
            "shortened_outs_threshold": SHORTENED_OUTS_THRESHOLD,
            "shrinkage_k_rate": SHRINKAGE_K_RATE,
            "shrinkage_k_regime": SHRINKAGE_K_REGIME,
            "min_prior_starts": MIN_PRIOR_STARTS,
            "max_support_k": max_k,
        },
        "validation_metrics": {
            "train_log_loss": train_log_loss,
            "test_log_loss": test_log_loss,
            "test_mae": test_mae,
            "naive_baseline_test_log_loss": naive_test_log_loss,
            "log_loss_improvement_vs_naive": naive_test_log_loss - test_log_loss,
            "test_rows_n": len(test_rows),
            "train_rows_n": len(train_rows),
        },
        "model_artifact_version": model_artifact_version,
        "calibrator_version": calibrator_version,
        "feature_transform_version": feature_transform_version,
        "specialist_version": specialist_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "training_dataset_hash": source_hash,
        "training_code_sha": code_hash,
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "supported_sport": "MLB",
        "supported_stat_type": supported_stat_type,
        "supported_line_min": supported_line_min,
        "supported_line_max": supported_line_max,
        "note": (
            "PROSPECTIVE_CERTIFIED is the model's self-reported readiness "
            "state only. This artifact has NOT been registered in "
            "wow_prop_fitted_model_artifacts and cannot serve production "
            "traffic until a governance ratification step (Greg, per "
            "active ChatGPT-out-of-credit substitution) reviews these "
            "validation_metrics and explicitly promotes it."
        ),
    }


def main() -> None:
    starts = _load_pitcher_starts()
    featured = _build_featured_rows(starts)

    train_rows = [r for r in featured if r.game_date < TRAIN_END]
    test_rows = [r for r in featured if r.game_date >= TEST_START]
    if not train_rows or not test_rows:
        raise RuntimeError(f"empty split: train={len(train_rows)} test={len(test_rows)}")

    source_hash = _sha256_file(SOURCE_JSON)
    code_hash = _sha256_file(__file__)

    strikes_artifact = _train_one_target(
        train_rows, test_rows,
        model_family="MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1",
        model_artifact_version="MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1_2026_09_04",
        calibrator_version="MLB_PITCHER_STRIKES_THROWN_CAL_V1",
        feature_transform_version="MLB_PITCHER_STRIKES_THROWN_TRANSFORM_V1",
        specialist_version="wow.mlb-pitcher-pitch-composition-expert@1",
        prior_normal_attr="prior_mean_strikes_normal", prior_short_attr="prior_mean_strikes_short",
        actual_attr="actual_strikes", max_k=MAX_SUPPORT_K_STRIKES,
        supported_stat_type="STRIKES_THROWN", supported_line_min=15.5, supported_line_max=85.5,
        source_hash=source_hash, code_hash=code_hash,
    )
    balls_artifact = _train_one_target(
        train_rows, test_rows,
        model_family="MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1",
        model_artifact_version="MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1_2026_09_04",
        calibrator_version="MLB_PITCHER_BALLS_THROWN_CAL_V1",
        feature_transform_version="MLB_PITCHER_BALLS_THROWN_TRANSFORM_V1",
        specialist_version="wow.mlb-pitcher-pitch-composition-expert@1",
        prior_normal_attr="prior_mean_balls_normal", prior_short_attr="prior_mean_balls_short",
        actual_attr="actual_balls", max_k=MAX_SUPPORT_K_BALLS,
        supported_stat_type="BALLS_THROWN", supported_line_min=10.5, supported_line_max=55.5,
        source_hash=source_hash, code_hash=code_hash,
    )

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STRIKES_ARTIFACT_OUT, "w") as f:
        json.dump(strikes_artifact, f, indent=2)
    with open(STRIKES_METRICS_OUT, "w") as f:
        json.dump(strikes_artifact["validation_metrics"], f, indent=2)
    with open(BALLS_ARTIFACT_OUT, "w") as f:
        json.dump(balls_artifact, f, indent=2)
    with open(BALLS_METRICS_OUT, "w") as f:
        json.dump(balls_artifact["validation_metrics"], f, indent=2)

    print("=== STRIKES THROWN ===")
    print(json.dumps(strikes_artifact["validation_metrics"], indent=2))
    print("\n=== BALLS THROWN ===")
    print(json.dumps(balls_artifact["validation_metrics"], indent=2))
    print(f"\nartifacts written under {DATA_DIR}")


if __name__ == "__main__":
    main()
