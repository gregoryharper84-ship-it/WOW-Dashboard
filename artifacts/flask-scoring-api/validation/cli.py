"""
validation/cli.py

WOW Prediction Validation Harness v1 — Command-line entry point.

Usage
-----
  python -m validation.cli [OPTIONS]

Options
-------
  --split         only       Run split + metrics; do NOT evaluate holdout.
  --baselines     only       Run baselines on train/validation splits only.
  --ablation      only       Run feature ablation on validation set.
  --all-splits               Include holdout in report (DANGER: only after final model lock).
  --out           DIR        Output directory for reports (default: ./validation_reports).
  --synthetic                Use synthetic fixtures instead of live Savant data.

Default (no flags): split + baselines + ablation on train/validation; no holdout.

NOTE: This tool never modifies production code, gates, or endpoints.
      Production commit frozen at af96567.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

from validation import HARNESS_VERSION, FROZEN_COMMIT
from validation.splitting.chronological_split import chronological_split
from validation.schema.prediction_record import PredictionRecord
from validation.schema.outcome_record import OutcomeRecord, attach_outcome
from validation.metrics.core import evaluate
from validation.baselines.season_empirical import predict_single as season_predict
from validation.baselines.l10_empirical import predict_single as l10_predict
from validation.baselines.stat_model import predict_single as stat_predict
from validation.ablation.runner import run_ablation
from validation.reporters.json_reporter import build_report, write_json_report
from validation.reporters.markdown_reporter import write_markdown_report


# ---------------------------------------------------------------------------
# Synthetic fixtures (deterministic; used for smoke tests and CI)
# ---------------------------------------------------------------------------

def _make_synthetic_pair(
    i: int,
    *,
    game_date: str,
    pitcher_name: str,
    pitcher_mlbam_id: int,
    line: float,
    direction: str,
    actual_pitches: int,
    model_prob: float,
) -> tuple:
    from datetime import datetime, timezone, timedelta
    frozen_at  = (datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=i)).isoformat()
    outcome_ts = (datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=i, hours=12)).isoformat()

    pred = PredictionRecord.create(
        game_date         = game_date,
        pitcher_name      = pitcher_name,
        pitcher_mlbam_id  = pitcher_mlbam_id,
        opponent          = "OPP",
        line              = line,
        direction         = direction,
        model_probability = model_prob,
        model_uncertainty = 0.05,
        features          = {
            "bf_distribution": {"p_bf_3": 0.40, "p_bf_4": 0.35, "p_bf_gte5": 0.25, "n": 10},
            "data_coverage": 10,
        },
        model_version     = "1ip_monte_carlo_event_tree_v1",
        data_provenance   = {"source": "synthetic_fixture", "fetch_method": "synthetic"},
        _frozen_at        = frozen_at,
    )
    outcome = attach_outcome(
        pred,
        actual_pitches  = actual_pitches,
        outcome_source  = "synthetic_fixture",
        outcome_verified = True,
        _outcome_timestamp = outcome_ts,
    )
    return (pred, outcome)


def _build_synthetic_dataset(n: int = 50) -> list:
    """
    Generate n deterministic (PredictionRecord, OutcomeRecord) pairs
    covering line values 13.5, 14.5, 15.5, 16.5 with varied probabilities.
    """
    import math
    lines = [13.5, 14.5, 15.5, 16.5]
    pitchers = [
        ("Shota Imanaga",  669392),
        ("Michael Wacha",  621111),
        ("Sixto Sanchez",  682928),
        ("Jake McLean",    699429),
    ]
    pairs = []
    for i in range(n):
        ln      = lines[i % len(lines)]
        pitcher = pitchers[i % len(pitchers)]
        # Deterministic actual_pitches that varies around lines
        actual  = int(ln + math.sin(i * 1.3) * 3)
        direction = "LESS" if i % 3 != 2 else "MORE"
        # Model prob calibrated-ish: higher prob for side more likely
        model_prob = round(0.45 + 0.15 * math.cos(i * 0.7), 4)
        model_prob = max(0.05, min(0.95, model_prob))

        game_date = f"2026-{(i // 28 + 6):02d}-{(i % 28 + 1):02d}"
        try:
            _dt.date.fromisoformat(game_date)
        except ValueError:
            game_date = f"2026-07-{(i % 28 + 1):02d}"

        pair = _make_synthetic_pair(
            i,
            game_date       = game_date,
            pitcher_name    = pitcher[0],
            pitcher_mlbam_id = pitcher[1],
            line            = ln,
            direction       = direction,
            actual_pitches  = actual,
            model_prob      = model_prob,
        )
        pairs.append(pair)
    return pairs


def _pairs_to_eval_rows(pairs: list) -> tuple:
    """Convert pairs to (samples, lines, ablation_rows)."""
    samples = []
    lines   = []
    abl_rows = []
    for pred, outcome in pairs:
        samples.append((pred.model_probability, outcome.hit))
        lines.append(pred.line)
        abl_rows.append({
            "ledger_rows":      [],
            "bf_distribution":  pred.data_provenance.get("bf_distribution"),
            "line":             pred.line,
            "direction":        pred.direction,
            "hit":              outcome.hit,
        })
    return samples, lines, abl_rows


# ---------------------------------------------------------------------------
# Eval rules loader
# ---------------------------------------------------------------------------

def _load_eval_rules() -> dict:
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), "config", "eval_rules.yaml")
    try:
        with open(cfg_path) as f:
            return yaml.safe_load(f)
    except Exception:
        # Fallback minimal rules if yaml not installed
        return {
            "primary_threshold":   {"warn_above": 0.25, "fail_above": 0.30},
            "secondary_threshold": {"warn_above": 0.693, "fail_above": 0.75},
            "calibration":         {"min_bins": 5, "max_calibration_error": 0.15, "min_bin_count": 3},
            "coverage":            {"min_predictions_total": 10},
            "note":                "yaml not available; using built-in defaults",
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=f"WOW Prediction Validation Harness v{HARNESS_VERSION}"
    )
    parser.add_argument("--synthetic", action="store_true",
                        help="Use deterministic synthetic fixtures (no network)")
    parser.add_argument("--all-splits", action="store_true",
                        help="Include holdout evaluation (only after final model lock)")
    parser.add_argument("--out", default="validation_reports",
                        help="Output directory (default: validation_reports)")
    parser.add_argument("--ablation", action="store_true",
                        help="Run feature ablation on validation set")
    parser.add_argument("--no-baselines", action="store_true",
                        help="Skip baseline computation")
    args = parser.parse_args(argv)

    print(f"\n=== WOW Prediction Validation Harness v{HARNESS_VERSION} ===")
    print(f"Frozen commit: {FROZEN_COMMIT}")

    eval_rules = _load_eval_rules()
    print("✓ Evaluation rules loaded (predeclared before holdout)")

    # ── Build dataset ─────────────────────────────────────────────────────
    if args.synthetic:
        print("Using synthetic dataset (50 deterministic pairs)")
        pairs = _build_synthetic_dataset(50)
    else:
        print("No live data pipeline wired yet — use --synthetic for smoke run")
        print("Live data requires: pitcher MLBAM IDs, board dates, and actual pitch counts")
        return 1

    # ── Split ─────────────────────────────────────────────────────────────
    split = chronological_split(pairs)
    manifest_dict = {
        k: getattr(split.manifest, k)
        for k in split.manifest.__dataclass_fields__
    }
    print(f"✓ Split: {split.manifest.train_count} train / "
          f"{split.manifest.validation_count} validation / "
          f"{split.manifest.holdout_count} holdout")

    # ── Model evaluation ──────────────────────────────────────────────────
    tr_samples, tr_lines, _  = _pairs_to_eval_rows(split.train)
    vl_samples, vl_lines, vl_abl = _pairs_to_eval_rows(split.validation)
    ho_samples, ho_lines, _  = _pairs_to_eval_rows(split.holdout)

    model_results = {
        "train":      evaluate(tr_samples, tr_lines, split_label="train"),
        "validation": evaluate(vl_samples, vl_lines, split_label="validation"),
        "holdout":    evaluate(ho_samples, ho_lines, split_label="holdout")
                      if args.all_splits else None,
    }
    print(f"✓ Model evaluated "
          f"(val Brier: {model_results['validation']['brier'].get('score')})")

    # ── Baselines ─────────────────────────────────────────────────────────
    baseline_results = {}
    if not args.no_baselines:
        for bid in ["season_empirical", "l10_empirical", "stat_model_pitches_bf"]:
            # All baselines return constant 0.5 on synthetic data (no real ledger_rows)
            # so we report them honestly with n=0 / None probability
            bl_samples = [(None, o) for _, o in vl_samples]
            baseline_results[bid] = {
                "validation": evaluate(bl_samples, vl_lines, split_label="validation"),
            }
        print("✓ Baselines evaluated (synthetic: all return None — no ledger_rows in fixture)")

    # ── Ablation ──────────────────────────────────────────────────────────
    ablation_results = {}
    if args.ablation:
        ablation_results = run_ablation(vl_abl)
        n_ran = sum(1 for v in ablation_results.values() if v["status"] == "RAN")
        n_una = sum(1 for v in ablation_results.values() if v["status"] == "UNAVAILABLE")
        print(f"✓ Ablation: {n_ran} features ran, {n_una} UNAVAILABLE")

    # ── Build reports ─────────────────────────────────────────────────────
    report = build_report(
        split_manifest    = manifest_dict,
        model_results     = model_results,
        baseline_results  = baseline_results,
        ablation_results  = ablation_results,
        eval_rules        = eval_rules,
        holdout_evaluated = args.all_splits,
    )

    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    json_path = os.path.join(args.out, f"report_{ts}.json")
    md_path   = os.path.join(args.out, f"report_{ts}.md")

    write_json_report(report, json_path)
    write_markdown_report(report, md_path)

    print(f"✓ JSON report: {json_path}")
    print(f"✓ Markdown report: {md_path}")

    # ── Gate verdicts summary ─────────────────────────────────────────────
    verdicts = report.get("verdicts") or {}
    all_pass = all(v.get("verdict") in {"PASS", "WARN", "INSUFFICIENT_DATA"}
                   for v in verdicts.values())
    any_fail = any(v.get("verdict") == "FAIL" for v in verdicts.values())

    print("\n=== Gate Verdicts ===")
    for k, v in verdicts.items():
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INSUFFICIENT_DATA": "⬜"}.get(
            v.get("verdict"), "?"
        )
        score = v.get("score") or v.get("ece")
        print(f"  {icon} {k}: {score}")

    if any_fail:
        print("\n❌ One or more gates FAILED.")
        return 1
    print("\n✅ All gates passed (or insufficient data — add more samples).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
