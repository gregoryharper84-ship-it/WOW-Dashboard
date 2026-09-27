"""Independent publication-readiness replay for the governed MLB 1IP lane.

This experiment does not refit, recalibrate, interpolate, promote, publish, or
execute. It reuses the frozen player-conditioned BF-mixture artifact and the
existing untouched early-2026 chronological replay, expands that replay across
all eight certified half-point lines, and asks one additional publication gate:
for every exact line and BOTH directions, is the empirical hit rate at least as
large as the mean calibrated lower bound produced by the unchanged scorer?

The existing exact-line validation gates retain their original scope: the 14.5
and 16.5 expansion lines are still the only expansion-gated lines. The six
already-certified lines are scored as sentinels here so this publication replay
does not invent a new retrospective AUC-admission rule for them.

The publication lower-bound gate is deliberately strict and has no fitted/tuned
tolerance. If any line/direction fails, production publication remains blocked
and the evidence must be reviewed before any Class-C bound-method challenger is
considered.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from statistics import mean
from typing import Any

from scripts import validate_mlb_1ip_line_expansion_2026 as base

CERTIFIED_LINES = (11.5, 13.5, 14.5, 15.5, 16.5, 17.5, 19.5, 21.5)
EXPANSION_LINES = (14.5, 16.5)
PREVIOUSLY_CERTIFIED_SENTINEL_LINES = (11.5, 13.5, 15.5, 17.5, 19.5, 21.5)
DIRECTIONS = ("MORE", "LESS")
EPSILON = 1e-12
PURPOSE = "MLB_1IP_PUBLICATION_READINESS_LOWER_BOUND_REPLAY_2026"


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _direction_rows(rows: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    actual: list[int] = []
    probability: list[float] = []
    lower_bound: list[float] = []

    for row in rows:
        more_actual = int(row["actual_more"])
        more_probability = float(row["player_probability"])
        more_lower = float(row["player_lower_bound"])
        more_upper = float(row["player_upper_bound"])

        if direction == "MORE":
            actual.append(more_actual)
            probability.append(more_probability)
            lower_bound.append(more_lower)
        elif direction == "LESS":
            actual.append(1 - more_actual)
            probability.append(1.0 - more_probability)
            # Half-point lines have no push. The player-conditioned interval is
            # a translated Wilson interval, so LESS lower == 1 - MORE upper.
            lower_bound.append(1.0 - more_upper)
        else:  # pragma: no cover - guarded by constant directions
            raise ValueError("MLB_1IP_PUBLICATION_DIRECTION_INVALID")

    n = len(actual)
    if n == 0:
        return {
            "n": 0,
            "observed_hit_rate": None,
            "mean_probability": None,
            "mean_calibrated_lower_bound": None,
            "lower_bound_margin": None,
            "lower_bound_unique_count": 0,
            "probability_unique_count": 0,
            "conservative_lower_bound": False,
        }

    observed = sum(actual) / n
    mean_probability = mean(probability)
    mean_lower = mean(lower_bound)
    margin = observed - mean_lower
    return {
        "n": n,
        "observed_hit_rate": observed,
        "mean_probability": mean_probability,
        "mean_calibrated_lower_bound": mean_lower,
        "lower_bound_margin": margin,
        "lower_bound_unique_count": len({round(value, 12) for value in lower_bound}),
        "probability_unique_count": len({round(value, 12) for value in probability}),
        "conservative_lower_bound": margin >= -EPSILON,
    }


def validate_publication_packet(
    assignments: list[dict[str, Any]],
    source_report: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    line_results: dict[str, Any] = {}

    source_lines = tuple(
        float(value)
        for value in source_report.get("validation_lineage", {}).get("validation_lines", [])
    )
    if source_lines != CERTIFIED_LINES:
        failures.append("MLB_1IP_PUBLICATION_EXACT_LINE_SET_MISMATCH")
    if source_report.get("validation_passed") is not True:
        failures.append("MLB_1IP_PUBLICATION_SOURCE_REPLAY_NOT_PASSED")
    if source_report.get("partition_failures") != 0:
        failures.append("MLB_1IP_PUBLICATION_DIRECTION_PARTITION_FAILED")
    if source_report.get("bound_invariant_failures") != 0:
        failures.append("MLB_1IP_PUBLICATION_BOUND_ORDER_FAILED")
    if source_report.get("can_execute") is not False:
        failures.append("MLB_1IP_PUBLICATION_EXECUTION_GOVERNANCE_FAILED")

    for line in CERTIFIED_LINES:
        rows = [row for row in assignments if float(row.get("line")) == line]
        direction_results: dict[str, Any] = {}
        for direction in DIRECTIONS:
            result = _direction_rows(rows, direction)
            direction_results[direction] = result
            if int(result["n"] or 0) < base.MIN_MATURE_ROWS_PER_LINE:
                failures.append(f"MLB_1IP_LINE_{line}_{direction}_MATURE_SAMPLE_INSUFFICIENT")
            if int(result["probability_unique_count"] or 0) < 2:
                failures.append(f"MLB_1IP_LINE_{line}_{direction}_PROBABILITY_COLLAPSE")
            if int(result["lower_bound_unique_count"] or 0) < 2:
                failures.append(f"MLB_1IP_LINE_{line}_{direction}_LOWER_BOUND_COLLAPSE")
            if result["conservative_lower_bound"] is not True:
                failures.append(f"MLB_1IP_LINE_{line}_{direction}_LOWER_BOUND_NOT_CONSERVATIVE")
        line_results[str(line)] = direction_results

    failures = list(dict.fromkeys(failures))
    lineage = {
        "source_validation_lineage_hash": source_report.get("validation_lineage", {}).get("validation_lineage_hash"),
        "aggregate_artifact_checksum": source_report.get("validation_lineage", {}).get("aggregate_artifact_checksum"),
        "bf_artifact_checksum": source_report.get("validation_lineage", {}).get("bf_artifact_checksum"),
        "source_assignments_hash": _sha(assignments),
        "publication_validation_code_sha": os.getenv("GITHUB_SHA") or "LOCAL",
        "certified_lines": list(CERTIFIED_LINES),
        "directions": list(DIRECTIONS),
        "sample_policy": source_report.get("sample_policy"),
        "cutoff_date": source_report.get("cutoff_date"),
    }
    lineage["publication_validation_lineage_hash"] = _sha(lineage)

    return {
        "purpose": PURPOSE,
        "validation_type": "UNTOUCHED_2026_CHRONOLOGICAL_EXACT_LINE_DIRECTION_LOWER_BOUND",
        "certified_lines": list(CERTIFIED_LINES),
        "directions": list(DIRECTIONS),
        "source_expansion_gate_lines": list(EXPANSION_LINES),
        "source_sentinel_lines": list(PREVIOUSLY_CERTIFIED_SENTINEL_LINES),
        "sample_policy": source_report.get("sample_policy"),
        "cutoff_date": source_report.get("cutoff_date"),
        "games_attempted": source_report.get("games_attempted"),
        "source_gap_rate": source_report.get("source_gap_rate"),
        "mature_assignment_rows": len(assignments),
        "line_results": line_results,
        "validation_lineage": lineage,
        "lower_bound_gate": "OBSERVED_HIT_RATE_GTE_MEAN_CALIBRATED_LOWER_BOUND_NO_TUNED_TOLERANCE",
        "model_payload_changed": False,
        "calibration_transform_changed": False,
        "bound_method_changed": False,
        "validation_passed": not failures,
        "validation_failures": failures,
        "promotion_status": "REVIEW_REQUIRED" if not failures else "BLOCKED",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def main() -> None:
    out_dir = Path(os.environ.get(
        "MLB_1IP_PUBLICATION_VALIDATION_OUT",
        "research-output/mlb-1ip-publication-validation",
    ))
    replay_dir = out_dir / "source-replay"
    replay_dir.mkdir(parents=True, exist_ok=True)

    # Reuse the independently reviewed exact-line replay implementation. Preserve
    # its original admission scope (14.5/16.5 expansion only), but score all eight
    # certified lines so the publication lower-bound audit has complete coverage.
    base.EXPANSION_LINES = EXPANSION_LINES
    base.SENTINEL_LINES = PREVIOUSLY_CERTIFIED_SENTINEL_LINES
    base.VALIDATION_LINES = CERTIFIED_LINES
    os.environ["MLB_1IP_LINE_EXPANSION_OUT"] = str(replay_dir)
    base.main()

    source_report = json.loads(
        (replay_dir / "line_expansion_validation_report.json").read_text(encoding="utf-8")
    )
    assignments = json.loads(
        (replay_dir / "line_expansion_assignments.json").read_text(encoding="utf-8")
    )
    report = validate_publication_packet(assignments, source_report)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "publication_validation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    if report["validation_failures"]:
        raise SystemExit(
            "MLB_1IP_PUBLICATION_VALIDATION_BLOCKED:"
            + ",".join(report["validation_failures"])
        )


if __name__ == "__main__":
    main()
