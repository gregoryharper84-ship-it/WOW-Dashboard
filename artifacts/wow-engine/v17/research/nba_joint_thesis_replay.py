"""Offline NBA paired-prop joint-thesis replay. Research diagnostics only.

Consumes immutable, strictly pregame historical *records*. Does not fit, score,
certify, rank, stake, place orders, or mutate production data. A candidate joint
probability must be recorded before event start; it cannot be invented here.
V17_TERMINAL_REDUCER remains the sole terminal authority.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite, log
from pathlib import Path
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
RESEARCH_ONLY_STATUS = "RESEARCH_HOLD_NOT_CERTIFIED"
SETTLED = {"WIN", "LOSS"}
RESEARCH_MIN_COHORT_N = 30
EPS = 1e-12


def _dt(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("timestamp missing")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp invalid") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(timezone.utc)


def _text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + " missing")
    return value.strip()


def _p(raw: Any, field: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (float, int)):
        raise ValueError(field + " must be numeric")
    p = float(raw)
    if not isfinite(p) or not 0.0 < p < 1.0:
        raise ValueError(field + " must be strictly between zero and one")
    return p


def _leg(leg: Mapping[str, Any], start: datetime) -> tuple[str, float, str]:
    for field in ("receipt_id", "stat_type", "period", "exact_line",
                  "direction", "settlement_identity"):
        _text(leg, field)  # preserve literal 7+ or 8+; never convert a threshold
    if leg["direction"].upper() not in ("MORE", "LESS"):
        raise ValueError("direction must be MORE or LESS")
    if leg.get("calibration_status") != "PASS":
        raise ValueError("leg calibration not PASS")
    if _dt(leg.get("model_timestamp_utc")) >= start:
        raise ValueError("leg model timestamp not pregame")
    status = _text(leg, "result").upper()
    if status not in SETTLED | {"PUSH", "VOID", "DNP", "UNRESOLVED"}:
        raise ValueError("leg result unsupported")
    return (
        "|".join(_text(leg, f).casefold() for f in
                 ("stat_type", "period", "exact_line", "direction", "settlement_identity")),
        _p(leg.get("calibrated_probability"), "calibrated_probability"),
        status,
    )


def _validate(record: Mapping[str, Any], holdout_start: datetime) -> dict[str, Any]:
    if _text(record, "sport").upper() != "NBA":
        raise ValueError("sport must be NBA")
    event = _text(record, "official_event_id")
    player = _text(record, "official_player_id")
    _text(record, "prediction_id")
    _text(record, "joint_model_artifact_id")
    _text(record, "joint_research_receipt_id")
    _text(record, "matchup_cohort")
    start = _dt(record.get("event_start_utc"))
    prediction_at = _dt(record.get("prediction_timestamp_utc"))
    model_at = _dt(record.get("joint_model_timestamp_utc"))
    if prediction_at >= start or model_at >= start or model_at > prediction_at:
        raise ValueError("joint prediction must be produced and frozen before start")
    if not isinstance(record.get("legs"), list) or len(record["legs"]) != 2:
        raise ValueError("exactly two legs required")
    a_key, a_p, a_result = _leg(record["legs"][0], start)
    b_key, b_p, b_result = _leg(record["legs"][1], start)
    if a_key == b_key:
        raise ValueError("duplicate exact leg")
    if record["legs"][0]["receipt_id"] == record["legs"][1]["receipt_id"]:
        raise ValueError("duplicate leg receipt")
    joint = _p(record.get("research_joint_probability"), "research_joint_probability")
    # Necessary Fréchet bounds on ANY valid joint event probability.
    if joint < max(0.0, a_p + b_p - 1.0) - 1e-9 or joint > min(a_p, b_p) + 1e-9:
        raise ValueError("joint probability violates marginal Fréchet bounds")
    canonical = event + "|" + player + "|" + "|".join(sorted([a_key, b_key]))
    return {
        "canonical": canonical, "event": event, "player": player,
        "cohort": record["matchup_cohort"].strip(), "start": start,
        "split": "holdout" if start >= holdout_start else "prior",
        "p_joint": joint, "p_independent_baseline": a_p * b_p,
        "outcomes": (a_result, b_result),
    }


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "brier_joint": None, "brier_independent_baseline": None,
                "log_loss_joint": None, "log_loss_independent_baseline": None,
                "brier_gain_positive_is_better": None, "observed_joint_rate": None}
    y = [int(r["outcomes"] == ("WIN", "WIN")) for r in rows]

    def brier(key: str) -> float:
        return sum((r[key] - observed) ** 2 for r, observed in zip(rows, y)) / n

    def ll(key: str) -> float:
        total = 0.0
        for r, observed in zip(rows, y):
            p = max(EPS, min(1.0 - EPS, r[key]))
            total -= observed * log(p) + (1 - observed) * log(1 - p)
        return total / n

    joint_brier = brier("p_joint")
    base_brier = brier("p_independent_baseline")
    return {
        "n": n, "observed_joint_rate": sum(y) / n,
        "mean_joint_forecast": sum(r["p_joint"] for r in rows) / n,
        "mean_independent_forecast": sum(r["p_independent_baseline"] for r in rows) / n,
        "brier_joint": joint_brier,
        "brier_independent_baseline": base_brier,
        "log_loss_joint": ll("p_joint"),
        "log_loss_independent_baseline": ll("p_independent_baseline"),
        "brier_gain_positive_is_better": base_brier - joint_brier,
    }


def evaluate_joint_replay(
    records: Sequence[Mapping[str, Any]],
    *,
    holdout_start_utc: str,
    min_cohort_n: int = RESEARCH_MIN_COHORT_N,
) -> dict[str, Any]:
    """Evidence-only replay of fixed pregame forecasts; cannot publish picks.

    The benchmark product p1*p2 is an evaluation comparator ONLY. It is never
    relabeled as a governed probability or used to authorize any card.
    """
    cutoff = _dt(holdout_start_utc)
    if isinstance(min_cohort_n, bool) or not isinstance(min_cohort_n, int) or min_cohort_n < 1:
        raise ValueError("min_cohort_n must be a positive integer")
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be a sequence")

    accepted, rejected, duplicate = [], [], []
    seen: set[str] = set()
    for index, record in enumerate(records):
        try:
            if not isinstance(record, Mapping):
                raise ValueError("row is not a mapping")
            parsed = _validate(record, cutoff)
            if parsed["canonical"] in seen:
                duplicate.append({"row_index": index, "reason": "duplicate exact two-leg thesis"})
                continue
            seen.add(parsed["canonical"])
            accepted.append(parsed)
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"row_index": index, "reason": str(exc)})

    prior = [r for r in accepted if r["split"] == "prior"]
    holdout = [r for r in accepted if r["split"] == "holdout"]
    settled = [r for r in holdout if set(r["outcomes"]) <= SETTLED]
    excluded = [r for r in holdout if r not in settled]

    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in settled:
        cohorts[item["cohort"]].append(item)
    cohort_results = {
        key: {"sample_sufficient_for_descriptive_review": len(rows) >= min_cohort_n,
              **_metrics(rows)}
        for key, rows in sorted(cohorts.items())
    }
    metrics = _metrics(settled)
    return {
        "status": RESEARCH_ONLY_STATUS,
        "candidate_certified": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "portfolio_qualification": "NOT_EVALUATED",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "benchmark_is_not_governed_probability": True,
        "holdout_start_utc": cutoff.isoformat(),
        "source_rows": len(records),
        "accepted_unique_rows": len(accepted),
        "prior_rows_not_graded": len(prior),
        "holdout_rows": len(holdout),
        "holdout_settled_rows": len(settled),
        "holdout_unsettled_or_void_rows": len(excluded),
        "duplicate_rows": duplicate,
        "rejected_rows": rejected,
        "row_reconciliation_pass": len(records) ==
            len(prior) + len(holdout) + len(duplicate) + len(rejected),
        "sample_sufficient_for_descriptive_review": len(settled) >= min_cohort_n,
        "metrics": metrics,
        "matchup_cohorts": cohort_results,
        "claim_of_outperformance": False,
        "model_promoted": False,
        "staging_note": "No fitting, calibration, holdout selection, or certification occurs in this tool.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline historical NBA joint-thesis replay; never scores live bets")
    parser.add_argument("path", help="JSON with records[] from immutable pregame/outcome ledger")
    parser.add_argument("--holdout-start-utc", required=True)
    parser.add_argument("--min-cohort-n", type=int, default=RESEARCH_MIN_COHORT_N)
    args = parser.parse_args()
    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    result = evaluate_joint_replay(
        data["records"], holdout_start_utc=args.holdout_start_utc,
        min_cohort_n=args.min_cohort_n)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
