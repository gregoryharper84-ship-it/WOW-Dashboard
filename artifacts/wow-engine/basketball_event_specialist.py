"""Independent NBA/WNBA historical acquisition + fitted specialist trainer.

Governance invariants:
- ESPN public data supplies schedules/results only; it is never a probability source.
- NBA and WNBA use separate persistence tables, feature rows, calibrators, and artifacts.
- Training writes CANDIDATE artifacts only. Promotion/certification is a separate governed step.
- can_execute is always false and probability_publishable is always false here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from supabase import create_client

SPORTS = {
    "NBA": {
        "league": "nba",
        "source_table": "wow_nba_source_snapshots",
        "games_table": "wow_nba_training_games",
        "features_table": "wow_nba_pregame_feature_rows",
        "artifacts_table": "wow_nba_event_fitted_model_artifacts",
        "feature_schema": "NBA_EVENT_FEATURES_V1",
        "provider_identity": "WOW_NBA_EVENT_FITTED_MODEL_V1",
        "model_family": "NBA_PREGAME_ELO_REST_MARGIN_LOGIT_V1",
    },
    "WNBA": {
        "league": "wnba",
        "source_table": "wow_wnba_source_snapshots",
        "games_table": "wow_wnba_training_games",
        "features_table": "wow_wnba_pregame_feature_rows",
        "artifacts_table": "wow_wnba_event_fitted_model_artifacts",
        "feature_schema": "WNBA_EVENT_FEATURES_V1",
        "provider_identity": "WOW_WNBA_EVENT_FITTED_MODEL_V1",
        "model_family": "WNBA_PREGAME_ELO_REST_MARGIN_LOGIT_V1",
    },
}

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball"
FEATURE_NAMES = ("elo_diff", "rest_diff", "rolling_margin_diff")


def _sha(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _client():
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def _fetch_json(url: str) -> dict[str, Any]:
    req = Request(url, headers={"User-Agent": "WOW-V17-basketball-specialist/1.0"})
    with urlopen(req, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def scoreboard_url(sport: str, start: date, end: date) -> str:
    cfg = SPORTS[sport]
    params = urlencode({"dates": f"{start:%Y%m%d}-{end:%Y%m%d}", "limit": 2000})
    return f"{ESPN_BASE}/{cfg['league']}/scoreboard?{params}"


def _parse_completed_games(payload: dict[str, Any], snapshot_id: str, season: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        status = ((comp.get("status") or {}).get("type") or {})
        if not status.get("completed"):
            continue
        teams: dict[str, dict[str, Any]] = {}
        for competitor in comp.get("competitors") or []:
            side = competitor.get("homeAway")
            if side in ("home", "away"):
                teams[side] = competitor
        if set(teams) != {"home", "away"}:
            continue
        try:
            home_score = int(float(teams["home"].get("score")))
            away_score = int(float(teams["away"].get("score")))
        except (TypeError, ValueError):
            continue
        # NBA/WNBA full-game winner markets cannot settle as a tie.
        if home_score == away_score:
            continue
        home_team = ((teams["home"].get("team") or {}).get("abbreviation") or "").strip().upper()
        away_team = ((teams["away"].get("team") or {}).get("abbreviation") or "").strip().upper()
        event_id = str(event.get("id") or "").strip()
        start_time = event.get("date")
        if not event_id or not home_team or not away_team or not start_time:
            continue
        identity = {
            "event_id": event_id,
            "season": season,
            "start_time": start_time,
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
        }
        rows.append({
            **identity,
            "home_win": home_score > away_score,
            "source_snapshot_id": snapshot_id,
            "row_inputs_hash": _sha(identity),
            "probability_publishable": False,
            "can_execute": False,
        })
    return rows


def acquire_range(sport: str, season: int, start: date, end: date, *, client=None) -> dict[str, Any]:
    sport = sport.upper()
    cfg = SPORTS[sport]
    client = client or _client()
    url = scoreboard_url(sport, start, end)
    payload = _fetch_json(url)
    payload_hash = _sha(payload)
    source_row = {
        "season": season,
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "source_url": url,
        "content_sha256": payload_hash,
        "event_count": len(payload.get("events") or []),
        "raw_payload": payload,
        "source_status": "CAPTURED" if payload.get("events") else "CAPTURED_EMPTY",
        "probability_publishable": False,
        "can_execute": False,
    }
    inserted = client.table(cfg["source_table"]).upsert(
        source_row,
        on_conflict="season,range_start,range_end,content_sha256",
    ).execute().data
    if not inserted:
        existing = client.table(cfg["source_table"]).select("snapshot_id").eq("season", season).eq("range_start", start.isoformat()).eq("range_end", end.isoformat()).eq("content_sha256", payload_hash).limit(1).execute().data
        if not existing:
            raise RuntimeError(f"{sport}_SOURCE_SNAPSHOT_PERSISTENCE_FAILED")
        snapshot_id = existing[0]["snapshot_id"]
    else:
        snapshot_id = inserted[0]["snapshot_id"]
    games = _parse_completed_games(payload, snapshot_id, season)
    if games:
        client.table(cfg["games_table"]).upsert(games, on_conflict="event_id").execute()
    return {"sport": sport, "snapshot_id": snapshot_id, "events": source_row["event_count"], "completed_games": len(games), "can_execute": False}


def _iso_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_feature_rows(sport: str, *, client=None) -> dict[str, Any]:
    sport = sport.upper()
    cfg = SPORTS[sport]
    client = client or _client()
    games = client.table(cfg["games_table"]).select("event_id,season,start_time,home_team,away_team,home_score,away_score,home_win,row_inputs_hash").order("start_time").execute().data or []
    elo: dict[str, float] = defaultdict(lambda: 1500.0)
    last_played: dict[str, datetime] = {}
    margins: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    features: list[dict[str, Any]] = []
    k = 20.0
    for game in games:
        t = _iso_dt(game["start_time"])
        home, away = game["home_team"], game["away_team"]
        h_elo, a_elo = elo[home], elo[away]
        h_rest = (t - last_played[home]).total_seconds() / 86400 if home in last_played else None
        a_rest = (t - last_played[away]).total_seconds() / 86400 if away in last_played else None
        rest_diff = (h_rest - a_rest) if h_rest is not None and a_rest is not None else 0.0
        h_margin = float(np.mean(margins[home])) if margins[home] else 0.0
        a_margin = float(np.mean(margins[away])) if margins[away] else 0.0
        raw = {
            "event_id": game["event_id"], "as_of": game["start_time"],
            "home_elo": h_elo, "away_elo": a_elo, "elo_diff": h_elo - a_elo,
            "home_rest_days": h_rest, "away_rest_days": a_rest, "rest_diff": rest_diff,
            "home_rolling_margin": h_margin, "away_rolling_margin": a_margin,
            "rolling_margin_diff": h_margin - a_margin,
            "target_home_win": bool(game["home_win"]),
            "feature_schema_version": cfg["feature_schema"],
            "probability_publishable": False, "can_execute": False,
        }
        raw["feature_inputs_hash"] = _sha({k: raw[k] for k in raw if k not in ("feature_inputs_hash", "probability_publishable", "can_execute")})
        features.append(raw)
        # State update occurs only after the pregame row is frozen.
        actual = 1.0 if game["home_win"] else 0.0
        expected = 1.0 / (1.0 + 10.0 ** ((a_elo - h_elo) / 400.0))
        shift = k * (actual - expected)
        elo[home], elo[away] = h_elo + shift, a_elo - shift
        score_margin = float(game["home_score"] - game["away_score"])
        margins[home].append(score_margin)
        margins[away].append(-score_margin)
        last_played[home] = last_played[away] = t
    if features:
        client.table(cfg["features_table"]).upsert(features, on_conflict="event_id").execute()
    return {"sport": sport, "games": len(games), "feature_rows": len(features), "can_execute": False}


def _ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    total = len(y)
    out = 0.0
    for low, high in zip(np.linspace(0, 1, bins + 1)[:-1], np.linspace(0, 1, bins + 1)[1:]):
        mask = (p >= low) & (p < high if high < 1 else p <= high)
        if mask.any():
            out += float(mask.mean()) * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return out if total else float("nan")


def train_candidate(sport: str, training_code_sha: str, *, client=None) -> dict[str, Any]:
    sport = sport.upper()
    cfg = SPORTS[sport]
    client = client or _client()
    rows = client.table(cfg["features_table"]).select("*").order("as_of").execute().data or []
    if len(rows) < 200:
        raise RuntimeError(f"{sport}_MODEL_INPUTS_INSUFFICIENT: feature_rows={len(rows)} minimum=200")
    X = np.asarray([[float(r["elo_diff"]), float(r["rest_diff"] or 0), float(r["rolling_margin_diff"])] for r in rows], dtype=float)
    y = np.asarray([1 if r["target_home_win"] else 0 for r in rows], dtype=int)
    n = len(rows)
    train_end = int(n * 0.70)
    cal_end = int(n * 0.85)
    if len(set(y[:train_end])) < 2 or len(set(y[train_end:cal_end])) < 2 or len(set(y[cal_end:])) < 2:
        raise RuntimeError(f"{sport}_MODEL_INPUTS_INSUFFICIENT: chronological split lacks both outcomes")
    model = LogisticRegression(C=1.0, max_iter=2000, random_state=17).fit(X[:train_end], y[:train_end])
    cal_logits = model.decision_function(X[train_end:cal_end]).reshape(-1, 1)
    calibrator = LogisticRegression(C=1e6, max_iter=2000, random_state=17).fit(cal_logits, y[train_end:cal_end])
    test_logits = model.decision_function(X[cal_end:]).reshape(-1, 1)
    p = calibrator.predict_proba(test_logits)[:, 1]
    y_test = y[cal_end:]
    metrics = {
        "brier_score": float(brier_score_loss(y_test, p)),
        "log_loss": float(log_loss(y_test, p, labels=[0, 1])),
        "calibration_error": float(_ece(y_test, p)),
        "training_n": train_end,
        "calibration_n": cal_end - train_end,
        "test_n": n - cal_end,
        "split": "CHRONOLOGICAL_70_15_15",
    }
    dataset_hash = _sha([{k: r.get(k) for k in ("event_id", "as_of", "feature_inputs_hash", "target_home_win")} for r in rows])
    artifact_payload = {
        "feature_names": list(FEATURE_NAMES),
        "coef": [float(v) for v in model.coef_[0]],
        "intercept": float(model.intercept_[0]),
        "platt_a": float(calibrator.coef_[0][0]),
        "platt_b": float(calibrator.intercept_[0]),
        "elo_k": 20.0,
        "rolling_margin_games": 10,
        "source_family": "ESPN_PUBLIC_BASKETBALL",
    }
    artifact_checksum = _sha(artifact_payload)
    bundle = {"sport": sport, "provider": cfg["provider_identity"], "schema": cfg["feature_schema"], "artifact": artifact_payload, "dataset_hash": dataset_hash, "metrics": metrics}
    bundle_fingerprint = _sha(bundle)
    fit_start, fit_end = rows[0]["as_of"], rows[train_end - 1]["as_of"]
    cal_row = {
        "phase": "PHASE_B", "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": f"{sport}_PLATT_V1_{dataset_hash[:12]}",
        "parent_cohort": f"{sport}_OUTRIGHT_WINNER_{cfg['feature_schema']}",
        "training_n": cal_end - train_end, "fit_start": fit_start, "fit_end": fit_end,
        "fit_metrics_json": metrics, "platt_a": artifact_payload["platt_a"], "platt_b": artifact_payload["platt_b"],
        "bounds_method_version": "EMPIRICAL_HOLDOUT_V1", "promoted": False, "active": False,
        "sport": sport, "market_family": "OUTRIGHT_WINNER", "model_family": cfg["model_family"],
        "validation_status": "NOT_EVALUATED", "health_status": "NOT_EVALUATED",
        "source_data_hash": dataset_hash, "split_hash": _sha({"train_end": train_end, "cal_end": cal_end, "n": n}),
        "brier_score": metrics["brier_score"], "log_loss": metrics["log_loss"], "calibration_error": metrics["calibration_error"],
        "live_bounds_json": {"method": "EMPIRICAL_HOLDOUT_V1", "status": "CANDIDATE_ONLY"},
    }
    inserted_cal = client.table("wow_calibrators").insert(cal_row).execute().data
    if not inserted_cal:
        raise RuntimeError(f"{sport}_CALIBRATOR_PERSISTENCE_FAILED")
    calibrator_id = inserted_cal[0]["calibrator_id"]
    version = f"{sport}_EVENT_LOGIT_V1_{dataset_hash[:12]}"
    art_row = {
        "provider_identity": cfg["provider_identity"], "model_family": cfg["model_family"],
        "model_artifact_version": version, "artifact_format": "JSON_LOGISTIC_REGRESSION_V1",
        "artifact_payload": artifact_payload, "artifact_checksum": artifact_checksum, "bundle_fingerprint": bundle_fingerprint,
        "feature_schema_version": cfg["feature_schema"], "feature_transform_version": f"{sport}_PREGAME_STATE_V1",
        "training_code_sha": training_code_sha, "training_dataset_hash": dataset_hash, "training_rows": train_end,
        "validation_metrics": metrics, "calibrator_id": calibrator_id, "certification_id": None,
        "lifecycle_state": "CANDIDATE", "active": False, "promoted": False,
        "probability_publishable": False, "can_execute": False, "sport": sport,
        "specialist_calibration_identity": {"calibrator_id": calibrator_id, "method": "PLATT_TIME_SPLIT_V1", "status": "CANDIDATE_ONLY"},
    }
    inserted_art = client.table(cfg["artifacts_table"]).insert(art_row).execute().data
    if not inserted_art:
        raise RuntimeError(f"{sport}_ARTIFACT_PERSISTENCE_FAILED")
    return {"sport": sport, "artifact_id": inserted_art[0]["artifact_id"], "model_artifact_version": version, "calibrator_id": calibrator_id, "metrics": metrics, "lifecycle_state": "CANDIDATE", "probability_publishable": False, "can_execute": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    acq = sub.add_parser("acquire")
    acq.add_argument("--sport", choices=sorted(SPORTS), required=True)
    acq.add_argument("--season", type=int, required=True)
    acq.add_argument("--start", type=date.fromisoformat, required=True)
    acq.add_argument("--end", type=date.fromisoformat, required=True)
    feat = sub.add_parser("features")
    feat.add_argument("--sport", choices=sorted(SPORTS), required=True)
    train = sub.add_parser("train")
    train.add_argument("--sport", choices=sorted(SPORTS), required=True)
    train.add_argument("--training-code-sha", required=True)
    args = parser.parse_args()
    if args.cmd == "acquire":
        result = acquire_range(args.sport, args.season, args.start, args.end)
    elif args.cmd == "features":
        result = build_feature_rows(args.sport)
    else:
        result = train_candidate(args.sport, args.training_code_sha)
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
