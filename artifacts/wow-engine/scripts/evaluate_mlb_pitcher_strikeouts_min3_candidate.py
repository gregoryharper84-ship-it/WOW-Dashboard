"""Governed follow-up gate for the MLB strikeout three-start candidate.

Consumes the same chronological replay code and certified fitted artifact as
``evaluate_mlb_pitcher_strikeouts_low_start.py``. The purpose is to prevent a
combined 1-9 result from hiding the materially weaker 1-2-start calibration.
No production threshold, artifact, probability, calibration, or routing is
changed here. can_execute=false.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import evaluate_mlb_pitcher_strikeouts_low_start as base


def build_report() -> dict:
    artifact = json.loads(base.ARTIFACT_PATH.read_text(encoding="utf-8"))
    training_report = json.loads(base.TRAINING_REPORT_PATH.read_text(encoding="utf-8"))
    baseline = training_report["baseline_constants"]
    baseline_pmf = base._nb_pmf(
        float(baseline["mu"]),
        float(baseline["r"]),
        int(artifact["max_support_k"]),
    )
    all_rows = [
        row
        for row in base._build_rows()
        if row.game_date >= base.TEST_START
        and row.n_prior_starts >= int(artifact["min_prior_starts"])
    ]
    candidate_3_to_9 = base._evaluate(
        (row for row in all_rows if 3 <= row.n_prior_starts <= 9),
        artifact,
        baseline_pmf,
    )
    one_to_two_holdout = base._evaluate(
        (row for row in all_rows if 1 <= row.n_prior_starts <= 2),
        artifact,
        baseline_pmf,
    )
    incumbent_10_plus = base._evaluate(
        (row for row in all_rows if row.n_prior_starts >= 10),
        artifact,
        baseline_pmf,
    )

    candidate_rows = int(candidate_3_to_9.get("rows", 0))
    candidate_beats_baseline = bool(candidate_3_to_9.get("model_beats_baseline_nll")) and bool(
        candidate_3_to_9.get("model_beats_baseline_brier")
    )
    candidate_calibration_gap = float(candidate_3_to_9.get("absolute_calibration_gap", 1.0))
    candidate_ece = float(candidate_3_to_9.get("ece_10_bin", 1.0))
    candidate_gate_pass = (
        candidate_rows >= 150
        and candidate_beats_baseline
        and candidate_calibration_gap <= 0.03
        and candidate_ece <= 0.08
    )

    holdout_gap = float(one_to_two_holdout.get("absolute_calibration_gap", 1.0))
    holdout_ece = float(one_to_two_holdout.get("ece_10_bin", 1.0))
    one_to_two_remains_blocked = holdout_gap > 0.08 or holdout_ece > 0.10

    return {
        "experiment": "MLB_PITCHER_STRIKEOUT_MIN3_THRESHOLD_CANDIDATE_V1",
        "classification": "CLASS_C_CHALLENGER_ONLY",
        "production_change": False,
        "can_execute": False,
        "candidate_min_prior_starts": 3,
        "current_production_min_prior_starts": 10,
        "candidate_3_to_9": candidate_3_to_9,
        "one_to_two_holdout": one_to_two_holdout,
        "incumbent_10_plus": incumbent_10_plus,
        "promotion_evidence_gate": {
            "minimum_candidate_rows": 150,
            "maximum_absolute_calibration_gap": 0.03,
            "maximum_ece_10_bin": 0.08,
            "has_minimum_rows": candidate_rows >= 150,
            "beats_naive_baseline_on_nll_and_brier": candidate_beats_baseline,
            "calibration_gap_within_limit": candidate_calibration_gap <= 0.03,
            "ece_within_limit": candidate_ece <= 0.08,
            "candidate_gate_pass": candidate_gate_pass,
            "one_to_two_remains_blocked": one_to_two_remains_blocked,
            "status": (
                "MIN3_CANDIDATE_EARNED_GOVERNED_REVIEW"
                if candidate_gate_pass and one_to_two_remains_blocked
                else "KEEP_CURRENT_PRODUCTION_GATE"
            ),
        },
        "notes": [
            "This is evidence for review, not authority to change production.",
            "The 1-2-start cohort is evaluated independently and cannot inherit the 3-9 cohort decision.",
            "Any production floor change requires a separate governed Class C PR, full regression, review, deployment, and production verification.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
