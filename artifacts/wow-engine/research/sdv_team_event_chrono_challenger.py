"""Research-only NHL / NCAAF moneyline chronological challengers (restoration workstreams #2-6).

Source adapters for SportsDataverse release assets. Rights: the sportsdataverse-data release store
declares CC BY 4.0 (its DESCRIPTION file); the MIT license belongs to separate wrapper software, not
the data. Rights of the upstream ESPN / NHL-origin data are UNVERIFIED, so output is research-only and
must not be used commercially until a rights review passes (attribution to SportsDataverse required).
  NHL   releases/nhl_schedules/nhl_schedule_<season>.csv
  NCAAF releases/espn_cfb_schedules/cfb_schedule_<season>.csv
Pipeline: final games -> strictly-prior decayed form + rest -> logistic -> Platt ->
identical chronological holdout, plus a separate forward season. Candidate only: it cannot
publish, certify, promote, or execute (can_execute=false). Math/guards come from chrono_common.py (self-contained; no cross-PR module dependency).
"""
from __future__ import annotations

import argparse, glob, hashlib, json, subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from chrono_common import (COHORT_BOUND_SEMANTICS, EvidenceError, code_provenance, fit_logit, logit, metrics, predict,
                           reliability, sha256_file, wilson_lower)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

CONFIG = {
    "NHL": dict(id="NHL_ML_DECAYFORM_LOGREG_PLATT_CHRONO_V1", rolling=20, half_life=60.0, min_hist=10, max_age=365, train_end=2017,
                cal=2018, holdout=(2024, 2025), forward=2026, extra=[]),
    "NCAAF": dict(id="NCAAF_ML_DECAYFORM_LOGREG_PLATT_CHRONO_V1", rolling=12, half_life=300.0, min_hist=5, max_age=365, train_end=2021,
                  cal=2022, holdout=(2023, 2025), forward=2026, extra=["neutral"]),
}
NEED = {
    "NHL": {"game_id", "season", "game_type", "game_date", "home_team_abbr", "away_team_abbr", "home_score", "away_score", "game_state"},
    "NCAAF": {"game_id", "season", "game_date", "home_id", "away_id", "home_score", "away_score", "status", "neutral_site"},
}


def load_sport(sport: str, csv_paths: list[Path]) -> pd.DataFrame:
    """Normalize to game_id, season, date, home, away, hs, as, neutral; final games only."""
    if sport not in CONFIG: raise EvidenceError("SDV_SPORT_UNSUPPORTED")
    if not csv_paths or any(not Path(p).exists() for p in csv_paths): raise EvidenceError("SDV_SOURCE_MISSING")
    d = pd.concat([pd.read_csv(p, low_memory=False) for p in csv_paths], ignore_index=True)
    if NEED[sport] - set(d.columns): raise EvidenceError("SDV_SOURCE_SCHEMA_MISMATCH")
    if sport == "NHL":
        # Source `season` is inconsistent across files and `PR` rows are preseason: derive the
        # end-year season from the game date and keep regular-season/playoff games only.
        d = d[d.game_state.astype(str).str.upper().isin(["OFF", "FINAL"]) & d.game_type.astype(str).isin(["R", "P"])]
        d = d.rename(columns={"home_team_abbr": "home", "away_team_abbr": "away"}); d["neutral"] = 0.0
    else:
        d = d[d.status.astype(str) == "STATUS_FINAL"]
        d = d.rename(columns={"home_id": "home", "away_id": "away"}); d["neutral"] = d.neutral_site.astype(str).str.lower().eq("true").astype(float)
    d = d.dropna(subset=["home_score", "away_score"]).copy()
    d["game_id"] = d["game_id"].astype(str)
    if d.game_id.duplicated().any(): raise EvidenceError("SDV_SOURCE_DUPLICATE_EVENT_IDENTITY")
    d["date"] = pd.to_datetime(d["game_date"].astype(str).str[:10], errors="coerce")
    if d.date.isna().any(): raise EvidenceError("SDV_SOURCE_UNPARSEABLE_DATE")
    d = d[d.home.astype(str) != d.away.astype(str)]
    if sport == "NHL": d["season"] = d["date"].dt.year + (d["date"].dt.month >= 8).astype(int)
    # Fail closed on placeholder scores: a season whose home or away score never varies is not
    # real outcome data (observed in several SportsDataverse NHL assets). Excluded, never trained.
    bad = sorted(int(x) for x, g in d.groupby("season") if len(g) > 20 and (g.home_score.nunique() == 1 or g.away_score.nunique() == 1))
    d.attrs["excluded_degenerate_seasons"] = bad
    d = d[~d.season.isin(bad)]
    out = d[["game_id", "season", "date", "home", "away", "home_score", "away_score", "neutral"]].sort_values(["date", "game_id"]).reset_index(drop=True)
    out.attrs["excluded_degenerate_seasons"] = bad
    return out


def build_features(d: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    hist: dict = {}; rows = []
    for day, g in d.groupby("date", sort=True):
        done = []
        for r in g.itertuples():
            fresh = lambda t: [x for x in hist.get(t, []) if (day - x[0]).days <= cfg["max_age"]][-cfg["rolling"]:]  # stale history never used
            hh, ah = fresh(r.home), fresh(r.away)
            if len(hh) >= cfg["min_hist"] and len(ah) >= cfg["min_hist"]:
                def form(h):
                    w = np.array([0.5 ** ((day - t).days / cfg["half_life"]) for t, _, _ in h])
                    return (w * np.array([m for _, m, _ in h])).sum() / w.sum(), (w * np.array([x for _, _, x in h])).sum() / w.sum()
                (hm, hw), (am, aw) = form(hh), form(ah)
                rest = lambda h: float(min((day - h[-1][0]).days, 14))
                tie = r.home_score == r.away_score
                rows.append(dict(game_id=r.game_id, season=r.season, date=day, margin_diff=hm - am, win_diff=hw - aw,
                                 rest_diff=rest(hh) - rest(ah), neutral=r.neutral, y=np.nan if tie else float(r.home_score > r.away_score)))
            done.append(r)
        for r in done:
            mg = float(r.home_score - r.away_score)
            hist.setdefault(r.home, []).append((day, mg, float(mg > 0))); hist.setdefault(r.away, []).append((day, -mg, float(mg < 0)))
    return pd.DataFrame(rows)


def run(sport: str, csv_paths: list[Path], out_dir: Path, mlflow_uri: str | None = None) -> dict:
    cfg = CONFIG[sport]; feats = ["margin_diff", "win_diff", "rest_diff", *cfg["extra"]]
    raw = load_sport(sport, [Path(p) for p in csv_paths])
    f = build_features(raw, cfg); f = f[f.y.notna()]
    tr, cal = f[f.season <= cfg["train_end"]], f[f.season == cfg["cal"]]
    ho = f[(f.season >= cfg["holdout"][0]) & (f.season <= cfg["holdout"][1])]; fw = f[f.season == cfg["forward"]]
    if min(len(tr), len(cal), len(ho)) == 0: raise EvidenceError("SDV_CHRONOLOGICAL_SPLIT_EMPTY")
    if not (tr.date.max() < cal.date.min() and cal.date.max() < ho.date.min()): raise EvidenceError("SDV_CHRONOLOGICAL_OVERLAP")
    mu, sd = tr[feats].mean().values, tr[feats].std().values; sd = np.where(np.nan_to_num(sd) > 0, sd, 1.0)
    z = lambda x: (x[feats].values - mu) / sd
    b = fit_logit(z(tr), tr.y.values); pl = fit_logit(logit(predict(b, z(cal)))[:, None], cal.y.values, l2=0.0)
    score = lambda x: predict(pl, logit(predict(b, z(x)))[:, None])
    res = dict(sport=sport, challenger_id=cfg["id"], features=feats, excluded_degenerate_seasons=raw.attrs.get("excluded_degenerate_seasons", []),
               splits=dict(n_train=len(tr), n_cal=len(cal), n_holdout=len(ho), n_forward=len(fw), train_last=str(tr.date.max().date()),
                           cal_first=str(cal.date.min().date()), holdout_first=str(ho.date.min().date())))
    for name, x in (("holdout", ho), ("forward", fw)):
        if len(x) == 0: continue
        p, y = score(x), x.y.values; hi = np.maximum(p, 1 - p) >= .65; ok = (p > .5) == (y > .5)
        res[name] = dict(baseline_home_rate=metrics(np.full(len(x), tr.y.mean()), y), challenger_platt=metrics(p, y),
                         reliability=reliability(p, y), cohort_ge_65=dict(n=int(hi.sum()), observed_hit_rate=float(ok[hi].mean()) if hi.any() else None,
                         cohort_hit_rate_wilson90_lower=wilson_lower(int(ok[hi].sum()), int(hi.sum())) if hi.any() else None,
                         semantics=COHORT_BOUND_SEMANTICS))
    art = dict(model_family=cfg["id"], features=feats, mu=mu.tolist(), sd=sd.tolist(), coef=b.tolist(), platt=pl.tolist())
    blob = json.dumps(art, sort_keys=True).encode()
    res["artifact_checksum"] = hashlib.sha256(blob).hexdigest()
    res["source_sha256"] = {Path(p).name: sha256_file(Path(p)) for p in csv_paths}
    res["training_dataset_hash"] = hashlib.sha256(pd.util.hash_pandas_object(tr, index=False).values.tobytes()).hexdigest()
    res.update(code_provenance())
    res["source_rights"] = dict(dataset="sportsdataverse-data releases", license="CC BY 4.0", attribution_required=True,
                                upstream_rights="UNVERIFIED", commercial_use="NOT_AUTHORIZED_PENDING_RIGHTS_REVIEW")
    res["official_score_crosswalk"] = "NOT_PERFORMED"  # official NHL/ESPN final scores not reconciled to every included game
    res["calibrated_lower_bound"] = None; res["rank_eligible"] = False; res["incumbent_comparison"] = "NONE_EXISTS_HOME_RATE_BASELINE_ONLY"
    res["status"] = "CANDIDATE_ONLY_NOT_PROMOTABLE"; res["can_execute"] = False
    out_dir.mkdir(parents=True, exist_ok=True); (out_dir / "artifact.json").write_bytes(blob); (out_dir / "report.json").write_text(json.dumps(res, indent=2, default=float))
    if mlflow_uri:
        import mlflow
        mlflow.set_tracking_uri(mlflow_uri); mlflow.set_experiment(f"wow_{sport.lower()}_ml_restoration")
        with mlflow.start_run(run_name=cfg["id"]):
            mlflow.log_params(dict(code_sha=res["code_sha"], artifact_checksum=res["artifact_checksum"], train_hash=res["training_dataset_hash"]))
            for k, v in res["holdout"]["challenger_platt"].items(): mlflow.log_metric(f"holdout_{k}", v)
            mlflow.log_artifacts(str(out_dir))
            res["mlflow_run_id"] = mlflow.active_run().info.run_id
        (out_dir / "report.json").write_text(json.dumps(res, indent=2, default=float))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("sport", choices=list(CONFIG)); ap.add_argument("glob"); ap.add_argument("out_dir")
    ap.add_argument("--mlflow-uri"); a = ap.parse_args()
    r = run(a.sport, [Path(p) for p in sorted(glob.glob(a.glob))], Path(a.out_dir), a.mlflow_uri)
    print(json.dumps({k: r[k] for k in ("sport", "excluded_degenerate_seasons", "splits", "holdout", "forward", "status") if k in r}, indent=1, default=float))
