"""Offline training pipeline for WOW_PROP_FITTED_MODEL_V1, model family
MLB_BATTER_PLATE_APPEARANCES_NB_V1 (MLB batter plate appearances).

Real data only. Source: Supabase project `wow-engine-validation`
(iczfhsmjrrafhvcpmqhr), table `wow_mlb_retrosplits_rows`. Extracted with:

    select person_key, team_key, opponent_key, game_key, game_date,
           game_number, batting_slot, team_alignment, b_pa
    from wow_mlb_retrosplits_rows
    where season_phase = 'R' and b_pa is not null and batting_slot between 1 and 9
    order by person_key, game_date, game_number;

saved verbatim to data/mlb_batter_pa_2024_2025_regular_season_full.json
(101,631 rows). batting_slot = 0 rows (bench/non-starters, avg_pa exactly
0.0 -- confirmed by direct query before writing this script) are excluded:
this model is scoped to confirmed/projected starters only, matching how a
real PrizePicks PA line would only be posted for a starting batter.

Design rationale, grounded in a direct query of this same table before any
modeling: batting_slot is a strong, monotonic real predictor (slot 1 avg PA
4.14 down to slot 9 avg PA 2.90) and team_alignment has a real, smaller
effect (away avg 3.67 vs. home avg 3.52 -- consistent with home teams
sometimes not batting in the bottom of the 9th). Both are KNOWN pregame from
a confirmed/projected lineup card, unlike a pitcher's shortened-outing
status -- so this model conditions directly on (batting_slot, team_alignment)
rather than mixing over an unknown regime the way the pitcher workload
models do.

    mu(row) = shrink(player_prior_mean_pa, league_mean_pa[slot, alignment], n_prior, k)
    P(PA = k) = NB(k; mu, r)

  * player_prior_mean_pa: this player's own cumulative mean PA across ALL
    of his prior rows (any slot/alignment) strictly before this row's
    (game_date, game_number) -- a general per-player PA tendency signal.
  * league_mean_pa[slot, alignment]: TRAIN-only league average PA for the
    row's own (batting_slot, team_alignment) cell -- the context-specific
    baseline the player's own rate gets shrunk toward.
  * r: negative-binomial dispersion, fit once globally via method of
    moments on TRAIN residuals.

v1 scope note (explicit, not hidden): no opposing-starter-length,
bullpen-environment, game-script, or lineup-protection features yet (all
named in the original remediation spec as PA-relevant). Those need either
additional joins against pitcher rows in this same table (which is feasible
as v2 -- the data exists) or context this table doesn't carry (in-game
score state). This v1 is the batting-slot + alignment baseline only.

Every per-player quantity is computed strictly from rows with an earlier
(game_date, game_number) than the row being featurized -- same leakage-safe
forward-pass construction as the pitcher models.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__) + "/..")
from prop_model_adapters import nb_pmf, shrink as _shrink

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SOURCE_JSON = os.path.join(DATA_DIR, "mlb_batter_pa_2024_2025_regular_season_full.json")
ARTIFACT_OUT = os.path.join(DATA_DIR, "wow_mlb_plate_appearances_artifact_v1.json")
METRICS_OUT = os.path.join(DATA_DIR, "wow_mlb_plate_appearances_training_report_v1.json")

MODEL_FAMILY = "MLB_BATTER_PLATE_APPEARANCES_NB_V1"
MODEL_ARTIFACT_VERSION = "MLB_BATTER_PLATE_APPEARANCES_NB_V1_2026_09_04"
CALIBRATOR_VERSION = "MLB_BATTER_PA_CAL_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "MLB_BATTER_PA_TRANSFORM_V1"
SPECIALIST_VERSION = "wow.mlb-batter-plate-appearances-expert@1"

SHRINKAGE_K_RATE = 10.0
MIN_PRIOR_STARTS = 1
MAX_SUPPORT_K = 8
TRAIN_END = "2025-01-01"
TEST_START = "2025-07-18"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


@dataclass
class BatterRow:
    person_key: str
    game_date: str
    game_number: int
    batting_slot: int
    team_alignment: int
    b_pa: int


def _load_rows() -> list[BatterRow]:
    with open(SOURCE_JSON) as f:
        raw = json.load(f)
    rows = [
        BatterRow(
            person_key=r["person_key"],
            game_date=r["game_date"],
            game_number=int(r["game_number"]),
            batting_slot=int(r["batting_slot"]),
            team_alignment=int(r["team_alignment"]),
            b_pa=int(r["b_pa"]),
        )
        for r in raw
    ]
    rows.sort(key=lambda x: (x.person_key, x.game_date, x.game_number))
    return rows


@dataclass
class FeaturedRow:
    person_key: str
    game_date: str
    n_prior: int
    prior_mean_pa: float
    batting_slot: int
    team_alignment: int
    actual_pa: int


def _build_featured_rows(rows: list[BatterRow]) -> list[FeaturedRow]:
    featured: list[FeaturedRow] = []
    acc: dict[str, dict] = {}
    for r in rows:
        a = acc.setdefault(r.person_key, {"n": 0, "pa_sum": 0})
        n_prior = a["n"]
        prior_mean_pa = (a["pa_sum"] / n_prior) if n_prior > 0 else float("nan")
        featured.append(
            FeaturedRow(
                person_key=r.person_key,
                game_date=r.game_date,
                n_prior=n_prior,
                prior_mean_pa=prior_mean_pa,
                batting_slot=r.batting_slot,
                team_alignment=r.team_alignment,
                actual_pa=r.b_pa,
            )
        )
        a["n"] += 1
        a["pa_sum"] += r.b_pa
    return featured


@dataclass
class GlobalConstants:
    league_mean_pa_by_cell: dict[tuple[int, int], float]
    league_mean_pa_overall: float
    dispersion_r: float


def _fit_global_constants(train_rows: list[FeaturedRow]) -> GlobalConstants:
    by_cell: dict[tuple[int, int], list[int]] = {}
    for r in train_rows:
        by_cell.setdefault((r.batting_slot, r.team_alignment), []).append(r.actual_pa)
    league_mean_pa_by_cell = {k: sum(v) / len(v) for k, v in by_cell.items()}
    all_pa = [r.actual_pa for r in train_rows]
    league_mean_pa_overall = sum(all_pa) / len(all_pa)

    eligible = [r for r in train_rows if r.n_prior >= MIN_PRIOR_STARTS]
    mus, actuals = [], []
    for r in eligible:
        cell_mean = league_mean_pa_by_cell.get((r.batting_slot, r.team_alignment), league_mean_pa_overall)
        mu = _shrink(r.prior_mean_pa, cell_mean, r.n_prior, SHRINKAGE_K_RATE)
        mus.append(mu)
        actuals.append(r.actual_pa)

    mean_mu = sum(mus) / len(mus)
    var = sum((a - m) ** 2 for a, m in zip(actuals, mus)) / len(mus)
    excess = max(var - mean_mu, 1e-6)
    dispersion_r = max((mean_mu ** 2) / excess, 1.0)

    return GlobalConstants(
        league_mean_pa_by_cell=league_mean_pa_by_cell,
        league_mean_pa_overall=league_mean_pa_overall,
        dispersion_r=dispersion_r,
    )


def _predict_pmf(row: FeaturedRow, gc: GlobalConstants) -> dict[int, float]:
    cell_mean = gc.league_mean_pa_by_cell.get((row.batting_slot, row.team_alignment), gc.league_mean_pa_overall)
    mu = _shrink(row.prior_mean_pa, cell_mean, row.n_prior, SHRINKAGE_K_RATE)
    return nb_pmf(mu, gc.dispersion_r, MAX_SUPPORT_K)


def _log_loss(rows: list[FeaturedRow], gc: GlobalConstants) -> float:
    eligible = [r for r in rows if r.n_prior >= MIN_PRIOR_STARTS]
    total = 0.0
    for r in eligible:
        pmf = _predict_pmf(r, gc)
        k = min(r.actual_pa, MAX_SUPPORT_K)
        p = max(pmf.get(k, 1e-9), 1e-9)
        total += -math.log(p)
    return total / len(eligible) if eligible else float("nan")


def _naive_baseline_log_loss(train_rows: list[FeaturedRow], test_rows: list[FeaturedRow]) -> float:
    """Single unconditional NB fit on TRAIN actuals -- no per-player signal,
    no slot/alignment conditioning. The bar this model must clear."""
    train_actuals = [r.actual_pa for r in train_rows]
    mean_mu = sum(train_actuals) / len(train_actuals)
    var = sum((a - mean_mu) ** 2 for a in train_actuals) / len(train_actuals)
    excess = max(var - mean_mu, 1e-6)
    r_naive = max((mean_mu ** 2) / excess, 1.0)
    pmf = nb_pmf(mean_mu, r_naive, MAX_SUPPORT_K)
    total, n = 0.0, 0
    for row in test_rows:
        k = min(row.actual_pa, MAX_SUPPORT_K)
        p = max(pmf.get(k, 1e-9), 1e-9)
        total += -math.log(p)
        n += 1
    return total / n if n else float("nan")


def _slot_only_baseline_log_loss(train_rows: list[FeaturedRow], test_rows: list[FeaturedRow], gc: GlobalConstants) -> float:
    """League slot/alignment mean only, NO per-player shrinkage. Isolates
    how much the per-player signal is actually contributing versus just
    knowing the batting order slot."""
    total, n = 0.0, 0
    # dispersion refit against the slot-only mean for a fair comparison
    mus_train, actuals_train = [], []
    for r in train_rows:
        mu = gc.league_mean_pa_by_cell.get((r.batting_slot, r.team_alignment), gc.league_mean_pa_overall)
        mus_train.append(mu)
        actuals_train.append(r.actual_pa)
    mean_mu = sum(mus_train) / len(mus_train)
    var = sum((a - m) ** 2 for a, m in zip(actuals_train, mus_train)) / len(mus_train)
    excess = max(var - mean_mu, 1e-6)
    r_slot = max((mean_mu ** 2) / excess, 1.0)
    for row in test_rows:
        mu = gc.league_mean_pa_by_cell.get((row.batting_slot, row.team_alignment), gc.league_mean_pa_overall)
        pmf = nb_pmf(mu, r_slot, MAX_SUPPORT_K)
        k = min(row.actual_pa, MAX_SUPPORT_K)
        p = max(pmf.get(k, 1e-9), 1e-9)
        total += -math.log(p)
        n += 1
    return total / n if n else float("nan")


def _mae(rows: list[FeaturedRow], gc: GlobalConstants) -> float:
    eligible = [r for r in rows if r.n_prior >= MIN_PRIOR_STARTS]
    total = 0.0
    for r in eligible:
        pmf = _predict_pmf(r, gc)
        mean_pred = sum(k * p for k, p in pmf.items())
        total += abs(mean_pred - r.actual_pa)
    return total / len(eligible) if eligible else float("nan")


def main() -> None:
    rows = _load_rows()
    featured = _build_featured_rows(rows)

    train_rows = [r for r in featured if r.game_date < TRAIN_END]
    test_rows = [r for r in featured if r.game_date >= TEST_START]
    if not train_rows or not test_rows:
        raise RuntimeError(f"empty split: train={len(train_rows)} test={len(test_rows)}")

    gc = _fit_global_constants(train_rows)

    train_log_loss = _log_loss(train_rows, gc)
    test_log_loss = _log_loss(test_rows, gc)
    test_mae = _mae(test_rows, gc)
    naive_test_log_loss = _naive_baseline_log_loss(train_rows, test_rows)
    slot_only_test_log_loss = _slot_only_baseline_log_loss(train_rows, test_rows, gc)

    source_hash = _sha256_file(SOURCE_JSON)
    code_hash = _sha256_file(__file__)

    artifact_payload = {
        "model_family": MODEL_FAMILY,
        "artifact_format": "PROP_NB_SHRINKAGE_V1",
        "training_rows": len(train_rows),
        "artifact_payload": {
            "league_mean_pa_by_cell": {f"{k[0]}_{k[1]}": v for k, v in gc.league_mean_pa_by_cell.items()},
            "league_mean_pa_overall": gc.league_mean_pa_overall,
            "dispersion_r": gc.dispersion_r,
            "shrinkage_k_rate": SHRINKAGE_K_RATE,
            "min_prior_starts": MIN_PRIOR_STARTS,
            "max_support_k": MAX_SUPPORT_K,
        },
        "validation_metrics": {
            "train_log_loss": train_log_loss,
            "test_log_loss": test_log_loss,
            "test_mae_pa": test_mae,
            "naive_baseline_test_log_loss": naive_test_log_loss,
            "slot_alignment_only_test_log_loss": slot_only_test_log_loss,
            "log_loss_improvement_vs_naive": naive_test_log_loss - test_log_loss,
            "log_loss_improvement_vs_slot_only": slot_only_test_log_loss - test_log_loss,
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
        "supported_stat_type": "PLATE_APPEARANCES",
        "supported_line_min": 2.5,
        "supported_line_max": 5.5,
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
