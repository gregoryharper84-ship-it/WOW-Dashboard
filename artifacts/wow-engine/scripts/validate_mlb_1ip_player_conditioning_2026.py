"""Untouched-2026 discrimination test for the MLB 1IP player-conditioned mixture.

Development inputs are frozen before this test:
- aggregate conditional total-pitch PMF developed on 2024 and evaluated in 2025;
- BF Dirichlet shrinkage model developed on 2024 and evaluated in 2025.

This script evaluates their composition on chronological settled 2026 games
through 2026-09-13. It does not tune parameters on 2026. A warm-up window only
populates each pitcher's recent BF history; the later test window is scored
pregame-style before each settled row is admitted to history.

Research only: no promotion, publication, database mutation, or execution.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from statistics import pstdev
from typing import Any

import httpx

from mlb_1ip_bf_mixture import score_player_bf_mixture
from mlb_1ip_training_dataset import game_training_rows
from prop_auto_hydration import MLB_STATS_API_BASE, _int, _request_json

CUT_OFF_DATE = "2026-09-13"
WARMUP_GAMES = 400
TEST_GAMES = 700
MIN_HISTORY_FOR_SERVING_COHORT = 5
MAX_GAP_RATE = 0.05
MAX_BRIER_REGRESSION = 0.005
MAX_ECE = 0.06
MIN_AUC_GAIN = 0.01
MIN_PROBABILITY_STD = 0.002
PRIMARY_LINES = (11.5, 15.5)
CAN_EXECUTE = False


def _fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "research-fixtures" / "mlb_1ip_player_conditioning_dev_bundle_20260914.json"


def _load_bundle() -> dict[str, Any]:
    return json.loads(_fixture_path().read_text())


def _final_games() -> list[tuple[str, int]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={
            "sportId": "1",
            "season": "2026",
            "gameType": "R",
            "startDate": "2026-03-01",
            "endDate": CUT_OFF_DATE,
        },
        http_get=httpx.get,
    )
    games: list[tuple[str, int]] = []
    for block in payload.get("dates") or []:
        for game in (block or {}).get("games") or []:
            pk = _int(game.get("gamePk"))
            status = str(((game.get("status") or {}).get("abstractGameState") or "")).upper()
            when = str(game.get("gameDate") or "")
            if pk > 0 and status == "FINAL" and when:
                games.append((when, pk))
    games.sort(key=lambda x: (x[0], x[1]))
    need = WARMUP_GAMES + TEST_GAMES
    if len(games) < need:
        raise RuntimeError(f"MLB_1IP_2026_FINAL_GAME_SAMPLE_INSUFFICIENT n={len(games)} need={need}")
    return games[-need:]


def _auc(actual: list[int], predicted: list[float]) -> float | None:
    pos = sum(actual)
    neg = len(actual) - pos
    if pos == 0 or neg == 0:
        return None
    pairs = sorted(zip(predicted, actual), key=lambda x: x[0])
    rank_sum_pos = 0.0
    rank = 1
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (rank + (rank + (j - i) - 1)) / 2.0
        rank_sum_pos += avg_rank * sum(y for _, y in pairs[i:j])
        rank += j - i
        i = j
    return (rank_sum_pos - pos * (pos + 1) / 2.0) / (pos * neg)


def _metrics(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "brier": None, "ece": None, "auc": None, "probability_std": None}
    y = [int(r["actual_more"]) for r in rows]
    p = [float(r[probability_key]) for r in rows]
    n = len(rows)
    brier = sum((a - b) ** 2 for a, b in zip(y, p)) / n
    ece = 0.0
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        idx = [j for j, v in enumerate(p) if (lo <= v < hi) or (i == 9 and v == 1.0)]
        if not idx:
            continue
        conf = sum(p[j] for j in idx) / len(idx)
        hit = sum(y[j] for j in idx) / len(idx)
        ece += len(idx) / n * abs(conf - hit)
    eps = 1e-12
    log_loss = -sum(
        a * math.log(max(eps, min(1.0 - eps, b)))
        + (1 - a) * math.log(max(eps, min(1.0 - eps, 1.0 - b)))
        for a, b in zip(y, p)
    ) / n
    return {
        "n": n,
        "brier": brier,
        "ece": ece,
        "auc": _auc(y, p),
        "log_loss": log_loss,
        "mean_probability": sum(p) / n,
        "observed_hit_rate": sum(y) / n,
        "calibration_bias": sum(p) / n - sum(y) / n,
        "probability_std": pstdev(p) if n > 1 else 0.0,
        "unique_probability_count": len({round(v, 12) for v in p}),
    }


def _append_history(histories: dict[int, list[int]], pitcher_id: int, bf: int, limit: int) -> None:
    histories.setdefault(int(pitcher_id), []).append(int(bf))
    if len(histories[int(pitcher_id)]) > limit:
        del histories[int(pitcher_id)][:-limit]


def main() -> None:
    bundle = _load_bundle()
    aggregate = bundle["aggregate_artifact"]["artifact_payload"]
    lines = tuple(float(v) for v in bundle["aggregate_artifact"]["validated_lines"])
    bf_artifact = bundle["bf_artifact"]
    alpha = float(bf_artifact["alpha"])
    prior = dict(bf_artifact["league_prior"])
    history_limit = int(bf_artifact["recent_history_limit"])

    games = _final_games()
    histories: dict[int, list[int]] = {}
    assignments: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    for game_index, (event_time, game_pk) in enumerate(games):
        try:
            _, manifest = game_training_rows(game_pk)
        except Exception as exc:
            gaps.append({"game_pk": game_pk, "event_time": event_time, "error_type": type(exc).__name__})
            continue
        details = list(manifest.get("rows_detail") or [])
        for detail in details:
            pitcher_id = int(detail.get("pitcher_id") or 0)
            bf = int(detail.get("bf") or 0)
            pitches = int(detail.get("pitches") or 0)
            if pitcher_id <= 0 or bf < 3 or pitches <= 0:
                continue
            recent = list(histories.get(pitcher_id, []))[-history_limit:]
            if game_index >= WARMUP_GAMES:
                for line in lines:
                    scored = score_player_bf_mixture(
                        aggregate_artifact_payload=aggregate,
                        bf_league_prior=prior,
                        bf_alpha=alpha,
                        pitcher_id=pitcher_id,
                        recent_batters_faced=recent,
                        line_value=line,
                        side="MORE",
                    )
                    assignments.append({
                        "event_time": event_time,
                        "game_pk": game_pk,
                        "pitcher_id": pitcher_id,
                        "line": line,
                        "actual_pitches": pitches,
                        "actual_bf": bf,
                        "actual_more": 1 if pitches > line else 0,
                        "history_n": len(recent),
                        "aggregate_probability": scored["aggregate_baseline_probability"],
                        "player_probability": scored["selected_probability"],
                        "player_delta": scored["player_probability_delta_vs_aggregate"],
                        "P_BF_3": scored["P_BF_3"],
                        "P_BF_4": scored["P_BF_4"],
                        "P_BF_GE_5": scored["P_BF_GE_5"],
                    })
            _append_history(histories, pitcher_id, bf, history_limit)

    gap_rate = len(gaps) / len(games)
    if gap_rate > MAX_GAP_RATE:
        raise RuntimeError(f"MLB_1IP_2026_SOURCE_GAP_RATE_EXCEEDED rate={gap_rate:.4f}")

    mature = [r for r in assignments if int(r["history_n"]) >= MIN_HISTORY_FOR_SERVING_COHORT]
    by_line: dict[str, Any] = {}
    gate_failures: list[str] = []
    for line in lines:
        line_rows = [r for r in mature if float(r["line"]) == line]
        base = _metrics(line_rows, "aggregate_probability")
        player = _metrics(line_rows, "player_probability")
        auc_gain = (
            float(player["auc"]) - float(base["auc"])
            if player["auc"] is not None and base["auc"] is not None
            else None
        )
        line_result = {
            "aggregate": base,
            "player_conditioned": player,
            "auc_gain": auc_gain,
            "brier_delta": (
                float(player["brier"]) - float(base["brier"])
                if player["brier"] is not None and base["brier"] is not None
                else None
            ),
        }
        by_line[str(line)] = line_result
        if line in PRIMARY_LINES:
            if line_result["brier_delta"] is None or line_result["brier_delta"] > MAX_BRIER_REGRESSION:
                gate_failures.append(f"LINE_{line}_BRIER_REGRESSION")
            if player["ece"] is None or float(player["ece"]) > MAX_ECE:
                gate_failures.append(f"LINE_{line}_ECE_ABOVE_LIMIT")
            if auc_gain is None or auc_gain < MIN_AUC_GAIN:
                gate_failures.append(f"LINE_{line}_AUC_GAIN_INSUFFICIENT")
            if player["probability_std"] is None or float(player["probability_std"]) < MIN_PROBABILITY_STD:
                gate_failures.append(f"LINE_{line}_DISCRIMINATION_VARIANCE_INSUFFICIENT")

    report = {
        "purpose": "MLB_1IP_PLAYER_CONDITIONING_UNTOUCHED_2026_TEST",
        "cutoff_date": CUT_OFF_DATE,
        "warmup_games": WARMUP_GAMES,
        "test_games": TEST_GAMES,
        "games_attempted": len(games),
        "source_gaps": gaps,
        "source_gap_rate": gap_rate,
        "mature_assignment_rows": len(mature),
        "minimum_history_for_serving_cohort": MIN_HISTORY_FOR_SERVING_COHORT,
        "aggregate_artifact_checksum": bundle["aggregate_artifact"]["artifact_checksum"],
        "bf_artifact_checksum": bf_artifact["artifact_checksum"],
        "bf_alpha": alpha,
        "line_results": by_line,
        "primary_lines": list(PRIMARY_LINES),
        "gates": {
            "max_brier_regression": MAX_BRIER_REGRESSION,
            "max_ece": MAX_ECE,
            "min_auc_gain": MIN_AUC_GAIN,
            "min_probability_std": MIN_PROBABILITY_STD,
        },
        "validation_passed": not gate_failures,
        "validation_failures": gate_failures,
        "promotion_status": "REVIEW_REQUIRED" if not gate_failures else "BLOCKED",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }

    out_dir = Path(os.environ.get("MLB_1IP_PLAYER_CONDITIONING_OUT", "/tmp/mlb-1ip-player-conditioning"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "untouched_2026_discrimination_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    (out_dir / "untouched_2026_assignments.json").write_text(json.dumps(assignments, indent=2, sort_keys=True))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
