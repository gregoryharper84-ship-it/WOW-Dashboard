"""Non-serving, leak-guarded binary sports-forecast challenger experiment.

Research only. Never calls WOW scoring, prediction publication, registry or
terminal APIs. All data must be immutable, pregame-frozen evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite, log
from pathlib import Path
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class ResearchInputError(ValueError):
    """An experiment fixture does not satisfy the pregame/evidence contract."""


def _dt(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchInputError("INVALID_TIMESTAMP") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ResearchInputError("TIMESTAMP_NOT_TIMEZONE_AWARE")
    return dt.astimezone(timezone.utc)


def _prob(value: Any) -> float:
    if isinstance(value, bool):
        raise ResearchInputError("INVALID_PROBABILITY")
    try:
        p = float(value)
    except (TypeError, ValueError) as exc:
        raise ResearchInputError("INVALID_PROBABILITY") from exc
    if not isfinite(p) or not 0 <= p <= 1:
        raise ResearchInputError("INVALID_PROBABILITY")
    return p


@dataclass(frozen=True)
class Row:
    prediction_id: str
    official_event_id: str
    sport: str
    market: str
    event_start: datetime
    feature_asof: datetime
    outcome_available: datetime
    outcome: int
    features: Mapping[str, float]
    incumbent_probability: float | None = None


def parse_rows(raw: Sequence[Mapping[str, Any]]) -> list[Row]:
    if not isinstance(raw, list) or not raw:
        raise ResearchInputError("EMPTY_OR_NONLIST_DATA")
    rows: list[Row] = []
    seen: set[str] = set()
    feature_names: set[str] | None = None
    event_times: dict[str, datetime] = {}
    for value in raw:
        if not isinstance(value, dict):
            raise ResearchInputError("ROW_NOT_OBJECT")
        required = ("prediction_id", "official_event_id", "sport", "market")
        if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
            raise ResearchInputError("CANONICAL_IDENTITY_MISSING")
        pid = value["prediction_id"].strip()
        if pid in seen:
            raise ResearchInputError("DUPLICATE_PREDICTION_ID")
        seen.add(pid)
        start = _dt(value.get("event_start_utc"))
        asof = _dt(value.get("features_asof_utc"))
        available = _dt(value.get("outcome_available_utc"))
        if not (asof < start < available):
            raise ResearchInputError("PREGAME_OR_SETTLEMENT_TIME_INVALID")
        if (value.get("feature_evidence_kind") != "ARCHIVED_PREGAME_CAPTURE" or
            value.get("historical_reconstruction") is not False):
            raise ResearchInputError("FEATURE_PROVENANCE_UNCERTIFIED")
        captured = _dt(value.get("feature_snapshot_captured_at_utc"))
        if not (asof <= captured < start):
            raise ResearchInputError("FEATURE_SNAPSHOT_CAPTURE_TIMING_INVALID")
        receipt = value.get("feature_evidence_receipt_id")
        if not isinstance(receipt, str) or not receipt.strip():
            raise ResearchInputError("FEATURE_EVIDENCE_RECEIPT_MISSING")
        if value.get("outcome") not in (0, 1) or isinstance(value.get("outcome"), bool):
            raise ResearchInputError("INVALID_BINARY_OUTCOME")
        raw_features = value.get("features")
        if not isinstance(raw_features, dict) or not raw_features:
            raise ResearchInputError("FEATURES_MISSING")
        features: dict[str, float] = {}
        for name, v in raw_features.items():
            if not isinstance(name, str) or not name or isinstance(v, bool):
                raise ResearchInputError("INVALID_FEATURE")
            try:
                number = float(v)
            except (ValueError, TypeError) as exc:
                raise ResearchInputError("NON_NUMERIC_FEATURE") from exc
            if not isfinite(number):
                raise ResearchInputError("NON_FINITE_FEATURE")
            features[name] = number
        if feature_names is None:
            feature_names = set(features)
        if set(features) != feature_names:
            raise ResearchInputError("INCONSISTENT_FEATURE_SCHEMA")
        expected_hash = sha256(json.dumps(raw_features, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode("utf-8")).hexdigest()
        if value.get("feature_snapshot_sha256") != expected_hash:
            raise ResearchInputError("FEATURE_SNAPSHOT_HASH_MISMATCH")
        event_key = f'{value["sport"]}:{value["official_event_id"]}'
        if event_key in event_times:
            if event_times[event_key] != start:
                raise ResearchInputError("EVENT_START_CONFLICT")
            raise ResearchInputError("DUPLICATE_EVENT_OUTCOME_RECORD")
        event_times[event_key] = start
        incumbent = None
        if "incumbent_probability" in value:
            incumbent = _prob(value["incumbent_probability"])
            incumbent_time = _dt(value.get("incumbent_model_timestamp_utc"))
            if incumbent_time >= start:
                raise ResearchInputError("INCUMBENT_NOT_PREGAME")
            if captured > incumbent_time:
                raise ResearchInputError("CANDIDATE_EVIDENCE_LATER_THAN_INCUMBENT")
        rows.append(Row(pid, value["official_event_id"], value["sport"], value["market"],
                        start, asof, available, value["outcome"], features, incumbent))
    if len({(r.sport, r.market) for r in rows}) != 1:
        raise ResearchInputError("MIXED_SPECIALIST_OR_MARKET_COHORT")
    return sorted(rows, key=lambda r: (r.event_start, r.prediction_id))


def chronological_partition(rows: Sequence[Row], train_end: str, validation_end: str):
    train_cut = _dt(train_end)
    val_cut = _dt(validation_end)
    if train_cut >= val_cut:
        raise ResearchInputError("INVALID_SPLIT_BOUNDARIES")
    train = [r for r in rows if r.event_start <= train_cut]
    val = [r for r in rows if train_cut < r.event_start <= val_cut]
    hold = [r for r in rows if r.event_start > val_cut]
    if min(len(train), len(val), len(hold)) < 2:
        raise ResearchInputError("INSUFFICIENT_CHRONOLOGICAL_COHORT")
    if {r.outcome for r in train} != {0, 1}:
        raise ResearchInputError("TRAINING_SINGLE_CLASS")
    # Outcome labels must have existed before the next cohort's FIRST feature snapshot.
    if max(r.outcome_available for r in train) >= min(r.feature_asof for r in val):
        raise ResearchInputError("TRAIN_LABEL_LEAKAGE")
    if max(r.outcome_available for r in val) >= min(r.feature_asof for r in hold):
        raise ResearchInputError("VALIDATION_LABEL_LEAKAGE")
    return train, val, hold


def metric_packet(probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> dict[str, float | int]:
    if len(probabilities) != len(outcomes) or len(probabilities) == 0 or bins < 2:
        raise ResearchInputError("INVALID_METRIC_COHORT")
    ps = [_prob(p) for p in probabilities]
    if any(y not in (0, 1) or isinstance(y, bool) for y in outcomes):
        raise ResearchInputError("INVALID_BINARY_OUTCOME")
    n = len(ps)
    brier = sum((p - y) ** 2 for p, y in zip(ps, outcomes)) / n
    eps = 1e-12
    loss = -sum(y * log(max(p, eps)) + (1 - y) * log(max(1 - p, eps))
                for p, y in zip(ps, outcomes)) / n
    ece = 0.0
    for b in range(bins):
        idx = [i for i, p in enumerate(ps) if min(int(p * bins), bins - 1) == b]
        if idx:
            ece += len(idx) / n * abs(sum(ps[i] for i in idx) / len(idx) -
                                      sum(outcomes[i] for i in idx) / len(idx))
    mean_p = sum(ps) / n
    hit = sum(outcomes) / n
    return {"n": n, "brier": brier, "log_loss": loss, "ece": ece,
            "calibration_bias": mean_p - hit, "observed_rate": hit, "mean_probability": mean_p}


def _vectors(rows: Sequence[Row], names: Sequence[str]) -> list[list[float]]:
    return [[r.features[name] for name in names] for r in rows]


def fit_candidate(train: Sequence[Row], val: Sequence[Row], hold: Sequence[Row], *,
                  backend: str, optuna_trials: int, time_limit: int) -> list[float]:
    names = sorted(train[0].features)
    x_train, x_val, x_hold = (_vectors(cohort, names) for cohort in (train, val, hold))
    y_train, y_val = ([r.outcome for r in cohort] for cohort in (train, val))
    if backend == "catboost":
        try:
            from catboost import CatBoostClassifier
        except ImportError as exc:
            raise ResearchInputError("CATBOOST_RESEARCH_DEPENDENCY_MISSING") from exc
        params = {"iterations": 300, "depth": 4, "learning_rate": 0.04,
                  "loss_function": "Logloss", "random_seed": 17, "verbose": False,
                  "thread_count": 1, "allow_writing_files": False}
        if optuna_trials:
            try:
                import optuna
            except ImportError as exc:
                raise ResearchInputError("OPTUNA_RESEARCH_DEPENDENCY_MISSING") from exc
            def objective(trial):
                trial_params = {**params, "depth": trial.suggest_int("depth", 3, 7),
                                "learning_rate": trial.suggest_float("learning_rate", 0.015, 0.2, log=True)}
                candidate = CatBoostClassifier(**trial_params)
                candidate.fit(x_train, y_train, eval_set=(x_val, y_val), early_stopping_rounds=25)
                probs = candidate.predict_proba(x_val)[:, 1]
                return metric_packet(probs, y_val)["log_loss"]
            study = optuna.create_study(direction="minimize",
                                        sampler=optuna.samplers.TPESampler(seed=17))
            study.optimize(objective, n_trials=optuna_trials)
            params.update(study.best_params)
        model = CatBoostClassifier(**params)
        model.fit(x_train, y_train, eval_set=(x_val, y_val), early_stopping_rounds=25)
        return [float(x) for x in model.predict_proba(x_hold)[:, 1]]
    if backend == "autogluon":
        if optuna_trials:
            raise ResearchInputError("OPTUNA_ONLY_SUPPORTED_FOR_CATBOOST")
        try:
            import pandas as pd
            from autogluon.tabular import TabularPredictor
        except ImportError as exc:
            raise ResearchInputError("AUTOGLUON_RESEARCH_DEPENDENCY_MISSING") from exc
        from tempfile import TemporaryDirectory
        def frame(data):
            return pd.DataFrame([{**r.features, "outcome": r.outcome} for r in data])
        with TemporaryDirectory(prefix="wow-v17-research-") as workdir:
            model = TabularPredictor(label="outcome", problem_type="binary",
                                     eval_metric="log_loss", path=workdir)
            model.fit(frame(train), tuning_data=frame(val), presets="medium_quality",
                      time_limit=time_limit)
            preds = model.predict_proba(frame(hold).drop(columns=["outcome"]))
            return [float(x) for x in preds[1].tolist()]
    raise ResearchInputError("BACKEND_NOT_RECOGNIZED")


def evaluate_experiment(raw: Sequence[Mapping[str, Any]], *, train_end: str,
                        validation_end: str, backend: str = "catboost", optuna_trials: int = 0,
                        time_limit: int = 180, fit_fn=None) -> dict[str, Any]:
    if optuna_trials < 0 or optuna_trials > 20 or time_limit < 1:
        raise ResearchInputError("INVALID_RESEARCH_BUDGET")
    rows = parse_rows(raw)
    train, val, hold = chronological_partition(rows, train_end, validation_end)
    trainer = fit_fn if fit_fn is not None else fit_candidate
    probabilities = trainer(train, val, hold, backend=backend, optuna_trials=optuna_trials,
                            time_limit=time_limit)
    candidate_metrics = metric_packet(probabilities, [r.outcome for r in hold])
    baseline = None
    if all(r.incumbent_probability is not None for r in hold):
        baseline = metric_packet([r.incumbent_probability for r in hold], [r.outcome for r in hold])
    fingerprint = sha256(json.dumps(raw, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"status": "SHADOW_EVALUATED", "data_sha256": fingerprint,
            "cohort": {"sport": rows[0].sport, "market": rows[0].market,
                       "train_n": len(train), "validation_n": len(val), "holdout_n": len(hold)},
            "backend": backend, "candidate": candidate_metrics, "incumbent": baseline,
            "comparison_status": "PAIRED_HOLDOUT" if baseline else "NO_VERIFIED_INCUMBENT_BASELINE",
            "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
            "production_mutation_allowed": False, "can_execute": CAN_EXECUTE,
            "terminal_authority": TERMINAL_AUTHORITY}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Trusted immutable pregame JSON list")
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--backend", choices=("catboost", "autogluon"), default="catboost")
    parser.add_argument("--optuna-trials", type=int, default=0)
    parser.add_argument("--time-limit", type=int, default=180)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mlflow-local", action="store_true")
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    report = evaluate_experiment(raw, train_end=args.train_end,
                                 validation_end=args.validation_end, backend=args.backend,
                                 optuna_trials=args.optuna_trials, time_limit=args.time_limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.mlflow_local:
        try:
            import mlflow
        except ImportError as exc:
            raise ResearchInputError("MLFLOW_RESEARCH_DEPENDENCY_MISSING") from exc
        mlflow.set_tracking_uri((args.output.parent / "mlruns").resolve().as_uri())
        mlflow.set_experiment("WOW_V17_NON_SERVING_FREE_FORECASTING")
        with mlflow.start_run():
            mlflow.log_params({"backend": args.backend, "data_sha256": report["data_sha256"],
                               **report["cohort"]})
            mlflow.log_metrics({"candidate_" + k: v for k, v in report["candidate"].items()})
            if report["incumbent"]:
                mlflow.log_metrics({"incumbent_" + k: v for k, v in report["incumbent"].items()})
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
