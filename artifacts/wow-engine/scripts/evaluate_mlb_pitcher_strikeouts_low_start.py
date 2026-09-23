"""Read-only Class C replay for MLB pitcher-strikeout low-start coverage.

This experiment evaluates the already-fitted MLB_PITCHER_SO_FAILURE_PATH_NB_V1
artifact on chronological held-out rows grouped by the amount of *prior* MLB
starting-pitcher history available at inference time. It exists to test whether
the production hydration floor of ten prior starts is stricter than the fitted
model's demonstrated support (the artifact itself declares min_prior_starts=1).

It does not edit, register, promote, recalibrate, or route any production model.
It never consumes sportsbook implied probability. can_execute remains false.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
PITCHER_CSV = DATA_DIR / "mlb_pitcher_starts_2024_2025_regular_season.csv"
TEAM_BATTING_CSV = DATA_DIR / "mlb_team_batting_by_game_2024_2025_regular_season.csv"
ARTIFACT_PATH = DATA_DIR / "wow_mlb_pitcher_strikeouts_artifact_v1.json"
TRAINING_REPORT_PATH = DATA_DIR / "wow_mlb_pitcher_strikeouts_training_report_v1.json"

TEST_START = "2025-07-18"
MAX_SUPPORT_K = 20
MIN_OPPONENT_PRIOR_PA = 50
ECE_BINS = 10


@dataclass(frozen=True)
class ReplayRow:
    game_date: str
    n_prior_starts: int
    prior_so_per_out: float
    prior_shortened_rate: float
    opponent_prior_k_per_pa: float | None
    actual_so: int
    actual_out: int


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _shrink(value: float, league_value: float, n: float, k: float) -> float:
    if not math.isfinite(value):
        return league_value
    lam = n / (n + k)
    return league_value + lam * (value - league_value)


def _nb_pmf(mu: float, r: float, max_k: int = MAX_SUPPORT_K) -> dict[int, float]:
    if mu <= 0:
        mu = 1e-6
    p = r / (r + mu)
    log_p = math.log(p)
    log_1mp = math.log(1.0 - p)
    running = 0.0
    pmf: dict[int, float] = {}
    for k in range(max_k):
        log_coef = math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        prob = math.exp(log_coef + r * log_p + k * log_1mp)
        pmf[k] = prob
        running += prob
    pmf[max_k] = max(0.0, 1.0 - running)
    return pmf


def _load_team_history() -> dict[str, tuple[list[str], list[int], list[int]]]:
    by_team: dict[str, list[tuple[str, int, int]]] = {}
    with TEAM_BATTING_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            by_team.setdefault(row["team_key"], []).append(
                (row["game_date"], int(row["team_so"]), int(row["team_pa"]))
            )

    history: dict[str, tuple[list[str], list[int], list[int]]] = {}
    for team, rows in by_team.items():
        rows.sort(key=lambda item: item[0])
        dates: list[str] = []
        so_prefix: list[int] = [0]
        pa_prefix: list[int] = [0]
        for game_date, so, pa in rows:
            dates.append(game_date)
            so_prefix.append(so_prefix[-1] + so)
            pa_prefix.append(pa_prefix[-1] + pa)
        history[team] = (dates, so_prefix, pa_prefix)
    return history


def _opponent_prior_k_rate(
    history: dict[str, tuple[list[str], list[int], list[int]]],
    team: str,
    before_date: str,
) -> float | None:
    payload = history.get(team)
    if payload is None:
        return None
    dates, so_prefix, pa_prefix = payload
    idx = bisect.bisect_left(dates, before_date)
    pa = pa_prefix[idx]
    if pa < MIN_OPPONENT_PRIOR_PA:
        return None
    return so_prefix[idx] / pa


def _build_rows() -> list[ReplayRow]:
    team_history = _load_team_history()
    raw: list[dict[str, str]] = []
    with PITCHER_CSV.open(newline="", encoding="utf-8") as handle:
        raw.extend(csv.DictReader(handle))
    raw.sort(key=lambda row: (row["person_key"], row["game_date"], int(row["game_number"])))

    acc: dict[str, dict[str, float]] = {}
    rows: list[ReplayRow] = []
    for source in raw:
        person = source["person_key"]
        state = acc.setdefault(person, {"n": 0.0, "so": 0.0, "outs": 0.0, "short": 0.0})
        n_prior = int(state["n"])
        prior_so_per_out = state["so"] / state["outs"] if state["outs"] > 0 else float("nan")
        prior_shortened_rate = state["short"] / state["n"] if state["n"] > 0 else float("nan")
        actual_out = int(source["p_out"])
        actual_so = int(source["p_so"])
        rows.append(
            ReplayRow(
                game_date=source["game_date"],
                n_prior_starts=n_prior,
                prior_so_per_out=prior_so_per_out,
                prior_shortened_rate=prior_shortened_rate,
                opponent_prior_k_per_pa=_opponent_prior_k_rate(
                    team_history, source["opponent_key"], source["game_date"]
                ),
                actual_so=actual_so,
                actual_out=actual_out,
            )
        )
        state["n"] += 1
        state["so"] += actual_so
        state["outs"] += actual_out
        state["short"] += int(actual_out < 15)
    return rows


def _model_pmf(row: ReplayRow, artifact: dict) -> tuple[dict[int, float], float]:
    fitted = artifact["fitted_constants"]
    n = row.n_prior_starts
    rate = _shrink(
        row.prior_so_per_out,
        float(fitted["league_so_per_out"]),
        n,
        float(artifact["shrinkage_k_rate"]),
    )
    p_short = _shrink(
        row.prior_shortened_rate,
        float(fitted["league_shortened_rate"]),
        n,
        float(artifact["shrinkage_k_regime"]),
    )
    opp_factor = 1.0
    if row.opponent_prior_k_per_pa is not None and float(fitted["league_k_per_pa"]) > 0:
        low, high = (float(v) for v in artifact["opponent_factor_clip"])
        opp_factor = min(
            max(row.opponent_prior_k_per_pa / float(fitted["league_k_per_pa"]), low),
            high,
        )
    mu_normal = rate * float(fitted["outs_normal_scale"]) * opp_factor
    mu_short = rate * float(fitted["outs_short_scale"]) * opp_factor
    normal = _nb_pmf(mu_normal, float(fitted["dispersion_r"]), int(artifact["max_support_k"]))
    short = _nb_pmf(mu_short, float(fitted["dispersion_r"]), int(artifact["max_support_k"]))
    mixed = {k: p_short * short[k] + (1.0 - p_short) * normal[k] for k in normal}
    mean = p_short * mu_short + (1.0 - p_short) * mu_normal
    return mixed, mean


def _ece(predictions: list[float], outcomes: list[float]) -> float:
    if not predictions:
        return float("nan")
    total = len(predictions)
    error = 0.0
    for index in range(ECE_BINS):
        lo = index / ECE_BINS
        hi = (index + 1) / ECE_BINS
        members = [i for i, p in enumerate(predictions) if lo <= p < hi or (index == ECE_BINS - 1 and p == 1.0)]
        if not members:
            continue
        mean_p = sum(predictions[i] for i in members) / len(members)
        mean_y = sum(outcomes[i] for i in members) / len(members)
        error += (len(members) / total) * abs(mean_p - mean_y)
    return error


def _bucket_name(n_prior: int) -> str:
    if 1 <= n_prior <= 2:
        return "1-2"
    if 3 <= n_prior <= 5:
        return "3-5"
    if 6 <= n_prior <= 9:
        return "6-9"
    return "10+"


def _evaluate(rows: Iterable[ReplayRow], artifact: dict, baseline_pmf: dict[int, float]) -> dict:
    rows = list(rows)
    model_nll: list[float] = []
    baseline_nll: list[float] = []
    model_brier: list[float] = []
    baseline_brier: list[float] = []
    predictions: list[float] = []
    outcomes: list[float] = []

    for row in rows:
        pmf, mean = _model_pmf(row, artifact)
        actual_k = min(row.actual_so, int(artifact["max_support_k"]))
        model_nll.append(-math.log(max(pmf.get(actual_k, 0.0), 1e-12)))
        baseline_nll.append(-math.log(max(baseline_pmf.get(actual_k, 0.0), 1e-12)))

        line = round(mean) - 0.5
        p_more = sum(prob for k, prob in pmf.items() if k > line)
        p_more_baseline = sum(prob for k, prob in baseline_pmf.items() if k > line)
        actual_more = 1.0 if row.actual_so > line else 0.0
        model_brier.append((p_more - actual_more) ** 2)
        baseline_brier.append((p_more_baseline - actual_more) ** 2)
        predictions.append(p_more)
        outcomes.append(actual_more)

    if not rows:
        return {"rows": 0}

    model_mean_nll = sum(model_nll) / len(model_nll)
    baseline_mean_nll = sum(baseline_nll) / len(baseline_nll)
    model_mean_brier = sum(model_brier) / len(model_brier)
    baseline_mean_brier = sum(baseline_brier) / len(baseline_brier)
    predicted_rate = sum(predictions) / len(predictions)
    observed_rate = sum(outcomes) / len(outcomes)
    return {
        "rows": len(rows),
        "model_mean_nll": model_mean_nll,
        "baseline_mean_nll": baseline_mean_nll,
        "model_beats_baseline_nll": model_mean_nll < baseline_mean_nll,
        "model_mean_brier_at_model_implied_line": model_mean_brier,
        "baseline_mean_brier_at_model_implied_line": baseline_mean_brier,
        "model_beats_baseline_brier": model_mean_brier < baseline_mean_brier,
        "mean_predicted_more_probability": predicted_rate,
        "observed_more_rate": observed_rate,
        "absolute_calibration_gap": abs(predicted_rate - observed_rate),
        "ece_10_bin": _ece(predictions, outcomes),
        "shortened_outing_rate": sum(1 for row in rows if row.actual_out < 15) / len(rows),
    }


def build_report() -> dict:
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    training_report = json.loads(TRAINING_REPORT_PATH.read_text(encoding="utf-8"))
    baseline = training_report["baseline_constants"]
    baseline_pmf = _nb_pmf(float(baseline["mu"]), float(baseline["r"]), int(artifact["max_support_k"]))

    all_rows = [
        row for row in _build_rows()
        if row.game_date >= TEST_START and row.n_prior_starts >= int(artifact["min_prior_starts"])
    ]
    buckets: dict[str, dict] = {}
    for name in ("1-2", "3-5", "6-9", "10+"):
        buckets[name] = _evaluate((row for row in all_rows if _bucket_name(row.n_prior_starts) == name), artifact, baseline_pmf)

    low_start = _evaluate((row for row in all_rows if 1 <= row.n_prior_starts <= 9), artifact, baseline_pmf)
    incumbent_gate = _evaluate((row for row in all_rows if row.n_prior_starts >= 10), artifact, baseline_pmf)
    enough_rows = low_start.get("rows", 0) >= 100
    beats_both = bool(low_start.get("model_beats_baseline_nll")) and bool(low_start.get("model_beats_baseline_brier"))

    return {
        "experiment": "MLB_PITCHER_STRIKEOUT_LOW_START_COVERAGE_REPLAY_V1",
        "classification": "CLASS_C_CHALLENGER_ONLY",
        "production_change": False,
        "can_execute": False,
        "test_start": TEST_START,
        "artifact_model_family": artifact["model_family"],
        "artifact_version": artifact["model_artifact_version"],
        "artifact_declared_min_prior_starts": artifact["min_prior_starts"],
        "production_hydration_min_starts": 10,
        "pitcher_csv_sha256": _sha256(PITCHER_CSV),
        "team_batting_csv_sha256": _sha256(TEAM_BATTING_CSV),
        "buckets": buckets,
        "low_start_1_to_9": low_start,
        "incumbent_gate_10_plus": incumbent_gate,
        "replay_gate": {
            "minimum_low_start_test_rows": 100,
            "has_minimum_rows": enough_rows,
            "beats_naive_baseline_on_nll_and_brier": beats_both,
            "status": (
                "EVIDENCE_SUFFICIENT_FOR_GOVERNED_THRESHOLD_REVIEW"
                if enough_rows and beats_both
                else "KEEP_CURRENT_PRODUCTION_GATE"
            ),
        },
        "notes": [
            "This replay does not promote or alter the production hydration threshold.",
            "The existing Phase A calibrator uses history length as n_eff, so smaller samples are automatically shrunk more strongly and receive bootstrap bounds; lower-bound validity still requires explicit regression before any promotion.",
            "No MiLB table is used by this replay; it tests whether the incumbent fitted MLB model already has defensible 1-9-start support.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
