"""Class-C challenger for MLB 1IP directional lower-bound construction.

This experiment does NOT change the fitted PMF, player-conditioned point probability,
exact-line certification, production manifest, registry, publication status, ranking,
or execution authority.

Motivation:
The active player-conditioned scorer translates the aggregate interval by the
player-conditioning probability delta. Publication replay #945 showed strict
conservatism failures for 11.5 LESS and 19.5 LESS. This challenger asks whether a
direction/line-specific empirical conservatism correction, estimated only on an
earlier calibration partition, remains conservative on a later untouched holdout.

The correction is deliberately not a universal haircut. For each exact line and
direction independently:
  correction = max(0, mean_original_lower_bound - Wilson95Lower(observed_hit_rate))
on the calibration partition. The correction is then frozen and subtracted from
that line/direction's lower bound on the later holdout.

A passing experiment is evidence for review only. It is not authority to promote.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from statistics import mean
from typing import Any

from scripts import validate_mlb_1ip_line_expansion_2026 as base

CERTIFIED_LINES = (11.5, 13.5, 14.5, 15.5, 16.5, 17.5, 19.5, 21.5)
EXPANSION_LINES = (14.5, 16.5)
SENTINEL_LINES = (11.5, 13.5, 15.5, 17.5, 19.5, 21.5)
DIRECTIONS = ("MORE", "LESS")
CALIBRATION_GAMES = 300
MIN_HOLDOUT_GAMES = 250
MIN_ROWS_PER_DIRECTION = 250
Z_ONE_SIDED_95 = 1.6448536269514722
EPSILON = 1e-12
PURPOSE = "MLB_1IP_DIRECTIONAL_BOUND_CHALLENGER_2026"


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _wilson_lower(hits: int, n: int, z: float = Z_ONE_SIDED_95) -> float:
    if n <= 0:
        return 0.0
    p = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n))
    return max(0.0, min(1.0, (centre - radius) / denom))


def _direction_values(row: dict[str, Any], direction: str) -> tuple[int, float, float]:
    more_actual = int(row["actual_more"])
    more_probability = float(row["player_probability"])
    more_lower = float(row["player_lower_bound"])
    more_upper = float(row["player_upper_bound"])
    if direction == "MORE":
        return more_actual, more_probability, more_lower
    if direction == "LESS":
        return 1 - more_actual, 1.0 - more_probability, 1.0 - more_upper
    raise ValueError("MLB_1IP_DIRECTION_INVALID")


def _partition_games(assignments: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
    ordered = sorted(
        {(str(row["event_time"]), int(row["game_pk"])) for row in assignments},
        key=lambda item: (item[0], item[1]),
    )
    minimum_required = CALIBRATION_GAMES + MIN_HOLDOUT_GAMES
    if len(ordered) < minimum_required:
        raise RuntimeError(
            "MLB_1IP_CHALLENGER_GAME_SAMPLE_INSUFFICIENT "
            f"n={len(ordered)} need_at_least={minimum_required}"
        )
    calibration = {pk for _, pk in ordered[:CALIBRATION_GAMES]}
    # Use every chronologically later unique game as the untouched holdout. The
    # source replay currently yields fewer than the originally assumed 700 unique
    # games because multiple player rows can belong to the same game. The holdout
    # size is therefore determined only by source availability, never outcomes.
    holdout = {pk for _, pk in ordered[CALIBRATION_GAMES:]}
    if calibration & holdout:
        raise RuntimeError("MLB_1IP_CHALLENGER_GAME_PARTITION_OVERLAP")
    return calibration, holdout


def _fit_correction(rows: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    values = [_direction_values(row, direction) for row in rows]
    actual = [item[0] for item in values]
    lower = [item[2] for item in values]
    n = len(values)
    hits = sum(actual)
    observed = hits / n if n else None
    mean_lower = mean(lower) if lower else None
    target = _wilson_lower(hits, n) if n else None
    correction = max(0.0, float(mean_lower) - float(target)) if n else None
    return {
        "n": n,
        "hits": hits,
        "observed_hit_rate": observed,
        "mean_original_lower_bound": mean_lower,
        "wilson95_one_sided_lower_hit_rate": target,
        "frozen_directional_correction": correction,
    }


def _evaluate_holdout(
    rows: list[dict[str, Any]],
    direction: str,
    correction: float,
) -> dict[str, Any]:
    values = [_direction_values(row, direction) for row in rows]
    actual = [item[0] for item in values]
    probabilities = [item[1] for item in values]
    original_lower = [item[2] for item in values]
    adjusted_lower = [max(0.0, value - correction) for value in original_lower]
    n = len(values)
    observed = sum(actual) / n if n else None
    mean_probability = mean(probabilities) if probabilities else None
    mean_original = mean(original_lower) if original_lower else None
    mean_adjusted = mean(adjusted_lower) if adjusted_lower else None
    original_margin = float(observed) - float(mean_original) if n else None
    adjusted_margin = float(observed) - float(mean_adjusted) if n else None
    return {
        "n": n,
        "observed_hit_rate": observed,
        "mean_probability": mean_probability,
        "mean_original_lower_bound": mean_original,
        "mean_challenger_lower_bound": mean_adjusted,
        "original_lower_bound_margin": original_margin,
        "challenger_lower_bound_margin": adjusted_margin,
        "challenger_conservative": bool(n and adjusted_margin >= -EPSILON),
        "point_probability_changed": False,
    }


def main() -> None:
    out_dir = Path(os.environ.get(
        "MLB_1IP_BOUND_CHALLENGER_OUT",
        "research-output/mlb-1ip-directional-bound-challenger",
    ))
    replay_dir = out_dir / "source-replay"
    replay_dir.mkdir(parents=True, exist_ok=True)

    base.EXPANSION_LINES = EXPANSION_LINES
    base.SENTINEL_LINES = SENTINEL_LINES
    base.VALIDATION_LINES = CERTIFIED_LINES
    os.environ["MLB_1IP_LINE_EXPANSION_OUT"] = str(replay_dir)
    base.main()

    source_report = json.loads(
        (replay_dir / "line_expansion_validation_report.json").read_text(encoding="utf-8")
    )
    assignments = json.loads(
        (replay_dir / "line_expansion_assignments.json").read_text(encoding="utf-8")
    )
    calibration_games, holdout_games = _partition_games(assignments)

    failures: list[str] = []
    if source_report.get("validation_passed") is not True:
        failures.append("MLB_1IP_CHALLENGER_SOURCE_REPLAY_NOT_PASSED")
    if source_report.get("partition_failures") != 0:
        failures.append("MLB_1IP_CHALLENGER_DIRECTION_PARTITION_FAILED")
    if source_report.get("bound_invariant_failures") != 0:
        failures.append("MLB_1IP_CHALLENGER_BOUND_ORDER_FAILED")
    if source_report.get("can_execute") is not False:
        failures.append("MLB_1IP_CHALLENGER_EXECUTION_GOVERNANCE_FAILED")

    results: dict[str, Any] = {}
    for line in CERTIFIED_LINES:
        line_rows = [row for row in assignments if float(row["line"]) == line]
        cal_rows = [row for row in line_rows if int(row["game_pk"]) in calibration_games]
        hold_rows = [row for row in line_rows if int(row["game_pk"]) in holdout_games]
        direction_results: dict[str, Any] = {}
        for direction in DIRECTIONS:
            fitted = _fit_correction(cal_rows, direction)
            correction = float(fitted["frozen_directional_correction"] or 0.0)
            evaluated = _evaluate_holdout(hold_rows, direction, correction)
            direction_results[direction] = {
                "calibration": fitted,
                "holdout": evaluated,
            }
            if int(fitted["n"] or 0) < MIN_ROWS_PER_DIRECTION:
                failures.append(f"MLB_1IP_CHALLENGER_{line}_{direction}_CAL_SAMPLE_INSUFFICIENT")
            if int(evaluated["n"] or 0) < MIN_ROWS_PER_DIRECTION:
                failures.append(f"MLB_1IP_CHALLENGER_{line}_{direction}_HOLDOUT_SAMPLE_INSUFFICIENT")
            if evaluated["challenger_conservative"] is not True:
                failures.append(f"MLB_1IP_CHALLENGER_{line}_{direction}_HOLDOUT_NOT_CONSERVATIVE")
            if evaluated["point_probability_changed"] is not False:
                failures.append(f"MLB_1IP_CHALLENGER_{line}_{direction}_POINT_PROBABILITY_CHANGED")
        results[str(line)] = direction_results

    failures = list(dict.fromkeys(failures))
    lineage = {
        "source_validation_lineage_hash": source_report.get("validation_lineage", {}).get("validation_lineage_hash"),
        "aggregate_artifact_checksum": source_report.get("validation_lineage", {}).get("aggregate_artifact_checksum"),
        "bf_artifact_checksum": source_report.get("validation_lineage", {}).get("bf_artifact_checksum"),
        "source_assignments_hash": _sha(assignments),
        "challenger_code_sha": os.getenv("GITHUB_SHA") or "LOCAL",
        "calibration_game_pks_hash": _sha(sorted(calibration_games)),
        "holdout_game_pks_hash": _sha(sorted(holdout_games)),
        "certified_lines": list(CERTIFIED_LINES),
        "directions": list(DIRECTIONS),
        "correction_method": "LINE_DIRECTION_EMPIRICAL_WILSON95_ONE_SIDED_CONSERVATISM_V1",
        "holdout_selection": "ALL_CHRONOLOGICALLY_LATER_UNIQUE_GAMES_AFTER_FIXED_300_GAME_CALIBRATION",
    }
    lineage["challenger_lineage_hash"] = _sha(lineage)

    report = {
        "purpose": PURPOSE,
        "change_class": "CLASS_C_CHALLENGER_ONLY",
        "challenger_method": "LINE_DIRECTION_EMPIRICAL_WILSON95_ONE_SIDED_CONSERVATISM_V1",
        "calibration_games": len(calibration_games),
        "holdout_games": len(holdout_games),
        "minimum_holdout_games": MIN_HOLDOUT_GAMES,
        "certified_lines": list(CERTIFIED_LINES),
        "directions": list(DIRECTIONS),
        "results": results,
        "validation_lineage": lineage,
        "model_payload_changed": False,
        "point_probability_math_changed": False,
        "bound_method_changed": True,
        "production_registry_mutated": False,
        "production_manifest_mutated": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
        "validation_passed": not failures,
        "validation_failures": failures,
        "recommendation": "INDEPENDENT_REVIEW_REQUIRED" if not failures else "CHALLENGER_REJECTED",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "directional_bound_challenger_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    if failures:
        raise SystemExit("MLB_1IP_DIRECTIONAL_BOUND_CHALLENGER_BLOCKED:" + ",".join(failures))


if __name__ == "__main__":
    main()
