"""Leakage-safe exact-line validation for MLB 1IP 14.5/16.5 expansion.

This validation is intentionally frozen and non-serving:
- aggregate PMF and BF shrinkage inputs come from the immutable 2024/2025
  development bundle;
- settled 2026 games through 2026-09-13 are used exactly once as the temporal
  test period;
- the first 400 games only warm pitcher BF history;
- the next 700 games are scored before the current outcome is admitted;
- no parameters, thresholds, or line-specific transforms are tuned on 2026.

The script validates exact half-point support only. It does not interpolate,
promote an artifact, mutate Supabase, publish probabilities, or execute bets.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import httpx

from mlb_1ip_player_conditioned import (
    ARTIFACT_FORMAT as PLAYER_ARTIFACT_FORMAT,
    MODEL_FAMILY as PLAYER_MODEL_FAMILY,
    score_player_conditioned_1ip,
)
from mlb_1ip_training_dataset import game_training_rows
from prop_auto_hydration import MLB_STATS_API_BASE, _int, _request_json

CUT_OFF_DATE = "2026-09-13"
WARMUP_GAMES = 400
TEST_GAMES = 700
HISTORY_LIMIT = 10
MIN_HISTORY = 5
EXPANSION_LINES = (14.5, 16.5)
SENTINEL_LINES = (15.5,)
VALIDATION_LINES = EXPANSION_LINES + SENTINEL_LINES
MAX_GAP_RATE = 0.05
MIN_MATURE_ROWS_PER_LINE = 500
MAX_BRIER_REGRESSION = 0.005
MAX_ECE = 0.06
MIN_AUC_GAIN = 0.01
MIN_PROBABILITY_STD = 0.002
CAN_EXECUTE = False


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "research-fixtures"
        / "mlb_1ip_player_conditioning_dev_bundle_20260914.json"
    )


def _load_bundle() -> dict[str, Any]:
    return json.loads(_fixture_path().read_text(encoding="utf-8"))


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
            event_time = str(game.get("gameDate") or "")
            if pk > 0 and status == "FINAL" and event_time:
                games.append((event_time, pk))
    games.sort(key=lambda row: (row[0], row[1]))
    required = WARMUP_GAMES + TEST_GAMES
    if len(games) < required:
        raise RuntimeError(
            f"MLB_1IP_LINE_EXPANSION_SAMPLE_INSUFFICIENT n={len(games)} need={required}"
        )
    return games[-required:]


def _auc(actual: list[int], predicted: list[float]) -> float | None:
    positives = sum(actual)
    negatives = len(actual) - positives
    if positives == 0 or negatives == 0:
        return None
    pairs = sorted(zip(predicted, actual), key=lambda row: row[0])
    positive_rank_sum = 0.0
    rank = 1
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (rank + (rank + (j - i) - 1)) / 2.0
        positive_rank_sum += avg_rank * sum(y for _, y in pairs[i:j])
        rank += j - i
        i = j
    return (
        positive_rank_sum - positives * (positives + 1) / 2.0
    ) / (positives * negatives)


def _metrics(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "brier": None,
            "ece": None,
            "auc": None,
            "probability_std": None,
            "unique_probability_count": 0,
        }
    actual = [int(row["actual_more"]) for row in rows]
    predicted = [float(row[probability_key]) for row in rows]
    n = len(rows)
    brier = sum((y - p) ** 2 for y, p in zip(actual, predicted)) / n
    ece = 0.0
    for bin_index in range(10):
        lo = bin_index / 10
        hi = (bin_index + 1) / 10
        idx = [
            j
            for j, p in enumerate(predicted)
            if (lo <= p < hi) or (bin_index == 9 and p == 1.0)
        ]
        if not idx:
            continue
        confidence = sum(predicted[j] for j in idx) / len(idx)
        hit_rate = sum(actual[j] for j in idx) / len(idx)
        ece += len(idx) / n * abs(confidence - hit_rate)
    hit_rate = sum(actual) / n
    mean_probability = sum(predicted) / n
    return {
        "n": n,
        "brier": brier,
        "ece": ece,
        "auc": _auc(actual, predicted),
        "probability_std": pstdev(predicted) if n > 1 else 0.0,
        "unique_probability_count": len({round(p, 12) for p in predicted}),
        "mean_probability": mean_probability,
        "observed_hit_rate": hit_rate,
        "calibration_bias": mean_probability - hit_rate,
    }


def _append_history(histories: dict[int, list[int]], pitcher_id: int, bf: int) -> None:
    history = histories.setdefault(int(pitcher_id), [])
    history.append(int(bf))
    if len(history) > HISTORY_LIMIT:
        del history[:-HISTORY_LIMIT]


def _player_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    aggregate = dict(bundle["aggregate_artifact"]["artifact_payload"])
    bf = dict(bundle["bf_artifact"])
    return {
        "model_family": PLAYER_MODEL_FAMILY,
        "artifact_format": PLAYER_ARTIFACT_FORMAT,
        "aggregate_artifact_checksum": bundle["aggregate_artifact"]["artifact_checksum"],
        "aggregate_artifact_payload": aggregate,
        "bf_model_family": bf["model_family"],
        "bf_alpha": float(bf["alpha"]),
        "bf_league_prior": dict(bf["league_prior"]),
        "recent_history_limit": int(bf["recent_history_limit"]),
        "probability_publishable": False,
        "can_execute": False,
    }


def main() -> None:
    bundle = _load_bundle()
    artifact_payload = _player_payload(bundle)
    aggregate_checksum = str(bundle["aggregate_artifact"]["artifact_checksum"])
    bf_checksum = str(bundle["bf_artifact"]["artifact_checksum"])
    if aggregate_checksum != str(artifact_payload["aggregate_artifact_payload"].get("artifact_checksum")):
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_AGGREGATE_CHECKSUM_MISMATCH")
    if bundle["bf_artifact"].get("historical_validation_passed") is not True:
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_BF_ARTIFACT_NOT_VALIDATED")

    games = _final_games()
    histories: dict[int, list[int]] = {}
    assignments: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    partition_failures = 0
    bound_invariant_failures = 0

    for game_index, (event_time, game_pk) in enumerate(games):
        try:
            _, manifest = game_training_rows(game_pk)
        except Exception as exc:  # typed into evidence; never silently drop gaps
            gaps.append(
                {
                    "game_pk": game_pk,
                    "event_time": event_time,
                    "error_type": type(exc).__name__,
                }
            )
            continue
        manifests.append(
            {
                "event_time": event_time,
                "game_pk": game_pk,
                "source_sha256": manifest.get("source_sha256"),
                "selection_rule": manifest.get("selection_rule"),
            }
        )
        for detail in list(manifest.get("rows_detail") or []):
            pitcher_id = int(detail.get("pitcher_id") or 0)
            bf = int(detail.get("bf") or 0)
            pitches = int(detail.get("pitches") or 0)
            if pitcher_id <= 0 or bf < 3 or pitches <= 0:
                continue
            recent = list(histories.get(pitcher_id, []))[-HISTORY_LIMIT:]
            if game_index >= WARMUP_GAMES and len(recent) >= MIN_HISTORY:
                for line in VALIDATION_LINES:
                    more = score_player_conditioned_1ip(
                        artifact_payload=artifact_payload,
                        pitcher_id=pitcher_id,
                        recent_batters_faced=recent,
                        line_value=line,
                        side="MORE",
                    )
                    less = score_player_conditioned_1ip(
                        artifact_payload=artifact_payload,
                        pitcher_id=pitcher_id,
                        recent_batters_faced=recent,
                        line_value=line,
                        side="LESS",
                    )
                    if abs(float(more["selected_probability"]) + float(less["selected_probability"]) - 1.0) > 1e-12:
                        partition_failures += 1
                    if not (
                        float(more["lower_bound"])
                        <= float(more["selected_probability"])
                        <= float(more["upper_bound"])
                    ):
                        bound_invariant_failures += 1
                    assignments.append(
                        {
                            "event_time": event_time,
                            "game_pk": game_pk,
                            "pitcher_id": pitcher_id,
                            "line": line,
                            "actual_pitches": pitches,
                            "actual_bf": bf,
                            "actual_more": 1 if pitches > line else 0,
                            "history_n": len(recent),
                            "aggregate_probability": float(more["aggregate_baseline_probability"]),
                            "player_probability": float(more["selected_probability"]),
                            "player_lower_bound": float(more["lower_bound"]),
                            "player_upper_bound": float(more["upper_bound"]),
                            "player_delta": float(more["player_probability_delta_vs_aggregate"]),
                            "P_BF_3": float(more["P_BF_3"]),
                            "P_BF_4": float(more["P_BF_4"]),
                            "P_BF_GE_5": float(more["P_BF_GE_5"]),
                        }
                    )
            _append_history(histories, pitcher_id, bf)

    gap_rate = len(gaps) / len(games)
    failures: list[str] = []
    if gap_rate > MAX_GAP_RATE:
        failures.append("SOURCE_GAP_RATE_EXCEEDED")
    if partition_failures:
        failures.append("HALF_POINT_PARTITION_INVARIANT_FAILED")
    if bound_invariant_failures:
        failures.append("CALIBRATED_BOUND_ORDER_INVARIANT_FAILED")

    line_results: dict[str, Any] = {}
    for line in VALIDATION_LINES:
        line_rows = [row for row in assignments if float(row["line"]) == line]
        aggregate = _metrics(line_rows, "aggregate_probability")
        player = _metrics(line_rows, "player_probability")
        auc_gain = (
            float(player["auc"]) - float(aggregate["auc"])
            if player["auc"] is not None and aggregate["auc"] is not None
            else None
        )
        brier_delta = (
            float(player["brier"]) - float(aggregate["brier"])
            if player["brier"] is not None and aggregate["brier"] is not None
            else None
        )
        lower_bounds = [float(row["player_lower_bound"]) for row in line_rows]
        result = {
            "aggregate": aggregate,
            "player_conditioned": player,
            "auc_gain": auc_gain,
            "brier_delta": brier_delta,
            "mean_calibrated_lower_bound": mean(lower_bounds) if lower_bounds else None,
            "lower_bound_unique_count": len({round(v, 12) for v in lower_bounds}),
        }
        line_results[str(line)] = result

        # The sentinel proves the previously certified serving behavior did not
        # collapse, while promotion is gated specifically by expansion lines.
        if line in EXPANSION_LINES:
            if int(player["n"] or 0) < MIN_MATURE_ROWS_PER_LINE:
                failures.append(f"LINE_{line}_MATURE_SAMPLE_INSUFFICIENT")
            if brier_delta is None or brier_delta > MAX_BRIER_REGRESSION:
                failures.append(f"LINE_{line}_BRIER_REGRESSION")
            if player["ece"] is None or float(player["ece"]) > MAX_ECE:
                failures.append(f"LINE_{line}_ECE_ABOVE_LIMIT")
            if auc_gain is None or auc_gain < MIN_AUC_GAIN:
                failures.append(f"LINE_{line}_AUC_GAIN_INSUFFICIENT")
            if player["probability_std"] is None or float(player["probability_std"]) < MIN_PROBABILITY_STD:
                failures.append(f"LINE_{line}_DISCRIMINATION_VARIANCE_INSUFFICIENT")
            if int(player["unique_probability_count"] or 0) < 2:
                failures.append(f"LINE_{line}_PLAYER_PROBABILITY_COLLAPSE")
            if int(result["lower_bound_unique_count"] or 0) < 2:
                failures.append(f"LINE_{line}_LOWER_BOUND_COLLAPSE")

    lineage = {
        "aggregate_artifact_checksum": aggregate_checksum,
        "bf_artifact_checksum": bf_checksum,
        "validation_code_sha": os.getenv("GITHUB_SHA") or "LOCAL",
        "cutoff_date": CUT_OFF_DATE,
        "warmup_games": WARMUP_GAMES,
        "test_games": TEST_GAMES,
        "validation_lines": list(VALIDATION_LINES),
        "source_manifest_hash": _sha(manifests),
        "assignments_hash": _sha(assignments),
    }
    lineage["validation_lineage_hash"] = _sha(lineage)

    report = {
        "purpose": "MLB_1IP_EXACT_LINE_EXPANSION_14_5_16_5_UNTOUCHED_2026",
        "cutoff_date": CUT_OFF_DATE,
        "warmup_games": WARMUP_GAMES,
        "test_games": TEST_GAMES,
        "games_attempted": len(games),
        "source_gaps": gaps,
        "source_gap_rate": gap_rate,
        "mature_assignment_rows": len(assignments),
        "expansion_lines": list(EXPANSION_LINES),
        "sentinel_lines": list(SENTINEL_LINES),
        "line_results": line_results,
        "partition_failures": partition_failures,
        "bound_invariant_failures": bound_invariant_failures,
        "gates": {
            "max_gap_rate": MAX_GAP_RATE,
            "min_mature_rows_per_line": MIN_MATURE_ROWS_PER_LINE,
            "max_brier_regression": MAX_BRIER_REGRESSION,
            "max_ece": MAX_ECE,
            "min_auc_gain": MIN_AUC_GAIN,
            "min_probability_std": MIN_PROBABILITY_STD,
        },
        "validation_lineage": lineage,
        "validation_passed": not failures,
        "validation_failures": failures,
        "promotion_status": "REVIEW_REQUIRED" if not failures else "BLOCKED",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }

    out_dir = Path(
        os.environ.get(
            "MLB_1IP_LINE_EXPANSION_OUT",
            "research-output/mlb-1ip-line-expansion",
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "line_expansion_validation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "line_expansion_assignments.json").write_text(
        json.dumps(assignments, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    if failures:
        raise SystemExit("MLB_1IP_LINE_EXPANSION_VALIDATION_BLOCKED:" + ",".join(failures))


if __name__ == "__main__":
    main()
