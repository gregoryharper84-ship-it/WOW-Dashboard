"""Research-only NFL moneyline chronological challenger (restoration workstream #2).

Reproducible candidate pipeline: nflverse schedule/results (CC BY 4.0) ->
strictly-prior decayed team form -> logistic fit -> Platt calibration -> identical
chronological holdout comparison. Candidate training does NOT authorize promotion.
It cannot publish a probability, certify, promote, or execute (can_execute=false).
Requires the isolated research stack (numpy, pandas; mlflow optional for lineage).
"""
from __future__ import annotations

import argparse, hashlib, json, math, subprocess, sys
from pathlib import Path

import numpy as np
import pandas as pd

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CHALLENGER_ID = "NFL_ML_DECAYFORM_LOGREG_PLATT_CHRONO_V1"
ROLLING_GAMES, HALF_LIFE_DAYS, MIN_HISTORY = 8, 224.0, 8
TRAIN_END, CAL_SEASON, HOLDOUT = 2021, 2022, (2023, 2025)
FEATURES = ["margin_diff", "win_diff", "rest_diff"]


class EvidenceError(ValueError):
    """Typed, fail-closed input-evidence failure."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_games(path: Path, expected_sha256: str | None = None) -> pd.DataFrame:
    if not Path(path).exists():
        raise EvidenceError("NFL_SOURCE_MISSING")
    if expected_sha256 and sha256_file(path) != expected_sha256:
        raise EvidenceError("NFL_SOURCE_CHECKSUM_MISMATCH")
    d = pd.read_csv(path)
    need = {"game_id", "season", "gameday", "home_team", "away_team", "home_score", "away_score", "home_rest", "away_rest"}
    if need - set(d.columns):
        raise EvidenceError("NFL_SOURCE_SCHEMA_MISMATCH")
    d["gameday"] = pd.to_datetime(d["gameday"], errors="raise")
    if d["game_id"].duplicated().any():
        raise EvidenceError("NFL_SOURCE_DUPLICATE_EVENT_IDENTITY")
    return d.sort_values(["gameday", "game_id"]).reset_index(drop=True)


def _form(hist, now):
    h = hist[-ROLLING_GAMES:]
    if len(h) < MIN_HISTORY:
        return None
    w = np.array([0.5 ** ((now - t).days / HALF_LIFE_DAYS) for t, _, _ in h])
    m = np.array([x for _, x, _ in h]); win = np.array([y for _, _, y in h])
    return float((w * m).sum() / w.sum()), float((w * win).sum() / w.sum())


def build_features(d: pd.DataFrame) -> pd.DataFrame:
    """Features use only games with gameday strictly before the target gameday."""
    hist: dict[str, list] = {}
    rows = []
    for day, g in d.groupby("gameday", sort=True):
        pending = []
        for r in g.itertuples():
            done = pd.notna(r.home_score) and pd.notna(r.away_score)
            hf, af = _form(hist.get(r.home_team, []), day), _form(hist.get(r.away_team, []), day)
            if hf and af:
                rows.append(dict(game_id=r.game_id, season=r.season, gameday=day, home=r.home_team, away=r.away_team,
                                 margin_diff=hf[0] - af[0], win_diff=hf[1] - af[1],
                                 rest_diff=float(np.clip((r.home_rest or 7) - (r.away_rest or 7), -7, 7)),
                                 y=(float(r.home_score > r.away_score) if done and r.home_score != r.away_score else np.nan),
                                 feature_cutoff=day))
            if done:
                pending.append(r)
        for r in pending:  # update only after every same-day target was featurized
            mg = float(r.home_score - r.away_score)
            hist.setdefault(r.home_team, []).append((day, mg, float(mg > 0)))
            hist.setdefault(r.away_team, []).append((day, -mg, float(mg < 0)))
    f = pd.DataFrame(rows)
    assert (f["feature_cutoff"] <= f["gameday"]).all()
    return f


def fit_logit(X, y, l2=1.0, iters=50):
    Xb = np.c_[np.ones(len(X)), X]; b = np.zeros(Xb.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ b)); W = p * (1 - p)
        H = Xb.T @ (Xb * W[:, None]) + l2 * np.eye(len(b)); H[0, 0] -= l2
        g = Xb.T @ (p - y) + l2 * np.r_[0, b[1:]]
        step = np.linalg.solve(H, g); b -= step
        if np.abs(step).max() < 1e-9: break
    return b


def predict(b, X): return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), X] @ b)))
def logit(p): p = np.clip(p, 1e-6, 1 - 1e-6); return np.log(p / (1 - p))


def wilson_lower(k, n, z=1.645):
    if n <= 0: return float("nan")
    p = k / n; d = 1 + z * z / n
    c = p + z * z / (2 * n); a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d


def metrics(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return dict(n=int(len(y)), brier=float(np.mean((p - y) ** 2)),
                log_loss=float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
                calibration_bias=float(np.mean(p) - np.mean(y)), accuracy=float(np.mean((p > .5) == (y > .5))))


def reliability(p, y, edges=(0, .4, .5, .6, .65, .7, .8, 1.0001)):
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum():
            k = int(y[m].sum()); out.append(dict(bin=f"[{lo},{min(hi,1)})", n=int(m.sum()), forecast=float(p[m].mean()),
                                                observed=k / int(m.sum()), wilson90_lower=wilson_lower(k, int(m.sum()))))
    return out


def run(games_csv: Path, out_dir: Path, expected_sha256: str | None = None, mlflow_uri: str | None = None):
    d = load_games(games_csv, expected_sha256); f = build_features(d)
    f = f[f["y"].notna()].copy()
    tr = f[f.season <= TRAIN_END]; cal = f[f.season == CAL_SEASON]
    ho = f[(f.season >= HOLDOUT[0]) & (f.season <= HOLDOUT[1])]
    if min(len(tr), len(cal), len(ho)) == 0: raise EvidenceError("NFL_CHRONOLOGICAL_SPLIT_EMPTY")
    if not (tr.gameday.max() < cal.gameday.min() <= cal.gameday.max() < ho.gameday.min()): raise EvidenceError("NFL_CHRONOLOGICAL_OVERLAP")
    mu, sd = tr[FEATURES].mean().values, tr[FEATURES].std().values
    sd = np.where(np.nan_to_num(sd) > 0, sd, 1.0)
    z = lambda x: (x[FEATURES].values - mu) / sd
    b = fit_logit(z(tr), tr.y.values)
    pc = predict(b, z(cal)); platt = fit_logit(logit(pc)[:, None], cal.y.values, l2=0.0)
    cal_fn = lambda p: predict(platt, logit(p)[:, None])
    p_raw = predict(b, z(ho)); p_cal = cal_fn(p_raw); yh = ho.y.values
    base = np.full(len(ho), tr.y.mean())
    res = dict(challenger_id=CHALLENGER_ID, splits=dict(train_end_season=TRAIN_END, calibration_season=CAL_SEASON, holdout=list(HOLDOUT),
               n_train=len(tr), n_cal=len(cal), n_holdout=len(ho), train_last=str(tr.gameday.max().date()),
               cal_first=str(cal.gameday.min().date()), holdout_first=str(ho.gameday.min().date())),
               holdout=dict(baseline_home_rate=metrics(base, yh), logit_uncalibrated=metrics(p_raw, yh), challenger_platt=metrics(p_cal, yh)),
               reliability_challenger=reliability(p_cal, yh))
    pick_ok_raw, pick_ok_cal = (p_raw > .5) == (yh > .5), (p_cal > .5) == (yh > .5)
    hi = (np.maximum(p_cal, 1 - p_cal) >= .65)
    res["counterexamples"] = dict(
        previously_correct_harmed=int((pick_ok_raw & ~pick_ok_cal).sum()), previously_wrong_fixed=int((~pick_ok_raw & pick_ok_cal).sum()),
        cohort_ge_65_n=int(hi.sum()), cohort_ge_65_wrong=int((hi & ~pick_ok_cal).sum()),
        cohort_ge_65_observed=float(pick_ok_cal[hi].mean()) if hi.any() else None,
        cohort_ge_65_wilson90_lower=wilson_lower(int(pick_ok_cal[hi].sum()), int(hi.sum())) if hi.any() else None,
        worst_misses=[dict(game_id=g, p_home=round(float(p), 3), home_won=int(y)) for g, p, y in
                      sorted(zip(ho.game_id, p_cal, yh), key=lambda t: -abs(t[1] - t[2]))[:5]])
    art = dict(model_family=CHALLENGER_ID, features=FEATURES, mu=mu.tolist(), sd=sd.tolist(), coef=b.tolist(), platt=platt.tolist())
    blob = json.dumps(art, sort_keys=True).encode(); res["artifact_checksum"] = hashlib.sha256(blob).hexdigest()
    res["training_dataset_hash"] = hashlib.sha256(pd.util.hash_pandas_object(tr, index=False).values.tobytes()).hexdigest()
    res["source_sha256"] = sha256_file(games_csv)
    try: res["code_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: res["code_sha"] = "UNKNOWN"
    res["status"] = "CANDIDATE_ONLY_NOT_PROMOTABLE"; res["can_execute"] = False
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "artifact.json").write_bytes(blob); (out_dir / "report.json").write_text(json.dumps(res, indent=2, default=float))
    if mlflow_uri:
        import mlflow
        mlflow.set_tracking_uri(mlflow_uri); mlflow.set_experiment("wow_nfl_ml_restoration")
        with mlflow.start_run(run_name=CHALLENGER_ID):
            mlflow.log_params(dict(code_sha=res["code_sha"], dataset_sha=res["source_sha256"], train_hash=res["training_dataset_hash"],
                                   artifact_checksum=res["artifact_checksum"], half_life=HALF_LIFE_DAYS))
            for k, v in res["holdout"]["challenger_platt"].items(): mlflow.log_metric(f"holdout_{k}", v)
            mlflow.log_artifacts(str(out_dir))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("games_csv"); ap.add_argument("out_dir")
    ap.add_argument("--expected-sha256"); ap.add_argument("--mlflow-uri")
    a = ap.parse_args(); r = run(Path(a.games_csv), Path(a.out_dir), a.expected_sha256, a.mlflow_uri)
    print(json.dumps(dict(splits=r["splits"], holdout=r["holdout"], counterexamples=r["counterexamples"], status=r["status"]), indent=1, default=float))
