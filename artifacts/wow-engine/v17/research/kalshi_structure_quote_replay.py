"""Research-only point-in-time replay of fully sourced Kalshi combo constructions.

This is an evidence SCREEN, not a provenance authenticator, fitted model,
recommender, model-certification process or execution authority.
Synthetic tests demonstrate invariants, not a live trading edge.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

STYLES = {
    "SINGLE_PLAYER_SAME_GAME_MULTI_MARKET",
    "MULTI_PLAYER_SAME_GAME",
    "MULTI_EVENT_COMBINATION",
}
RESULTS = {"YES", "NO", "SCALAR", "VOID"}


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("MISSING_OR_NON_STRING_TIMESTAMP")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("INVALID_TIMESTAMP") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("NAIVE_TIMESTAMP")
    return dt.astimezone(timezone.utc)


def _str(row: dict[str, Any], key: str) -> str:
    x = row.get(key)
    if not isinstance(x, str) or not x.strip():
        raise ValueError("MISSING_" + key.upper())
    return x.strip()


def _int(row: dict[str, Any], key: str, lo: int, hi: int) -> int:
    x = row.get(key)
    if type(x) is not int or not lo <= x <= hi:
        raise ValueError("INVALID_" + key.upper())
    return x


def _prob(row: dict[str, Any], key: str) -> float:
    x = row.get(key)
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError("INVALID_" + key.upper())
    x = float(x)
    if not math.isfinite(x) or not 0 < x < 1:
        raise ValueError("INVALID_" + key.upper())
    return x


def _check(row: dict[str, Any], cutoff: datetime) -> dict[str, Any]:
    style = _str(row, "structure_type")
    if style not in STYLES:
        raise ValueError("UNKNOWN_STRUCTURE_TYPE")
    if _str(row, "official_result") not in RESULTS:
        raise ValueError("INVALID_RESULT")
    if _str(row, "calibration_status") != "PASS":
        raise ValueError("CALIBRATION_NOT_VERIFIED")
    if _str(row, "source_status") != "OFFICIALLY_RECONCILED":
        raise ValueError("SOURCE_NOT_RECONCILED")
    ids = row.get("component_market_ids")
    if not isinstance(ids, list) or len(ids) < 2 or any(
        not isinstance(v, str) or not v.strip() for v in ids
    ) or len(ids) != len(set(ids)):
        raise ValueError("MISSING_OR_DUPLICATE_MARKET_IDS")
    stat_families = row.get("component_stat_families")
    if not isinstance(stat_families, list) or len(stat_families) != len(ids) or any(
        not isinstance(v, str) or not v.strip() or v.upper() == "UNKNOWN"
        for v in stat_families
    ):
        raise ValueError("MISSING_EXACT_STAT_FAMILIES")
    predicted = _utc(row.get("prediction_at_utc"))
    quote = _utc(row.get("quote_at_utc"))
    started = _utc(row.get("event_start_utc"))
    settled = _utc(row.get("settlement_at_utc"))
    fit_cutoff = _utc(row.get("model_fit_cutoff_utc"))
    if not fit_cutoff < predicted <= quote < started <= settled:
        raise ValueError("CHRONOLOGY_INVALID")
    if started < cutoff:
        raise ValueError("OUTSIDE_FORWARD_HOLDOUT")
    p = _prob(row, "pregame_joint_probability")
    lb = row.get("pregame_joint_lower_bound")
    if isinstance(lb, bool) or not isinstance(lb, (int, float)) or not math.isfinite(float(lb)):
        raise ValueError("MISSING_JOINT_LOWER_BOUND")
    if not 0 <= float(lb) <= p:
        raise ValueError("INVALID_JOINT_LOWER_BOUND")
    price = _int(row, "executable_quote_cents", 1, 99)
    fee = _int(row, "fee_cents_per_contract", 0, 100)
    settlement = _int(row, "settlement_cents_per_contract", 0, 100)
    result = row["official_result"]
    if (result == "YES" and settlement != 100) or (result == "NO" and settlement != 0):
        raise ValueError("RESULT_PAYOUT_CONFLICT")
    if result == "SCALAR" and settlement in (0, 100):
        raise ValueError("SCALAR_PAYOUT_CONFLICT")
    if result == "VOID":
        raise ValueError("VOID_NOT_BINARY_GRADEABLE")
    _str(row, "immutable_prediction_receipt_id")
    _str(row, "fitted_model_artifact_id")
    _str(row, "official_settlement_evidence_ref")
    _str(row, "quote_evidence_ref")
    _str(row, "thesis_cluster_id")
    _str(row, "comparison_group_id")
    return {
        "style": style,
        "p": p,
        "outcome": None if result == "SCALAR" else int(result == "YES"),
        "cash_profit_cents_per_contract": settlement - price - fee,
        "capital_cents_per_contract": price + fee,
        "cluster": row["thesis_cluster_id"],
        "comparison_group": row["comparison_group_id"],
        "scalar": result == "SCALAR",
    }


def replay(rows: list[dict[str, Any]], *, holdout_start_utc: str) -> dict[str, Any]:
    """Compute descriptive graded metrics; no estimated probability changes.

    All input provenance is *caller asserted*; this function does NOT
    independently authenticate the source, approve the model, or establish lift.
    """
    if not isinstance(rows, list):
        raise ValueError("ROWS_NOT_A_LIST")
    cutoff = _utc(holdout_start_utc)
    eligible: list[dict[str, Any]] = []
    rejects: dict[str, int] = defaultdict(int)
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            rejects["NOT_AN_OBJECT"] += 1
            continue
        try:
            slip_id = _str(row, "slip_id")
            if slip_id in ids:
                raise ValueError("DUPLICATE_SLIP_ID")
            ids.add(slip_id)
            eligible.append(_check(row, cutoff))
        except ValueError as exc:
            rejects[str(exc)] += 1

    by_style: dict[str, dict[str, Any]] = {}
    for style in sorted(STYLES):
        subset = [x for x in eligible if x["style"] == style]
        binary = [x for x in subset if x["outcome"] is not None]
        invested = sum(x["capital_cents_per_contract"] for x in subset)
        profit = sum(x["cash_profit_cents_per_contract"] for x in subset)
        by_style[style] = {
            "positions": len(subset),
            "independent_thesis_clusters": len({x["cluster"] for x in subset}),
            "binary_graded": len(binary),
            "scalar_settled": sum(x["scalar"] for x in subset),
            "win_rate": sum(x["outcome"] for x in binary) / len(binary) if binary else None,
            "brier": sum((x["p"] - x["outcome"]) ** 2 for x in binary) / len(binary) if binary else None,
            "log_loss": sum(
                -x["outcome"] * math.log(x["p"]) -
                (1 - x["outcome"]) * math.log1p(-x["p"]) for x in binary
            ) / len(binary) if binary else None,
            "net_cents_per_contract_sum": profit,
            "entry_weighted_roi": profit / invested if invested else None,
        }

    groups: dict[str, set[str]] = defaultdict(set)
    for x in eligible:
        groups[x["comparison_group"]].add(x["style"])
    matched_groups = sum(len(styles) > 1 for styles in groups.values())

    return {
        "status": "RESEARCH_DESCRIPTIVE_NOT_CERTIFIED",
        "rows_submitted": len(rows),
        "rows_eligible": len(eligible),
        "rows_excluded": len(rows) - len(eligible),
        "reject_reasons": dict(sorted(rejects.items())),
        "by_style": by_style,
        "comparison_groups_with_multiple_styles": matched_groups,
        "comparative_lift_established": False,
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
