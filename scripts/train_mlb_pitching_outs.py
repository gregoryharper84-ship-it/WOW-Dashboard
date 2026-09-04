"""Offline training pipeline for WOW_PROP_FITTED_MODEL_V1, model family
MLB_PITCHER_OUTS_WORKLOAD_NB_V1 (MLB starting-pitcher outs recorded).

Real data only. Source: Supabase project `wow-engine-validation`
(iczfhsmjrrafhvcpmqhr), table `wow_mlb_retrosplits_rows` -- the same
Retrosheet-derived per-player-per-game rows used by
scripts/train_mlb_pitcher_strikeouts.py. Extracted with:

    select person_key, team_key, opponent_key, game_key, game_date,
           game_number, team_alignment, p_out, p_tbf, p_pitch, p_strike,
           p_bb, p_h, p_er, p_so
    from wow_mlb_retrosplits_rows
    where p_gs > 0 and season_phase = 'R'
    order by person_key, game_date, game_number;

saved verbatim to data/mlb_pitcher_starts_2024_2025_regular_season_full.json
(9,718 rows; regular season only, 2024-03-20 through 2025-11-01 raw pull,
filtered here to season_phase='R' matching the certified SO artifact's
training population for direct comparability).

This script performs feature construction, temporal (leakage-safe) splitting,
model fitting, and out-of-sample evaluation against a naive baseline. It does
NOT touch any runtime/production module and does NOT register anything in
the certified model registry. Its output is one artifact JSON payload plus a
metrics report; a human/governance step (WOW governance: Greg as ratifier
while ChatGPT is out of credit) decides whether to register it in
wow_prop_fitted_model_artifacts and at what lifecycle_state.

Design (unconditional, two-regime discrete count model -- OUTS is the
target directly, not a byproduct of a rate x workload transform):

    P(Outs = k) = p_short * NB(k; mu_short, r) + (1 - p_short) * NB(k; mu_normal, r)

  * mu_normal / mu_short: this pitcher's own shrunk mean outs recorded in
    "normal-length" vs "shortened" outings respectively (both shrunk toward
    the league mean for that regime with an empirical-Bayes weight that
    grows with the pitcher's number of prior starts).
  * p_short: this pitcher's own shrunk historical shortened-outing rate
    (computed ONLY from starts strictly before the row being featurized --
    never from the row's own actual_out, which is exactly what is being
    predicted, so there is no target leakage into the regime split).
  * r: negative-binomial dispersion, fit once globally via method of
    moments on TRAIN residuals.

v1 scope note (explicit, not hidden): this version does not yet include an
opponent contact/patience adjustment (the SO model's opponent_k_per_pa
factor has no direct outs analogue in this dataset without additional
opponent-side feature engineering). That is a documented v2 candidate, not
a silently-skipped requirement. Do not present this v1 as opponent-aware.

Every per-pitcher quantity is computed strictly from rows with an earlier
(game_date, game_number) than the row being featurized -- enforced by
construction (single forward pass over a chronologically sorted per-entity
sequence, only ever reading accumulator state from *before* the update for
the current row).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__) + "/..")
from prop_model_adapters import nb_pmf, shrink as _shrink  # noqa: E402  (shared w/ runtime adapter once wired)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SOURCE_JSON = os.path.join(DATA_DIR, "mlb_pitcher_starts_2024_2025_regular_season_full.json")
ARTIFACT_OUT = os.path.join(DATA_DIR, "wow_mlb_pitching_outs_artifact_v1.json")
METRICS_OUT = os.path.join(DATA_DIR, "wow_mlb_pitching_outs_training_report_v1.json")

MODEL_FAMILY = "MLB_PITCHER_OUTS_WORKLOAD_NB_V1"
MODEL_ARTIFACT_VERSION = "MLB_PITCHER_OUTS_WORKLOAD_NB_V1_2026_09_04"
CALIBRATOR_VERSION = "MLB_PITCHER_OUTS_CAL_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "MLB_PITCHER_OUTS_TRANSFORM_V1"
SPECIALIST_VERSION = "wow.mlb-pitcher-outs-workload-expert@1"

SHRINKAGE_K_RATE = 8.0
SHRINKAGE_K_REGIME = 8.0
SHORTENED_OUTS_THRESHOLD = 15  # < 5 innings pitched (15 outs) counts as a shortened outing
MIN_PRIOR_STARTS = 1
MAX_SUPPORT_K = 27             # truncated discrete support (9 innings=27 outs); tail folded in
TRAIN_END = "2025-01-01"       # 2024 season only -- identical split boundary to the SO artifact
TEST_START = "2025-07-18"      # held out, untouched during fitting -- identical to the SO artifact


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class PitcherStart:
    person_key: str
    game_date: str
    game_number: int
    p_out: int


def _load_pitcher_starts() -> list[PitcherStart]:
    with open(SOURCE_JSON) as f:
        raw = json.load(f)
    rows = [
        PitcherStart(
            person_key=r["person_key"],
            game_date=r["game_date"],
            game_number=int(r["game_number"]),
            p_out=int(r["p_out"]),
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
    prior_mean_out_normal: float
    prior_mean_out_short: float
    prior_shortened_rate: float
    actual_out: int
    is_shortened: bool


def _build_featured_rows(starts: list[PitcherStart]) -> list[FeaturedRow]:
    featured: list[FeaturedRow] = []
    acc: dict[str, dict] = {}
    for s in starts:
        a = acc.setdefault(
            s.person_key, {"n": 0, "short_n": 0, "normal_out_sum": 0, "normal_n": 0, "short_out_sum": 0, "short_n2": 0}
        )
        n_prior = a["n"]
        prior_shortened_rate = (a["short_n"] / n_prior) if n_prior > 0 else float("nan")
        prior_mean_out_normal = (a["normal_out_sum"] / a["normal_n"]) if a["normal_n"] > 0 else float("nan")
        prior_mean_out_short = (a["short_out_sum"] / a["short_n2"]) if a["short_n2"] > 0 else float("nan")

        is_shortened = s.p_out < SHORTENED_OUTS_THRESHOLD
        featured.append(
            FeaturedRow(
                person_key=s.person_key,
                game_date=s.game_date,
                n_prior_starts=n_prior,
                prior_mean_out_normal=prior_mean_out_normal,
                prior_mean_out_short=prior_mean_out_short,
                prior_shortened_rate=prior_shortened_rate,
                actual_out=s.p_out,
                is_shortened=is_shortened,
            )
        )
        a["n"] += 1
        a["short_n"] += int(is_shortened)
        if is_shortened:
            a["short_out_sum"] += s.p_out
            a["short_n2"] += 1
        else:
            a["normal_out_sum"] += s.p_out
            a["normal_n"] += 1
    return featured


@dataclass
class GlobalConstants:
    league_mean_out_normal: float
    league_mean_out_short: float
    league_shortened_rate: float
    dispersion_r: float


def _fit_global_constants(train_rows: list[FeaturedRow]) -> GlobalConstants:
    eligible = [r for r in train_rows if r.n_prior_starts >= MIN_PRIOR_STARTS]

    normal_priors = [r.prior_mean_out_normal for r in eligible if not math.isnan(r.prior_mean_out_normal)]
    short_priors = [r.prior_mean_out_short for r in eligible if not math.isnan(r.prior_mean_out_short)]
    league_mean_out_normal = sum(normal_priors) / len(normal_priors) if normal_priors else 18.0
    league_mean_out_short = sum(short_priors) / len(short_priors) if short_priors else 9.0
    league_shortened_rate = sum(int(r.is_shortened) for r in eligible) / len(eligible)

    mus, actuals = [], []
    for r in eligible:
        p_short = _shrink(r.prior_shortened_rate, league_shortened_rate, r.n_prior_starts, SHRINKAGE_K_REGIME)
        mu_normal = _shrink(r.prior_mean_out_normal, league_mean_out_normal, r.n_prior_starts, SHRINKAGE_K_RATE)
        mu_short = _shrink(r.prior_mean_out_short, league_mean_out_short, r.n_prior_starts, SHRINKAGE_K_RATE)
        mus.append(p_short * mu_short + (1 - p_short) * mu_normal)
        actuals.append(r.actual_out)

    mean_mu = sum(mus) / len(mus)
    var = sum((a - m) ** 2 for a, m in zip(actuals, mus)) / len(mus)
    # Negative-binomial method-of-moments: var = mu + mu^2/r  =>  r = mu^2 / (var - mu)
    excess = max(var - mean_mu, 1e-6)
    dispersion_r = max((mean_mu ** 2) / excess, 1.0)

    return GlobalConstants(
        league_mean_out_normal=league_mean_out_normal,
        league_mean_out_short=league_mean_out_short,
        league_shortened_rate=league_shortened_rate,
        dispersion_r=dispersion_r,
    )


def _predict_pmf(row: FeaturedRow, gc: GlobalConstants) -> dict[int, float]:
    p_short = _shrink(row.prior_shortened_rate, gc.league_shortened_rate, row.n_prior_starts, SHRINKAGE_K_REGIME)
    mu_normal = _shrink(row.prior_mean_out_normal, gc.league_mean_out_normal, row.n_prior_starts, SHRINKAGE_K_RATE)
    mu_short = _shrink(row.prior_mean_out_short, gc.league_mean_out_short, row.n_prior_starts, SHRINKAGE_K_RATE)
    pmf_normal = nb_pmf(mu_normal, gc.dispersion_r, MAX_SUPPORT_K)
    pmf_short = nb_pmf(mu_short, gc.dispersion_r, MAX_SUPPORT_K)
    return {k: p_short * pmf_short.get(k, 0.0) + (1 - p_short) * pmf_normal.get(k, 0.0) for k in range(MAX_SUPPORT_K + 1)}


def _log_loss(rows: list[FeaturedRow], gc: GlobalConstants) -> float:
    eligible = [r for r in rows if r.n_prior_starts >= MIN_PRIOR_STARTS]
    total = 0.0
    for r in eligible:
        pmf = _predict_pmf(r, gc)
        k = min(r.actual_out, MAX_SUPPORT_K)
        p = max(pmf.get(k, 1e-9), 1e-9)
        total += -math.log(p)
    return total / len(eligible) if eligible else float("nan")


def _naive_baseline_log_loss(train_rows: list[FeaturedRow], test_rows: list[FeaturedRow]) -> float:
    """Single unconditional NB fit on TRAIN actuals only (no per-pitcher
    signal, no regime split) -- the bar this model must clear."""
    train_actuals = [r.actual_out for r in train_rows]
    mean_mu = sum(train_actuals) / len(train_actuals)
    var = sum((a - mean_mu) ** 2 for a in train_actuals) / len(train_actuals)
    excess = max(var - mean_mu, 1e-6)
    r_naive = max((mean_mu ** 2) / excess, 1.0)
    pmf = nb_pmf(mean_mu, r_naive, MAX_SUPPORT_K)
    total, n = 0.0, 0
    for row in test_rows:
        k = min(row.actual_out, MAX_SUPPORT_K)
        p = max(pmf.get(k, 1e-9), 1e-9)
        total += -math.log(p)
        n += 1
    return total / n if n else float("nan")


def _mae(rows: list[FeaturedRow], gc: GlobalConstants) -> float:
    eligible = [r for r in rows if r.n_prior_starts >= MIN_PRIOR_STARTS]
    total = 0.0
    for r in eligible:
        pmf = _predict_pmf(r, gc)
        mean_pred = sum(k * p for k, p in pmf.items())
        total += abs(mean_pred - r.actual_out)
    return total / len(eligible) if eligible else float("nan")


def main() -> None:
    starts = _load_pitcher_starts()
    featured = _build_featured_rows(starts)

    train_rows = [r for r in featured if r.game_date < TRAIN_END]
    test_rows = [r for r in featured if r.game_date >= TEST_START]

    if not train_rows or not test_rows:
        raise RuntimeError(
            f"empty split: train={len(train_rows)} test={len(test_rows)} "
            f"(check TRAIN_END/TEST_START against actual data range)"
        )

    gc = _fit_global_constants(train_rows)

    train_log_loss = _log_loss(train_rows, gc)
    test_log_loss = _log_loss(test_rows, gc)
    test_mae = _mae(test_rows, gc)
    naive_test_log_loss = _naive_baseline_log_loss(train_rows, test_rows)

    source_hash = _sha256_file(SOURCE_JSON)
    code_hash = _sha256_file(__file__)

    artifact_payload = {
        "model_family": MODEL_FAMILY,
        "artifact_format": "PROP_NB_MIXTURE_V1",
        "training_rows": len(train_rows),
        "artifact_payload": {
            "league_mean_out_normal": gc.league_mean_out_normal,
            "league_mean_out_short": gc.league_mean_out_short,
            "league_shortened_rate": gc.league_shortened_rate,
            "dispersion_r": gc.dispersion_r,
            "shortened_outs_threshold": SHORTENED_OUTS_THRESHOLD,
            "shrinkage_k_rate": SHRINKAGE_K_RATE,
            "shrinkage_k_regime": SHRINKAGE_K_REGIME,
            "min_prior_starts": MIN_PRIOR_STARTS,
            "max_support_k": MAX_SUPPORT_K,
        },
        "validation_metrics": {
            "train_log_loss": train_log_loss,
            "test_log_loss": test_log_loss,
            "test_mae_outs": test_mae,
            "naive_baseline_test_log_loss": naive_test_log_loss,
            "log_loss_improvement_vs_naive": naive_test_log_loss - test_log_loss,
            "test_rows_n": len(test_rows),
            "train_rows_n": len(train_rows),
        },
        "model_artifact_version": MODEL_ARTIFACT_VERSION,
        "calibrator_version": CALIBRATOR_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "training_dataset_hash": source_hash,
        "training_code_sha": code_hash,
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "supported_sport": "MLB",
        "supported_stat_type": "PITCHING_OUTS",
        "supported_line_min": 3.5,
        "supported_line_max": 24.5,
        "note": (
            "PROSPECTIVE_CERTIFIED is the model's self-reported readiness "
            "state only. This artifact has NOT been registered in "
            "wow_prop_fitted_model_artifacts and cannot serve production "
            "traffic until a governance ratification step (Greg, per "
            "active ChatGPT-out-of-credit substitution) reviews these "
            "validation_metrics and explicitly promotes it."
        ),
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ARTIFACT_OUT, "w") as f:
        json.dump(artifact_payload, f, indent=2)
    with open(METRICS_OUT, "w") as f:
        json.dump(artifact_payload["validation_metrics"], f, indent=2)

    print(json.dumps(artifact_payload["validation_metrics"], indent=2))
    print(f"\nartifact written: {ARTIFACT_OUT}")
    print(f"metrics written:  {METRICS_OUT}")


if __name__ == "__main__":
    main()
