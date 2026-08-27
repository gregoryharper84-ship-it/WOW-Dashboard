from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from run_mlb_v2_pregame import (
    GOVERNANCE,
    SEED,
    SOURCE_URL,
    bootstrap_brier_improvement,
    build_pregame_dataset,
    download_sources,
    metrics,
    pair_games,
    parse_season,
    xy,
)

AS_OF = "2026-08-27"
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"

# Retrosheet/Retrosplits team keys used by the historical feature state.
MLB_ID_TO_RETRO = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHN",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KCA", 119: "LAN", 120: "WAS", 121: "NYN", 133: "OAK",
    134: "PIT", 135: "SDN", 136: "SEA", 137: "SFN", 138: "SLN",
    139: "TBA", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CHA", 146: "MIA", 147: "NYA", 158: "MIL",
}


def get_json(url: str, timeout: int = 30, attempts: int = 4) -> dict[str, Any]:
    last: Exception | None = None
    req = urllib.request.Request(url, headers={"User-Agent": "WOW-MLB-V2-research/20260827"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"GET failed after {attempts} attempts: {url}: {last}")


def innings_to_outs(v: Any) -> int:
    if v in (None, ""):
        return 0
    s = str(v)
    if "." not in s:
        try:
            return int(float(s) * 3)
        except Exception:
            return 0
    whole, frac = s.split(".", 1)
    try:
        return 3 * int(whole) + int(frac[:1] or "0")
    except Exception:
        return 0


def stat_int(d: dict[str, Any], *keys: str) -> int:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            try:
                return int(float(d[k]))
            except Exception:
                continue
    return 0


def schedule_games() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "sportId": 1,
            "gameTypes": "R",
            "startDate": "2026-03-01",
            "endDate": AS_OF,
        }
    )
    payload = get_json(f"{MLB_SCHEDULE_URL}?{params}")
    final_games: list[dict[str, Any]] = []
    all_games = 0
    nonfinal = 0
    unknown_team_ids: set[int] = set()
    for day in payload.get("dates", []):
        for g in day.get("games", []):
            all_games += 1
            if g.get("gameType") != "R":
                continue
            status = g.get("status", {})
            if status.get("abstractGameState") != "Final":
                nonfinal += 1
                continue
            hid = int(g["teams"]["home"]["team"]["id"])
            aid = int(g["teams"]["away"]["team"]["id"])
            if hid not in MLB_ID_TO_RETRO:
                unknown_team_ids.add(hid)
            if aid not in MLB_ID_TO_RETRO:
                unknown_team_ids.add(aid)
            final_games.append(
                {
                    "game_pk": int(g["gamePk"]),
                    "game_date": str(g["officialDate"]),
                    "game_number": int(g.get("gameNumber") or 1),
                    "home_id": hid,
                    "away_id": aid,
                    "home_score": int(g["teams"]["home"].get("score") or 0),
                    "away_score": int(g["teams"]["away"].get("score") or 0),
                }
            )
    audit = {
        "schedule_total_games": all_games,
        "schedule_final_regular_games": len(final_games),
        "schedule_nonfinal_as_of": nonfinal,
        "unknown_team_ids": sorted(unknown_team_ids),
        "as_of": AS_OF,
    }
    return final_games, audit


def team_game_shell(g: dict[str, Any], is_home: bool) -> dict[str, Any]:
    team_id = g["home_id"] if is_home else g["away_id"]
    opp_id = g["away_id"] if is_home else g["home_id"]
    return {
        "game_key": f"MLBAM{g['game_pk']}",
        "game_date": g["game_date"],
        "game_number": g["game_number"],
        "season_phase": "R",
        "site": None,
        "is_home": is_home,
        "team": MLB_ID_TO_RETRO[team_id],
        "opponent": MLB_ID_TO_RETRO[opp_id],
        "runs": g["home_score"] if is_home else g["away_score"],
        "hits": 0,
        "hr": 0,
        "bb": 0,
        "so": 0,
        "tb": 0,
        "bp_out": 0,
        "bp_er": 0,
        "bp_so": 0,
        "bp_bb": 0,
        "bp_pitch": 0,
        "bp_relief_appearances": 0,
        "starter_id": None,
        "starter_out": 0,
        "starter_er": 0,
        "starter_so": 0,
        "starter_bb": 0,
        "starter_hr": 0,
        "starter_h": 0,
        "starter_pitch": 0,
        "starter_tbf": 0,
    }


def parse_box_team(box_side: dict[str, Any], shell: dict[str, Any]) -> dict[str, Any]:
    batting = box_side.get("teamStats", {}).get("batting", {})
    shell["hits"] = stat_int(batting, "hits")
    shell["hr"] = stat_int(batting, "homeRuns")
    shell["bb"] = stat_int(batting, "baseOnBalls")
    shell["so"] = stat_int(batting, "strikeOuts")
    tb = stat_int(batting, "totalBases")
    if tb <= 0 and shell["hits"] > 0:
        doubles = stat_int(batting, "doubles")
        triples = stat_int(batting, "triples")
        tb = shell["hits"] + doubles + 2 * triples + 3 * shell["hr"]
    shell["tb"] = tb

    pitcher_ids = [int(x) for x in box_side.get("pitchers", [])]
    players = box_side.get("players", {})
    for idx, pid in enumerate(pitcher_ids):
        pdata = players.get(f"ID{pid}", {})
        p = pdata.get("stats", {}).get("pitching", {})
        outs = stat_int(p, "outs") or innings_to_outs(p.get("inningsPitched"))
        er = stat_int(p, "earnedRuns")
        so = stat_int(p, "strikeOuts")
        bb = stat_int(p, "baseOnBalls")
        hr = stat_int(p, "homeRuns")
        hits = stat_int(p, "hits")
        pitches = stat_int(p, "numberOfPitches", "pitchesThrown")
        tbf = stat_int(p, "battersFaced")
        if idx == 0:
            shell["starter_id"] = f"mlbam:{pid}"
            shell["starter_out"] = outs
            shell["starter_er"] = er
            shell["starter_so"] = so
            shell["starter_bb"] = bb
            shell["starter_hr"] = hr
            shell["starter_h"] = hits
            shell["starter_pitch"] = pitches
            shell["starter_tbf"] = tbf
        else:
            shell["bp_out"] += outs
            shell["bp_er"] += er
            shell["bp_so"] += so
            shell["bp_bb"] += bb
            shell["bp_pitch"] += pitches
            shell["bp_relief_appearances"] += 1
    return shell


def fetch_one_box(g: dict[str, Any]) -> tuple[int, dict[tuple[str, str], dict[str, Any]]]:
    payload = get_json(MLB_BOXSCORE_URL.format(game_pk=g["game_pk"]))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for side_name, is_home in (("home", True), ("away", False)):
        shell = team_game_shell(g, is_home)
        parsed = parse_box_team(payload["teams"][side_name], shell)
        out[(parsed["game_key"], parsed["team"])] = parsed
    return g["game_pk"], out


def fetch_2026_team_games(max_workers: int) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    games, sched_audit = schedule_games()
    if sched_audit["unknown_team_ids"]:
        return {}, {**sched_audit, "boxscore_failures": [], "data_complete": False}

    combined: dict[tuple[str, str], dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_one_box, g): g for g in games}
        done = 0
        for fut in as_completed(futs):
            g = futs[fut]
            try:
                _, rows = fut.result()
                combined.update(rows)
            except Exception as exc:
                failures.append({"game_pk": g["game_pk"], "error": str(exc)[:500]})
            done += 1
            if done % 250 == 0:
                print(f"BOX_PROGRESS done={done}/{len(games)} failures={len(failures)}")

    expected_team_rows = 2 * len(games)
    audit = {
        **sched_audit,
        "boxscore_games_requested": len(games),
        "boxscore_failures": failures,
        "team_game_rows": len(combined),
        "expected_team_game_rows": expected_team_rows,
        "data_complete": not failures and len(combined) == expected_team_rows,
        "source": "MLB_STATS_API",
        "source_change_from_training": True,
        "source_change_note": "2023-2025 training history uses Chadwick Retrosplits; 2026 confirmation uses official MLB Stats API boxscores.",
    }
    return combined, audit


def fit_locked_model(train_rows: list[dict[str, Any]], cal_rows: list[dict[str, Any]]) -> tuple[Any, Any]:
    Xtr, ytr = xy(train_rows)
    Xcal, ycal = xy(cal_rows)
    model = HistGradientBoostingClassifier(
        learning_rate=0.05,
        max_iter=250,
        max_depth=3,
        min_samples_leaf=30,
        l2_regularization=1.0,
        random_state=SEED,
    )
    model.fit(Xtr, ytr)
    pcal = model.predict_proba(Xcal)[:, 1]
    platt = LogisticRegression(C=1.0, max_iter=5000, solver="lbfgs", random_state=SEED)
    platt.fit(pcal.reshape(-1, 1), ycal)
    return model, platt


def predict_locked(model: Any, platt: Any, rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    X, _ = xy(rows)
    raw = model.predict_proba(X)[:, 1]
    p = platt.predict_proba(raw.reshape(-1, 1))[:, 1]
    return raw, p


def evaluate(
    name: str,
    model: Any,
    platt: Any,
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    bootstraps: int,
    seed_offset: int,
) -> dict[str, Any]:
    _, ytest = xy(test_rows)
    raw, p = predict_locked(model, platt, test_rows)
    train_rate = float(np.mean([r["y"] for r in train_rows]))
    naive = np.full(len(ytest), train_rate)
    home54 = np.full(len(ytest), 0.54)
    m = metrics(ytest, p)
    raw_m = metrics(ytest, raw)
    naive_m = metrics(ytest, naive)
    h54_m = metrics(ytest, home54)
    b_naive = bootstrap_brier_improvement(ytest, naive, p, bootstraps, SEED + seed_offset)
    b_h54 = bootstrap_brier_improvement(ytest, home54, p, bootstraps, SEED + seed_offset + 10)
    return {
        "name": name,
        "test_n": len(test_rows),
        "train_home_rate": train_rate,
        "selected_probability_metrics": m,
        "raw_precalibration_metrics": raw_m,
        "naive_metrics": naive_m,
        "home_field_0_54_metrics": h54_m,
        "point_brier_gain_vs_naive": float(naive_m["brier"] - m["brier"]),
        "point_brier_gain_vs_home54": float(h54_m["brier"] - m["brier"]),
        "point_logloss_gain_vs_home54": float(h54_m["log_loss"] - m["log_loss"]),
        "bootstrap_vs_naive": b_naive,
        "bootstrap_vs_home54": b_h54,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("research/mlb-v2-2026-confirmation-out"))
    ap.add_argument("--bootstraps", type=int, default=10000)
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    # Historical source is exactly the V2 source family.
    source_paths = download_sources(out / "raw")
    all_team_games: dict[tuple[str, str], dict[str, Any]] = {}
    for year in (2023, 2024, 2025):
        all_team_games.update(parse_season(source_paths[year]))

    stats_2026, source_2026_audit = fetch_2026_team_games(args.workers)
    all_team_games.update(stats_2026)

    games = pair_games(all_team_games)
    rows, feature_audit = build_pregame_dataset(games)

    frozen_train = [r for r in rows if r["game_date"] <= "2024-06-30"]
    frozen_cal = [r for r in rows if "2024-07-01" <= r["game_date"] <= "2024-08-31"]
    rolling_train = [r for r in rows if r["game_date"] <= "2025-06-30"]
    rolling_cal = [r for r in rows if "2025-07-01" <= r["game_date"] <= "2025-11-01"]
    test_2026 = [r for r in rows if "2026-01-01" <= r["game_date"] <= AS_OF]

    if not test_2026:
        raise RuntimeError("No eligible 2026 confirmation rows")

    print(
        "CONFIRM_SPLITS",
        json.dumps(
            {
                "frozen_train": len(frozen_train),
                "frozen_cal": len(frozen_cal),
                "rolling_train": len(rolling_train),
                "rolling_cal": len(rolling_cal),
                "test_2026": len(test_2026),
                "test_dates": [test_2026[0]["game_date"], test_2026[-1]["game_date"]],
            },
            sort_keys=True,
        ),
    )

    frozen_model, frozen_platt = fit_locked_model(frozen_train, frozen_cal)
    rolling_model, rolling_platt = fit_locked_model(rolling_train, rolling_cal)

    frozen_eval = evaluate(
        "FROZEN_V2_2024_FIT",
        frozen_model,
        frozen_platt,
        frozen_train,
        test_2026,
        args.bootstraps,
        100,
    )
    rolling_eval = evaluate(
        "LOCKED_ARCHITECTURE_ANNUAL_REFIT_PRE2026",
        rolling_model,
        rolling_platt,
        rolling_train,
        test_2026,
        args.bootstraps,
        200,
    )

    complete = bool(source_2026_audit["data_complete"])
    def directional(ev: dict[str, Any]) -> bool:
        return bool(
            ev["point_brier_gain_vs_home54"] > 0
            and ev["point_logloss_gain_vs_home54"] > 0
            and ev["selected_probability_metrics"]["roc_auc"] > 0.5
        )

    frozen_support = directional(frozen_eval)
    rolling_support = directional(rolling_eval)
    rolling_secure = rolling_eval["bootstrap_vs_home54"]["ci95"][0] > 0.0
    confirmation_gate = bool(complete and frozen_support and rolling_support and rolling_secure)

    results = {
        "status": "MLB_V2_2026_FRESH_CONFIRMATION",
        "as_of": AS_OF,
        "pre_registered_before_2026_outcome_read": {
            "model_architecture": "HistGradientBoosting depth=3, learning_rate=.05, max_iter=250, min_samples_leaf=30, l2=1",
            "calibration": "Platt logistic C=1",
            "frozen_transport_fit": "base <=2024-06-30; calibration 2024-07-01..2024-08-31",
            "rolling_refit": "same locked architecture; base <=2025-06-30; calibration 2025-07-01..2025-11-01",
            "confirmation_holdout": f"2026 completed regular-season games through {AS_OF}",
            "no_2026_model_family_selection": True,
            "no_2026_calibrator_selection": True,
        },
        "source_2026_audit": source_2026_audit,
        "feature_audit": feature_audit,
        "split_counts": {
            "frozen_train": len(frozen_train),
            "frozen_calibration": len(frozen_cal),
            "rolling_train": len(rolling_train),
            "rolling_calibration": len(rolling_cal),
            "test_2026": len(test_2026),
        },
        "test_date_range": [test_2026[0]["game_date"], test_2026[-1]["game_date"]],
        "frozen_transport": frozen_eval,
        "rolling_refit": rolling_eval,
        "confirmation_gate": {
            "data_complete": complete,
            "frozen_directional_support_vs_home54": frozen_support,
            "rolling_directional_support_vs_home54": rolling_support,
            "rolling_bootstrap_ci_lower_gt_zero_vs_home54": bool(rolling_secure),
            "confirmation_gate_pass": confirmation_gate,
            "rule": "PASS requires complete 2026 acquisition, directional Brier/log-loss/AUC support from both frozen and rolling models, and rolling 95% bootstrap Brier-improvement CI entirely above zero versus fixed 0.54 home baseline.",
        },
        "governance": GOVERNANCE,
    }
    (out / "mlb_v2_2026_confirmation.json").write_text(json.dumps(results, indent=2))

    _, ytest = xy(test_2026)
    fraw, fp = predict_locked(frozen_model, frozen_platt, test_2026)
    rraw, rp = predict_locked(rolling_model, rolling_platt, test_2026)
    with (out / "mlb_v2_2026_predictions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "game_key", "game_date", "home_team", "away_team", "home_win",
            "frozen_raw", "frozen_platt", "rolling_raw", "rolling_platt",
        ])
        for i, r in enumerate(test_2026):
            w.writerow([
                r["game_key"], r["game_date"], r["home_team"], r["away_team"], ytest[i],
                fraw[i], fp[i], rraw[i], rp[i],
            ])

    joblib.dump(frozen_model, out / "frozen_v2_base_model.joblib")
    joblib.dump(frozen_platt, out / "frozen_v2_platt.joblib")
    joblib.dump(rolling_model, out / "rolling_v2_base_model.joblib")
    joblib.dump(rolling_platt, out / "rolling_v2_platt.joblib")
    (out / "governance.json").write_text(json.dumps(GOVERNANCE, indent=2))

    print("CONFIRM_RESULT", json.dumps({
        "source_complete": complete,
        "test_n": len(test_2026),
        "frozen": {
            "brier": frozen_eval["selected_probability_metrics"]["brier"],
            "auc": frozen_eval["selected_probability_metrics"]["roc_auc"],
            "gain_vs_home54": frozen_eval["point_brier_gain_vs_home54"],
            "bootstrap_vs_home54": frozen_eval["bootstrap_vs_home54"],
        },
        "rolling": {
            "brier": rolling_eval["selected_probability_metrics"]["brier"],
            "auc": rolling_eval["selected_probability_metrics"]["roc_auc"],
            "gain_vs_home54": rolling_eval["point_brier_gain_vs_home54"],
            "bootstrap_vs_home54": rolling_eval["bootstrap_vs_home54"],
        },
        "confirmation_gate": results["confirmation_gate"],
        "governance": GOVERNANCE,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
