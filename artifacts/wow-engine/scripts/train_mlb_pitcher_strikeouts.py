"""Offline training pipeline for WOW_PROP_FITTED_MODEL_V1, model family
MLB_PITCHER_SO_FAILURE_PATH_NB_V1 (MLB starting-pitcher strikeouts).

Real data only. Source: Supabase project `wow-engine-validation`
(iczfhsmjrrafhvcpmqhr), table `wow_mlb_retrosplits_rows` -- Retrosheet-derived
per-player-per-game rows, `research_only = true`. Extracted with:

    select person_key, team_key, opponent_key, game_key, game_date,
           game_number, team_alignment, p_out, p_so, p_tbf, p_bb, p_h, p_er
    from wow_mlb_retrosplits_rows
    where p_gs > 0 and season_phase = 'R'
    order by person_key, game_date, game_number;

    select team_key, game_key, game_date, sum(b_so) as team_so, sum(b_pa) as team_pa
    from wow_mlb_retrosplits_rows
    where season_phase = 'R' and b_pa > 0
    group by team_key, game_key, game_date
    order by team_key, game_date;

saved verbatim to data/mlb_pitcher_starts_2024_2025_regular_season.csv and
data/mlb_team_batting_by_game_2024_2025_regular_season.csv (9,718 rows each;
regular season only, 2024-03-20 through 2025-09-28).

This script performs feature construction, temporal (leakage-safe) splitting,
model fitting, and out-of-sample evaluation against a naive baseline. It does
not touch any runtime/production module. Its output is one artifact JSON
payload plus a metrics report; a human/governance step decides whether to
register it in wow_prop_fitted_model_artifacts and at what lifecycle_state.

Design (unconditional, two-regime discrete count model):
    P(SO = k) = p_short * NB(k; mu_short, r) + (1 - p_short) * NB(k; mu_normal, r)

  * mu_normal / mu_short: this pitcher's own shrunk strikeout-per-out rate,
    multiplied by a *global* (league) mean workload (outs recorded) for
    completed-length vs. shortened outings respectively, times an optional
    opponent contact/discipline adjustment.
  * p_short: this pitcher's own shrunk shortened-outing rate.
  * r: negative-binomial dispersion, fit once globally via method of moments
    on TRAIN residuals.

Every per-pitcher / per-opponent quantity is computed strictly from rows
with an earlier (game_date, game_number) than the row being featurized --
enforced by construction (single forward pass over a chronologically sorted
per-entity sequence, only ever reading accumulator state from *before* the
update for the current row).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__) + "/..")
from prop_model_adapters import nb_pmf, shrink as _shrink  # noqa: E402  (shared with runtime adapter)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PITCHER_CSV = os.path.join(DATA_DIR, "mlb_pitcher_starts_2024_2025_regular_season.csv")
TEAM_BATTING_CSV = os.path.join(DATA_DIR, "mlb_team_batting_by_game_2024_2025_regular_season.csv")
ARTIFACT_OUT = os.path.join(DATA_DIR, "wow_mlb_pitcher_strikeouts_artifact_v1.json")
METRICS_OUT = os.path.join(DATA_DIR, "wow_mlb_pitcher_strikeouts_training_report_v1.json")

MODEL_FAMILY = "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
MODEL_ARTIFACT_VERSION = "MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29"
CALIBRATOR_VERSION = "MLB_PITCHER_SO_CAL_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "MLB_PITCHER_SO_TRANSFORM_V1"
SPECIALIST_VERSION = "wow.mlb-pitcher-failure-path-expert@1"

SHRINKAGE_K_RATE = 8.0       # prior-start count at which pitcher-specific rate gets ~50% weight
SHRINKAGE_K_REGIME = 8.0     # same, for the shortened-outing regime probability
SHORTENED_OUTS_THRESHOLD = 15  # < 5 innings pitched (15 outs) counts as a shortened outing
MIN_PRIOR_STARTS = 1          # a row needs >=1 real prior start to have any pitcher-specific signal
MAX_SUPPORT_K = 20            # truncated discrete support; tail folded into MAX_SUPPORT_K bucket
TRAIN_END = "2025-01-01"        # 2024 season only
TEST_START = "2025-07-18"       # last ~30% of 2025 season, held untouched during fitting
OPPONENT_FACTOR_CLIP = (0.75, 1.30)


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
    team_key: str
    opponent_key: str
    game_key: str
    game_date: str
    game_number: int
    team_alignment: int
    p_out: int
    p_so: int
    p_tbf: int
    p_bb: int
    p_h: int
    p_er: int


def _load_pitcher_starts() -> list[PitcherStart]:
    rows: list[PitcherStart] = []
    with open(PITCHER_CSV, newline="") as f:
        for r in csv.DictReader(f):
            rows.append(
                PitcherStart(
                    person_key=r["person_key"],
                    team_key=r["team_key"],
                    opponent_key=r["opponent_key"],
                    game_key=r["game_key"],
                    game_date=r["game_date"],
                    game_number=int(r["game_number"]),
                    team_alignment=int(r["team_alignment"]),
                    p_out=int(r["p_out"]),
                    p_so=int(r["p_so"]),
                    p_tbf=int(r["p_tbf"]),
                    p_bb=int(r["p_bb"]),
                    p_h=int(r["p_h"]),
                    p_er=int(r["p_er"]),
                )
            )
    rows.sort(key=lambda x: (x.person_key, x.game_date, x.game_number))
    return rows


def _load_team_batting_by_game() -> dict[str, list[tuple[str, int, int]]]:
    """team_key -> chronologically sorted list of (game_date, team_so, team_pa)."""
    by_team: dict[str, list[tuple[str, int, int]]] = {}
    with open(TEAM_BATTING_CSV, newline="") as f:
        for r in csv.DictReader(f):
            by_team.setdefault(r["team_key"], []).append(
                (r["game_date"], int(r["team_so"]), int(r["team_pa"]))
            )
    for team, seq in by_team.items():
        seq.sort(key=lambda x: x[0])
    return by_team


def _prior_team_k_rate(by_team: dict, team: str, before_date: str) -> tuple[float, int]:
    """Cumulative (team_so, team_pa) strictly before `before_date`. O(n) scan;
    dataset is small enough (~9.7k rows) that this stays fast without an index."""
    seq = by_team.get(team, [])
    so_sum = pa_sum = 0
    for game_date, so, pa in seq:
        if game_date >= before_date:
            break
        so_sum += so
        pa_sum += pa
    if pa_sum <= 0:
        return (float("nan"), 0)
    return (so_sum / pa_sum, pa_sum)


@dataclass
class FeaturedRow:
    person_key: str
    game_date: str
    n_prior_starts: int
    prior_so_per_out: float
    prior_mean_out: float
    prior_shortened_rate: float
    opponent_prior_k_per_pa: float
    actual_so: int
    actual_out: int
    is_shortened: bool


def _build_featured_rows(starts: list[PitcherStart], by_team: dict) -> list[FeaturedRow]:
    """Single forward pass per pitcher. Accumulator state used for row i is
    read BEFORE row i's own stats are folded in, so no row ever sees its own
    outcome (or any later row's outcome) in its features."""
    featured: list[FeaturedRow] = []
    acc: dict[str, dict] = {}
    for s in starts:
        a = acc.setdefault(
            s.person_key, {"n": 0, "so_sum": 0, "out_sum": 0, "short_n": 0}
        )
        n_prior = a["n"]
        prior_so_per_out = (a["so_sum"] / a["out_sum"]) if a["out_sum"] > 0 else float("nan")
        prior_mean_out = (a["out_sum"] / n_prior) if n_prior > 0 else float("nan")
        prior_shortened_rate = (a["short_n"] / n_prior) if n_prior > 0 else float("nan")
        opp_k_rate, opp_pa = _prior_team_k_rate(by_team, s.opponent_key, s.game_date)

        is_shortened = s.p_out < SHORTENED_OUTS_THRESHOLD
        featured.append(
            FeaturedRow(
                person_key=s.person_key,
                game_date=s.game_date,
                n_prior_starts=n_prior,
                prior_so_per_out=prior_so_per_out,
                prior_mean_out=prior_mean_out,
                prior_shortened_rate=prior_shortened_rate,
                opponent_prior_k_per_pa=opp_k_rate if opp_pa >= 50 else float("nan"),
                actual_so=s.p_so,
                actual_out=s.p_out,
                is_shortened=is_shortened,
            )
        )
        a["n"] += 1
        a["so_sum"] += s.p_so
        a["out_sum"] += s.p_out
        a["short_n"] += int(is_shortened)
    return featured


@dataclass
class GlobalConstants:
    league_so_per_out: float
    league_k_per_pa: float
    league_shortened_rate: float
    outs_normal_scale: float
    outs_short_scale: float
    dispersion_r: float


def _fit_global_constants(train_rows: list[FeaturedRow]) -> GlobalConstants:
    eligible = [r for r in train_rows if r.n_prior_starts >= MIN_PRIOR_STARTS]

    total_so_prior = sum(r.prior_so_per_out * 1 for r in eligible if not math.isnan(r.prior_so_per_out))
    valid_rate_rows = [r for r in eligible if not math.isnan(r.prior_so_per_out)]
    league_so_per_out = (
        sum(r.prior_so_per_out for r in valid_rate_rows) / len(valid_rate_rows)
        if valid_rate_rows else 0.17
    )

    opp_rows = [r.opponent_prior_k_per_pa for r in eligible if not math.isnan(r.opponent_prior_k_per_pa)]
    league_k_per_pa = sum(opp_rows) / len(opp_rows) if opp_rows else 0.22

    league_shortened_rate = sum(int(r.is_shortened) for r in eligible) / len(eligible)

    normal_outs = [r.actual_out for r in eligible if not r.is_shortened]
    short_outs = [r.actual_out for r in eligible if r.is_shortened]
    outs_normal_scale = sum(normal_outs) / len(normal_outs) if normal_outs else 18.0
    outs_short_scale = sum(short_outs) / len(short_outs) if short_outs else 9.0

    # Method-of-moments dispersion from mixture-mean residuals.
    mus, actuals = [], []
    for r in eligible:
        p_short = _shrink(r.prior_shortened_rate, league_shortened_rate, r.n_prior_starts, SHRINKAGE_K_REGIME)
        rate = _shrink(r.prior_so_per_out, league_so_per_out, r.n_prior_starts, SHRINKAGE_K_RATE)
        opp_factor = 1.0
        if not math.isnan(r.opponent_prior_k_per_pa):
            opp_factor = min(max(r.opponent_prior_k_per_pa / league_k_per_pa, OPPONENT_FACTOR_CLIP[0]), OPPONENT_FACTOR_CLIP[1])
        mu_normal = rate * outs_normal_scale * opp_factor
        mu_short = rate * outs_short_scale * opp_factor
        mus.append(p_short * mu_short + (1 - p_short) * mu_normal)
        actuals.append(r.actual_so)

    mean_mu = sum(mus) / len(mus)
    var = sum((a - m) ** 2 for a, m in zip(actuals, mus)) / len(mus)
    if var > mean_mu:
        dispersion_r = (mean_mu ** 2) / (var - mean_mu)
    else:
        dispersion_r = 1000.0  # ~Poisson fallback if data is not overdispersed

    return GlobalConstants(
        league_so_per_out=league_so_per_out,
        league_k_per_pa=league_k_per_pa,
        league_shortened_rate=league_shortened_rate,
        outs_normal_scale=outs_normal_scale,
        outs_short_scale=outs_short_scale,
        dispersion_r=dispersion_r,
    )


def _row_pmf(r: FeaturedRow, gc: GlobalConstants) -> tuple[dict[int, float], float, float, float]:
    p_short = _shrink(r.prior_shortened_rate, gc.league_shortened_rate, r.n_prior_starts, SHRINKAGE_K_REGIME)
    rate = _shrink(r.prior_so_per_out, gc.league_so_per_out, r.n_prior_starts, SHRINKAGE_K_RATE)
    opp_factor = 1.0
    if not math.isnan(r.opponent_prior_k_per_pa):
        opp_factor = min(max(r.opponent_prior_k_per_pa / gc.league_k_per_pa, OPPONENT_FACTOR_CLIP[0]), OPPONENT_FACTOR_CLIP[1])
    mu_normal = rate * gc.outs_normal_scale * opp_factor
    mu_short = rate * gc.outs_short_scale * opp_factor
    pmf_normal = nb_pmf(mu_normal, gc.dispersion_r, MAX_SUPPORT_K)
    pmf_short = nb_pmf(mu_short, gc.dispersion_r, MAX_SUPPORT_K)
    mixed = {k: p_short * pmf_short.get(k, 0.0) + (1 - p_short) * pmf_normal.get(k, 0.0) for k in range(MAX_SUPPORT_K + 1)}
    return mixed, p_short, mu_normal, mu_short


def _nll(pmf: dict[int, float], actual: int) -> float:
    k = min(actual, MAX_SUPPORT_K)
    p = max(pmf.get(k, 0.0), 1e-12)
    return -math.log(p)


def _baseline_constants(train_rows: list[FeaturedRow]) -> tuple[float, float]:
    actuals = [r.actual_so for r in train_rows]
    mean_mu = sum(actuals) / len(actuals)
    var = sum((a - mean_mu) ** 2 for a in actuals) / len(actuals)
    r = (mean_mu ** 2) / (var - mean_mu) if var > mean_mu else 1000.0
    return mean_mu, r


def main() -> None:
    starts = _load_pitcher_starts()
    by_team = _load_team_batting_by_game()
    featured = _build_featured_rows(starts, by_team)

    train_rows = [r for r in featured if r.game_date < TRAIN_END and r.n_prior_starts >= MIN_PRIOR_STARTS]
    calibration_rows = [r for r in featured if TRAIN_END <= r.game_date < TEST_START and r.n_prior_starts >= MIN_PRIOR_STARTS]
    test_rows = [r for r in featured if r.game_date >= TEST_START and r.n_prior_starts >= MIN_PRIOR_STARTS]

    gc = _fit_global_constants(train_rows)
    baseline_mu, baseline_r = _baseline_constants(train_rows)
    baseline_pmf = nb_pmf(baseline_mu, baseline_r, MAX_SUPPORT_K)

    model_nlls, baseline_nlls = [], []
    model_briers, baseline_briers = [], []
    for r in test_rows:
        pmf, p_short, mu_normal, mu_short = _row_pmf(r, gc)
        model_nlls.append(_nll(pmf, r.actual_so))
        baseline_nlls.append(_nll(baseline_pmf, r.actual_so))

        mixture_mean = p_short * mu_short + (1 - p_short) * mu_normal
        line = round(mixture_mean) - 0.5  # model-implied representative half-line
        p_more_model = sum(p for k, p in pmf.items() if k > line)
        p_more_baseline = sum(p for k, p in baseline_pmf.items() if k > line)
        actual_more = 1.0 if r.actual_so > line else 0.0
        model_briers.append((p_more_model - actual_more) ** 2)
        baseline_briers.append((p_more_baseline - actual_more) ** 2)

    metrics = {
        "train_rows": len(train_rows),
        "calibration_rows": len(calibration_rows),
        "test_rows": len(test_rows),
        "train_date_range": [min(r.game_date for r in train_rows), max(r.game_date for r in train_rows)],
        "calibration_date_range": (
            [min(r.game_date for r in calibration_rows), max(r.game_date for r in calibration_rows)]
            if calibration_rows else None
        ),
        "test_date_range": [min(r.game_date for r in test_rows), max(r.game_date for r in test_rows)],
        "model_mean_nll": sum(model_nlls) / len(model_nlls),
        "baseline_mean_nll": sum(baseline_nlls) / len(baseline_nlls),
        "model_mean_brier_at_model_implied_line": sum(model_briers) / len(model_briers),
        "baseline_mean_brier_at_model_implied_line": sum(baseline_briers) / len(baseline_briers),
        "model_beats_baseline_nll": (sum(model_nlls) / len(model_nlls)) < (sum(baseline_nlls) / len(baseline_nlls)),
        "model_beats_baseline_brier": (sum(model_briers) / len(model_briers)) < (sum(baseline_briers) / len(baseline_briers)),
        "fitted_constants": {
            "league_so_per_out": gc.league_so_per_out,
            "league_k_per_pa": gc.league_k_per_pa,
            "league_shortened_rate": gc.league_shortened_rate,
            "outs_normal_scale": gc.outs_normal_scale,
            "outs_short_scale": gc.outs_short_scale,
            "dispersion_r": gc.dispersion_r,
        },
        "baseline_constants": {"mu": baseline_mu, "r": baseline_r},
    }

    pitcher_hash = _sha256_file(PITCHER_CSV)
    team_hash = _sha256_file(TEAM_BATTING_CSV)
    dataset_manifest = {
        "source": "supabase:wow-engine-validation:iczfhsmjrrafhvcpmqhr:wow_mlb_retrosplits_rows",
        "research_only": True,
        "pitcher_starts_csv": os.path.basename(PITCHER_CSV),
        "pitcher_starts_sha256": pitcher_hash,
        "pitcher_starts_rows": len(starts),
        "team_batting_csv": os.path.basename(TEAM_BATTING_CSV),
        "team_batting_sha256": team_hash,
        "extraction_filter": "p_gs > 0 and season_phase = 'R' (pitcher rows); b_pa > 0 and season_phase = 'R' grouped by team/game (batting rows)",
    }
    training_dataset_hash = _sha256_text(json.dumps(dataset_manifest, sort_keys=True))
    training_code_sha = _sha256_file(__file__)

    artifact_payload = {
        "model_family": MODEL_FAMILY,
        "model_artifact_version": MODEL_ARTIFACT_VERSION,
        "calibrator_version": CALIBRATOR_VERSION,
        "sport": "MLB",
        "stat_type": "PITCHER_STRIKEOUTS",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "training_dataset_hash": training_dataset_hash,
        "training_code_sha": training_code_sha,
        "training_rows": len(train_rows),
        "supported_line_min": 0.5,
        "supported_line_max": 12.5,
        "shrinkage_k_rate": SHRINKAGE_K_RATE,
        "shrinkage_k_regime": SHRINKAGE_K_REGIME,
        "shortened_outs_threshold": SHORTENED_OUTS_THRESHOLD,
        "min_prior_starts": MIN_PRIOR_STARTS,
        "max_support_k": MAX_SUPPORT_K,
        "opponent_factor_clip": list(OPPONENT_FACTOR_CLIP),
        "fitted_constants": metrics["fitted_constants"],
        "dataset_manifest": dataset_manifest,
        "validation_metrics": {
            "model_mean_nll": metrics["model_mean_nll"],
            "baseline_mean_nll": metrics["baseline_mean_nll"],
            "model_mean_brier_at_model_implied_line": metrics["model_mean_brier_at_model_implied_line"],
            "baseline_mean_brier_at_model_implied_line": metrics["baseline_mean_brier_at_model_implied_line"],
            "test_rows": len(test_rows),
        },
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ARTIFACT_OUT, "w") as f:
        json.dump(artifact_payload, f, indent=2, sort_keys=True)
    with open(METRICS_OUT, "w") as f:
        json.dump(metrics, f, indent=2, sort_keys=True)

    artifact_checksum = _sha256_file(ARTIFACT_OUT)
    print(json.dumps({**metrics, "training_dataset_hash": training_dataset_hash,
                       "training_code_sha": training_code_sha,
                       "artifact_checksum": artifact_checksum}, indent=2, default=str))


if __name__ == "__main__":
    main()
