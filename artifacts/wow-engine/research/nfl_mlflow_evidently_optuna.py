"""Minimal reproducible offline experiment for the NFL chronological challenger.

MLflow (sqlite backend) + Evidently calibration report + governed Optuna study.
RESEARCH ONLY: can_execute=false, no promotion, no probability publication.

Governance of the tuning study (Class C research, challenger only):
  * Optuna sees ONLY train (<= TUNE_TRAIN_END) and validation (TUNE_VAL) seasons.
  * Calibration season, holdout and forward cohorts are never used by the objective.
  * The chosen params are frozen BEFORE the holdout/forward evaluation, which runs once.
  * Nothing is promoted automatically; status stays CANDIDATE_ONLY_NOT_PROMOTABLE.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess
from pathlib import Path
import numpy as np, pandas as pd
import nfl_ml_chronological_challenger as m

CAN_EXECUTE = False
TUNE_TRAIN_END, TUNE_VAL = 2020, 2021          # tuning window (train/validation only)
FORWARD_SEASON = 2026
N_TRIALS, SEED = 20, 17


class ReproError(Exception):
    pass


def provenance() -> dict:
    root = Path(__file__).resolve().parent
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    except Exception as e:
        raise ReproError("CODE_SHA_UNAVAILABLE") from e
    if dirty:
        raise ReproError("CODE_TREE_DIRTY_NOT_REPRODUCIBLE")
    return {"code_sha": sha, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _fit_eval(f, tr_mask, ev_mask, l2, cal_mask=None):
    tr, ev = f[tr_mask], f[ev_mask]
    mu, sd = tr[m.FEATURES].mean().values, tr[m.FEATURES].std().values
    sd = np.where(np.nan_to_num(sd) > 0, sd, 1.0)
    z = lambda x: (x[m.FEATURES].values - mu) / sd
    b = m.fit_logit(z(tr), tr.y.values, l2=l2)
    p = m.predict(b, z(ev))
    if cal_mask is not None:
        cal = f[cal_mask]
        pc = m.predict(b, z(cal))
        platt = m.fit_logit(m.logit(pc)[:, None], cal.y.values, l2=0.0)
        p = m.predict(platt, m.logit(p)[:, None])
        return p, ev.y.values, dict(mu=mu.tolist(), sd=sd.tolist(), coef=b.tolist(), platt=platt.tolist())
    return p, ev.y.values, None


def tune(d, n_trials=N_TRIALS, seed=SEED):
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    cache = {}

    def feats(hl):
        if hl not in cache:
            old = m.HALF_LIFE_DAYS
            m.HALF_LIFE_DAYS = hl
            try:
                cache[hl] = m.build_features(d)
            finally:
                m.HALF_LIFE_DAYS = old
        return cache[hl]

    def objective(trial):
        hl = trial.suggest_categorical("half_life_days", [112, 168, 224, 336, 448])
        l2 = trial.suggest_float("l2", 0.01, 100.0, log=True)
        f = feats(hl)
        f = f[f.y.notna()]
        p, y, _ = _fit_eval(f, f.season <= TUNE_TRAIN_END, f.season == TUNE_VAL, l2)
        return m.metrics(p, y)["log_loss"]

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials)
    return study, feats


def run(games_csv: Path, out_dir: Path, expected_sha256: str, mlflow_uri: str):
    prov = provenance()
    d = m.load_games(games_csv, expected_sha256)
    study, feats = tune(d)
    best = study.best_params
    f = feats(best["half_life_days"]); f = f[f.y.notna()]

    # frozen params -> single evaluation on untouched holdout + forward cohorts
    tr = f.season <= m.TRAIN_END; cal = f.season == m.CAL_SEASON
    ho = (f.season >= m.HOLDOUT[0]) & (f.season <= m.HOLDOUT[1]); fw = f.season == FORWARD_SEASON
    if not (f[tr].gameday.max() < f[cal].gameday.min() <= f[cal].gameday.max() < f[ho].gameday.min()):
        raise m.EvidenceError("NFL_CHRONOLOGICAL_OVERLAP")
    p_ho, y_ho, art = _fit_eval(f, tr, ho, best["l2"], cal)
    p_fw, y_fw, _ = _fit_eval(f, tr, fw, best["l2"], cal)
    res = dict(
        challenger_id=m.CHALLENGER_ID + "_OPTUNA_L2_HL", frozen_params=best, n_trials=len(study.trials),
        tuning_window=dict(train_end=TUNE_TRAIN_END, validation=TUNE_VAL, best_validation_log_loss=study.best_value),
        holdout_evaluations_run=1,
        n=dict(train=int(tr.sum()), cal=int(cal.sum()), holdout=int(ho.sum()), forward=int(fw.sum())),
        holdout=m.metrics(p_ho, y_ho), forward=m.metrics(p_fw, y_fw) if len(y_fw) else None,
        reliability_holdout=m.reliability(p_ho, y_ho),
        cohort_bound_semantics="cohort Wilson bound of observed hit rate; NOT a per-selection calibrated_lower_bound",
        calibrated_lower_bound=None, rank_eligible=False, can_execute=False,
        status="CANDIDATE_ONLY_NOT_PROMOTABLE",
        source_sha256=m.sha256_file(games_csv),
        artifact_checksum=hashlib.sha256(json.dumps(art, sort_keys=True).encode()).hexdigest(), **prov)
    default_ho = m.run(games_csv, out_dir / "default_ref", expected_sha256)  # untuned reference (already-seen holdout)
    res["untuned_reference_holdout"] = default_ho["holdout"]["challenger_platt"]
    res["note"] = ("Holdout 2023-2025 was already viewed for the untuned reference in a prior receipt, so it is not pristine; "
                   "the 2026 forward cohort is the least-contaminated evidence. Neither authorizes promotion.")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(res, indent=2, default=float))

    # Evidently calibration/classification report on the holdout
    from evidently import Report, Dataset, DataDefinition, BinaryClassification
    from evidently.presets import ClassificationPreset
    df = pd.DataFrame({"target": y_ho.astype(int), "prediction": p_ho})
    dd = DataDefinition(classification=[BinaryClassification(target="target", prediction_probas="prediction", pos_label=1)])
    ds = Dataset.from_pandas(df, data_definition=dd)
    rep = Report([ClassificationPreset(probas_threshold=0.5)]).run(ds, None)
    rep.save_html(str(out_dir / "evidently_holdout_report.html"))

    import mlflow
    mlflow.set_tracking_uri(mlflow_uri); mlflow.set_experiment("wow_nfl_ml_restoration_tuned")
    with mlflow.start_run(run_name=res["challenger_id"]) as r:
        mlflow.log_params({**best, "code_sha": prov["code_sha"], "dataset_sha256": res["source_sha256"],
                           "artifact_checksum": res["artifact_checksum"], "seed": SEED, "n_trials": N_TRIALS})
        for k, v in res["holdout"].items(): mlflow.log_metric(f"holdout_{k}", v)
        if res["forward"]:
            for k, v in res["forward"].items(): mlflow.log_metric(f"forward_{k}", v)
        mlflow.log_artifacts(str(out_dir))
        res["mlflow_run_id"] = r.info.run_id
    (out_dir / "report.json").write_text(json.dumps(res, indent=2, default=float))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("games_csv"); ap.add_argument("out_dir"); ap.add_argument("--expected-sha256", required=True)
    ap.add_argument("--mlflow-uri", required=True)
    a = ap.parse_args()
    r = run(Path(a.games_csv), Path(a.out_dir), a.expected_sha256, a.mlflow_uri)
    print(json.dumps({k: r[k] for k in ("frozen_params", "tuning_window", "holdout", "forward", "untuned_reference_holdout", "mlflow_run_id", "status")}, indent=1, default=float))
