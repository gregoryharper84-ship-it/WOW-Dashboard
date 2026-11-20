"""Shared champion-vs-challenger evaluator (redesign chunk 8, R9/R10).

Evidence only. Input is one row per settled forecast with the incumbent's and
the challenger's probability for the same outcome. Output is a standard
evidence packet:

- paired Brier and log-loss differences (challenger minus incumbent) with a
  seeded bootstrap CI,
- calibration bias and ECE for each model,
- lower-bound reliability for each model,
- previously-correct incumbent rows the challenger harms (and wrong rows it fixes).

Historical replay and prospective holdout are evaluated separately and never
pooled. With no approved promotion policy the recommendation is always
``SHADOW_ONLY_NO_APPROVED_POLICY``. ``propose_policy`` derives *proposed*
criteria from the incumbent's own historical evidence; a proposal has no
effect until governance approves it. Nothing here fits, changes, publishes or
promotes a probability. can_execute is always false.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

EVALUATOR_VERSION = "V17_CHALLENGER_EVALUATOR_V1"
HISTORICAL = "HISTORICAL_REPLAY"
PROSPECTIVE = "PROSPECTIVE_HOLDOUT"
COHORTS = (HISTORICAL, PROSPECTIVE)
NO_POLICY = "SHADOW_ONLY_NO_APPROVED_POLICY"
CI_LEVEL = 0.95
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 17
ECE_BINS = 10
_INERT = {
    "can_execute": False,
    "probability_publishable": False,
    "automatic_promotion": False,
    "promotion_attempted": False,
}


class EvaluatorInputError(ValueError):
    """Rows are missing, malformed or not comparable; the caller must fail closed."""


@dataclass(frozen=True)
class PairedRow:
    row_id: str
    market_family: str
    cohort: str
    incumbent_p: float
    challenger_p: float | None
    outcome: int
    forecast_at: str
    settled_at: str
    incumbent_lower: float | None = None
    challenger_lower: float | None = None


def _ts(value: str, field: str, row_id: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluatorInputError(f"{row_id}: {field} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EvaluatorInputError(f"{row_id}: {field} has no timezone")
    return parsed


def _prob(value: Any, field: str, row_id: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EvaluatorInputError(f"{row_id}: {field} is not a finite number")
    if not 0.0 < float(value) < 1.0:
        raise EvaluatorInputError(f"{row_id}: {field} outside 0 < p < 1")
    return float(value)


def validate_rows(raw_rows: Sequence[Mapping[str, Any]], *, require_challenger: bool = True) -> list[PairedRow]:
    if not raw_rows:
        raise EvaluatorInputError("no rows")
    rows: list[PairedRow] = []
    seen: set[str] = set()
    families: set[str] = set()
    for raw in raw_rows:
        row_id = str(raw.get("row_id") or "").strip()
        if not row_id:
            raise EvaluatorInputError("row without row_id")
        if row_id in seen:
            raise EvaluatorInputError(f"{row_id}: duplicate row_id")
        seen.add(row_id)
        cohort = raw.get("cohort")
        if cohort not in COHORTS:
            raise EvaluatorInputError(f"{row_id}: cohort must be one of {COHORTS}")
        outcome = raw.get("outcome")
        if isinstance(outcome, bool) or outcome not in (0, 1):
            # Pushes, voids and unsettled rows are excluded upstream, never coerced.
            raise EvaluatorInputError(f"{row_id}: outcome must be settled 0 or 1")
        if _ts(raw.get("forecast_at"), "forecast_at", row_id) >= _ts(raw.get("settled_at"), "settled_at", row_id):
            raise EvaluatorInputError(f"{row_id}: forecast_at must precede settled_at")
        family = str(raw.get("market_family") or "").strip()
        if not family:
            raise EvaluatorInputError(f"{row_id}: market_family missing")
        families.add(family)
        rows.append(
            PairedRow(
                row_id=row_id,
                market_family=family,
                cohort=cohort,
                incumbent_p=_prob(raw.get("incumbent_p"), "incumbent_p", row_id),
                challenger_p=_prob(raw.get("challenger_p"), "challenger_p", row_id, optional=not require_challenger),
                outcome=int(outcome),
                forecast_at=str(raw["forecast_at"]),
                settled_at=str(raw["settled_at"]),
                incumbent_lower=_prob(raw.get("incumbent_lower"), "incumbent_lower", row_id, optional=True),
                challenger_lower=_prob(raw.get("challenger_lower"), "challenger_lower", row_id, optional=True),
            )
        )
    if len(families) != 1:
        raise EvaluatorInputError(f"one market family per packet, got {sorted(families)}")
    return rows


def _losses(p: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    brier = (p - y) ** 2
    logloss = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    return brier, logloss


def _paired(diff: np.ndarray) -> dict[str, float]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, diff.size, size=(BOOTSTRAP_RESAMPLES, diff.size))
    means = diff[idx].mean(axis=1)
    alpha = (1 - CI_LEVEL) / 2
    return {
        "mean": float(diff.mean()),
        "sd": float(diff.std(ddof=1)) if diff.size > 1 else 0.0,
        "ci_low": float(np.quantile(means, alpha)),
        "ci_high": float(np.quantile(means, 1 - alpha)),
    }


def _ece(p: np.ndarray, y: np.ndarray) -> float:
    bins = np.minimum((p * ECE_BINS).astype(int), ECE_BINS - 1)
    total = 0.0
    for b in range(ECE_BINS):
        mask = bins == b
        if mask.any():
            total += mask.sum() / p.size * abs(p[mask].mean() - y[mask].mean())
    return float(total)


def _lower_bound(lowers: list[float | None], y: np.ndarray) -> dict[str, Any]:
    pairs = [(lb, int(o)) for lb, o in zip(lowers, y) if lb is not None]
    if not pairs:
        return {"n": 0, "status": "NO_LOWER_BOUNDS"}
    lb = np.array([p[0] for p in pairs])
    out = np.array([p[1] for p in pairs], dtype=float)
    bins = np.minimum((lb * ECE_BINS).astype(int), ECE_BINS - 1)
    shortfalls = []
    for b in range(ECE_BINS):
        mask = bins == b
        if mask.any() and out[mask].mean() < lb[mask].mean():
            shortfalls.append(
                {"bin": b, "n": int(mask.sum()), "mean_lower": float(lb[mask].mean()), "hit_rate": float(out[mask].mean())}
            )
    return {
        "n": int(lb.size),
        "mean_lower": float(lb.mean()),
        "hit_rate": float(out.mean()),
        "margin": float(out.mean() - lb.mean()),
        "shortfall_bins": shortfalls,
    }


def _cohort_metrics(rows: list[PairedRow]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "status": "EMPTY"}
    y = np.array([r.outcome for r in rows], dtype=float)
    pi = np.array([r.incumbent_p for r in rows])
    pc = np.array([r.challenger_p for r in rows])
    bi, li = _losses(pi, y)
    bc, lc = _losses(pc, y)
    inc_correct = (pi > 0.5) == (y == 1)
    ch_correct = (pc > 0.5) == (y == 1)
    harmed = inc_correct & ~ch_correct
    fixed = ~inc_correct & ch_correct
    worse_on_correct = inc_correct & (lc > li)
    n_inc_correct = int(inc_correct.sum())
    return {
        "n": int(y.size),
        "base_rate": float(y.mean()),
        "brier": {"incumbent": float(bi.mean()), "challenger": float(bc.mean()), "paired_diff": _paired(bc - bi)},
        "log_loss": {"incumbent": float(li.mean()), "challenger": float(lc.mean()), "paired_diff": _paired(lc - li)},
        "calibration": {
            "incumbent": {"bias": float(pi.mean() - y.mean()), "ece": _ece(pi, y)},
            "challenger": {"bias": float(pc.mean() - y.mean()), "ece": _ece(pc, y)},
        },
        "lower_bound": {
            "incumbent": _lower_bound([r.incumbent_lower for r in rows], y),
            "challenger": _lower_bound([r.challenger_lower for r in rows], y),
        },
        "harm": {
            "incumbent_correct_rows": n_inc_correct,
            "flipped_to_wrong": int(harmed.sum()),
            "flipped_to_wrong_rate": float(harmed.sum() / n_inc_correct) if n_inc_correct else 0.0,
            "worse_log_loss_on_correct_rows": int(worse_on_correct.sum()),
            "wrong_rows_fixed": int(fixed.sum()),
            "harmed_row_ids": sorted(r.row_id for r, h in zip(rows, harmed) if h)[:50],
        },
    }


def _input_hash(rows: list[PairedRow]) -> str:
    canonical = json.dumps([asdict(r) for r in sorted(rows, key=lambda r: r.row_id)], sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def evaluate(raw_rows: Sequence[Mapping[str, Any]], *, policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    # Canonical order: the bootstrap and float sums must not depend on input order.
    rows = sorted(validate_rows(raw_rows), key=lambda r: r.row_id)
    cohorts = {c: _cohort_metrics([r for r in rows if r.cohort == c]) for c in COHORTS}
    packet: dict[str, Any] = {
        "evaluator_version": EVALUATOR_VERSION,
        "market_family": rows[0].market_family,
        "input_hash": _input_hash(rows),
        "method": {"ci_level": CI_LEVEL, "bootstrap_resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED, "bins": ECE_BINS},
        "cohorts": cohorts,
        **_INERT,
    }
    packet.update(apply_policy(packet, policy))
    return packet


def _metric(packet: Mapping[str, Any], cohort: str, path: str) -> float | None:
    node: Any = packet["cohorts"].get(cohort, {})
    for key in path.split("."):
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return float(node) if isinstance(node, (int, float)) and not isinstance(node, bool) else None


_COMPARATORS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


def apply_policy(packet: Mapping[str, Any], policy: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a recommendation. Never promotes; a governed reviewer acts on it."""
    if not policy or policy.get("status") != "APPROVED" or not policy.get("approved_by") or not policy.get("policy_id"):
        return {"policy": {"status": "NONE"}, "recommendation": NO_POLICY}
    ref = {"status": "APPROVED", "policy_id": policy["policy_id"], "approved_by": policy["approved_by"]}
    if policy.get("market_family") != packet["market_family"]:
        return {"policy": {**ref, "error": "MARKET_FAMILY_MISMATCH"}, "recommendation": NO_POLICY}
    min_n = policy.get("min_prospective_n")
    prospective_n = packet["cohorts"][PROSPECTIVE].get("n", 0)
    if not isinstance(min_n, int) or isinstance(min_n, bool) or min_n < 1:
        return {"policy": {**ref, "error": "MIN_PROSPECTIVE_N_MISSING"}, "recommendation": NO_POLICY}
    if prospective_n < min_n:
        return {"policy": ref, "recommendation": "EXTEND_SHADOW", "reason": f"prospective n {prospective_n} < {min_n}"}
    results = []
    for crit in policy.get("criteria") or []:
        value = _metric(packet, crit.get("cohort", PROSPECTIVE), str(crit.get("metric", "")))
        op = _COMPARATORS.get(crit.get("op"))
        target = crit.get("value")
        ok = value is not None and op is not None and isinstance(target, (int, float)) and op(value, float(target))
        results.append({**crit, "observed": value, "passed": bool(ok)})
    if not results:
        return {"policy": {**ref, "error": "NO_CRITERIA"}, "recommendation": NO_POLICY}
    passed = all(r["passed"] for r in results)
    return {
        "policy": ref,
        "criteria_results": results,
        "recommendation": "PROMOTE_RECOMMENDED_FOR_GOVERNED_REVIEW" if passed else "REJECT_RECOMMENDED",
    }


def propose_policy(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Derive *proposed* criteria from the incumbent's historical evidence.

    Every value is computed from the packet; nothing is a constant chosen here
    except the stated CI level and power. The proposal is inert until approved.
    """
    hist = packet["cohorts"][HISTORICAL]
    if hist.get("n", 0) < 2:
        return {"status": "INSUFFICIENT_HISTORICAL_EVIDENCE", "market_family": packet["market_family"], **_INERT}
    inc_bias = abs(hist["calibration"]["incumbent"]["bias"])
    inc_ece = hist["calibration"]["incumbent"]["ece"]
    diff = hist["brier"]["paired_diff"]
    z_alpha, z_power = 1.959963984540054, 0.8416212335729143  # 95% two-sided, 80% power
    min_n = None
    if diff["sd"] > 0 and diff["mean"] != 0:
        min_n = int(math.ceil(((z_alpha + z_power) * diff["sd"] / abs(diff["mean"])) ** 2))
    criteria = [
        {"cohort": PROSPECTIVE, "metric": "brier.paired_diff.ci_high", "op": "<", "value": 0.0,
         "derived_from": "challenger must beat incumbent on paired Brier at the stated CI level"},
        {"cohort": PROSPECTIVE, "metric": "calibration.challenger.ece", "op": "<=", "value": inc_ece,
         "derived_from": "incumbent historical ECE"},
        {"cohort": PROSPECTIVE, "metric": "log_loss.paired_diff.mean", "op": "<=", "value": 0.0,
         "derived_from": "no paired log-loss regression"},
    ]
    inc_lb = hist["lower_bound"]["incumbent"]
    if inc_lb.get("n", 0):
        criteria.append({"cohort": PROSPECTIVE, "metric": "lower_bound.challenger.margin", "op": ">=", "value": 0.0,
                         "derived_from": "calibrated lower-bound validity: hit rate >= mean lower bound"})
    return {
        "status": "PROPOSED_UNAPPROVED",
        "requires": "governance approval (WOW/Greg) before any use",
        "market_family": packet["market_family"],
        "source_input_hash": packet["input_hash"],
        "min_prospective_n": min_n,
        "min_prospective_n_derived_from": "historical paired Brier diff mean/SD at 95% two-sided, 80% power" if min_n else "UNDERIVABLE_REQUIRES_GOVERNANCE_VALUE",
        "reference": {"incumbent_abs_bias": inc_bias, "incumbent_ece": inc_ece},
        "criteria": criteria,
        **_INERT,
    }


def _model_metrics(p: np.ndarray, y: np.ndarray, lowers: list[float | None]) -> dict[str, Any]:
    brier, logloss = _losses(p, y)
    return {
        "brier": float(brier.mean()),
        "brier_row_sd": float(brier.std(ddof=1)) if brier.size > 1 else 0.0,
        "log_loss": float(logloss.mean()),
        "bias": float(p.mean() - y.mean()),
        "ece": _ece(p, y),
        "lower_bound": _lower_bound(lowers, y),
    }


def incumbent_baseline(raw_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Incumbent-only evidence when no settled challenger rows exist yet.

    Same validation as ``evaluate`` except the challenger probability may be
    absent. Used to propose (never approve) a promotion policy.
    """
    rows = sorted(validate_rows(raw_rows, require_challenger=False), key=lambda r: r.row_id)
    cohorts: dict[str, Any] = {}
    for cohort in COHORTS:
        subset = [r for r in rows if r.cohort == cohort]
        if not subset:
            cohorts[cohort] = {"n": 0, "status": "EMPTY"}
            continue
        y = np.array([r.outcome for r in subset], dtype=float)
        p = np.array([r.incumbent_p for r in subset])
        cohorts[cohort] = {
            "n": int(y.size),
            "base_rate": float(y.mean()),
            "incumbent": _model_metrics(p, y, [r.incumbent_lower for r in subset]),
        }
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "kind": "INCUMBENT_BASELINE",
        "market_family": rows[0].market_family,
        "input_hash": _input_hash(rows),
        "method": {"bins": ECE_BINS},
        "cohorts": cohorts,
        **_INERT,
    }


def propose_policy_from_baseline(baseline: Mapping[str, Any], *, reference_cohort: str = PROSPECTIVE) -> dict[str, Any]:
    """Proposed criteria for a future paired evaluation, from the incumbent's own record.

    Without paired evidence the minimum prospective sample cannot be derived
    from data; it is left for governance with the formula and inputs shown.
    """
    ref = baseline["cohorts"].get(reference_cohort, {})
    if ref.get("n", 0) < 2:
        return {"status": "INSUFFICIENT_INCUMBENT_EVIDENCE", "market_family": baseline["market_family"], **_INERT}
    inc = ref["incumbent"]
    observations = []
    if inc["lower_bound"].get("shortfall_bins"):
        observations.append("INCUMBENT_LOWER_BOUND_SHORTFALL_BINS_PRESENT")
    if inc["lower_bound"].get("n", 0) and inc["lower_bound"]["margin"] < 0:
        observations.append("INCUMBENT_LOWER_BOUND_MARGIN_NEGATIVE")
    return {
        "status": "PROPOSED_UNAPPROVED",
        "requires": "governance approval (WOW/Greg) before any use",
        "market_family": baseline["market_family"],
        "source": "INCUMBENT_BASELINE",
        "source_input_hash": baseline["input_hash"],
        "reference_cohort": reference_cohort,
        "reference": {
            "n": ref["n"], "base_rate": ref["base_rate"], "incumbent_brier": inc["brier"],
            "incumbent_brier_row_sd": inc["brier_row_sd"], "incumbent_log_loss": inc["log_loss"],
            "incumbent_bias": inc["bias"], "incumbent_ece": inc["ece"], "incumbent_lower_bound": inc["lower_bound"],
        },
        "observations": observations,
        "min_prospective_n": None,
        "min_prospective_n_derived_from": "UNDERIVABLE_WITHOUT_PAIRED_EVIDENCE_REQUIRES_GOVERNANCE_VALUE",
        "min_prospective_n_formula": "ceil(((1.95996 + 0.84162) * paired_brier_diff_sd / minimum_meaningful_improvement) ** 2)",
        "criteria": [
            {"cohort": PROSPECTIVE, "metric": "brier.paired_diff.ci_high", "op": "<", "value": 0.0,
             "derived_from": "challenger must beat incumbent on paired Brier at the stated CI level"},
            {"cohort": PROSPECTIVE, "metric": "log_loss.paired_diff.mean", "op": "<=", "value": 0.0,
             "derived_from": "no paired log-loss regression"},
            {"cohort": PROSPECTIVE, "metric": "calibration.challenger.ece", "op": "<=", "value": inc["ece"],
             "derived_from": f"incumbent {reference_cohort} ECE"},
            {"cohort": PROSPECTIVE, "metric": "lower_bound.challenger.margin", "op": ">=", "value": 0.0,
             "derived_from": "calibrated lower-bound validity: hit rate >= mean lower bound"},
        ],
        **_INERT,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Champion-vs-challenger evidence packet (evidence only).")
    parser.add_argument("--rows-jsonl", required=True)
    parser.add_argument("--policy-json")
    parser.add_argument("--propose", action="store_true")
    parser.add_argument("--baseline", action="store_true", help="incumbent-only baseline and proposal")
    args = parser.parse_args(argv)
    try:
        rows = [json.loads(line) for line in Path(args.rows_jsonl).read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.baseline:
            baseline = incumbent_baseline(rows)
            baseline["proposed_policy"] = propose_policy_from_baseline(baseline)
            print(json.dumps(baseline, sort_keys=True))
            return 0
        policy = json.loads(Path(args.policy_json).read_text(encoding="utf-8")) if args.policy_json else None
        packet = evaluate(rows, policy=policy)
    except (OSError, json.JSONDecodeError, EvaluatorInputError) as exc:
        print(json.dumps({"error": "EVALUATOR_INPUT_INVALID", "detail": str(exc), **_INERT}))
        return 2
    if args.propose:
        packet["proposed_policy"] = propose_policy(packet)
    print(json.dumps(packet, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
