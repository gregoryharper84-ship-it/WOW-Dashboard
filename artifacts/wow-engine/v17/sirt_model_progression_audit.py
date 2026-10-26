"""SIRT evidence-only assessment of historical model quality and recurrence.

The caller supplies immutable, independently attributable observations. This module
never retrieves production data, changes fitted probabilities, certifies a model,
starts an engineering worker, or authorizes any production action.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite, log
from typing import Any, Mapping, Sequence

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _probability(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and isfinite(value) and 0 < value < 1
    )


def _valid_observation(row: Mapping[str, Any]) -> bool:
    required = (
        "sport", "market_family", "cohort_id", "event_id", "market_id",
        "model_artifact_id", "prediction_receipt_ref", "settlement_evidence_ref",
        "calibration_evidence_ref", "source_snapshot_ref", "out_of_sample_split_ref",
        "model_owner_principal", "settlement_verifier_principal",
    )
    if any(not _text(row.get(k)) for k in required):
        return False
    if row.get("role") not in {"CHAMPION", "CHALLENGER"}:
        return False
    if row.get("regime") != "PREGAME":
        # A live model requires a separate independently certified replay contract.
        return False
    if row.get("out_of_sample_verified") is not True or row.get("settlement_verified") is not True:
        return False
    if row.get("point_in_time_snapshot_verified") is not True:
        return False
    if row["model_owner_principal"] == row["settlement_verifier_principal"]:
        return False
    prob = row.get("calibrated_probability")
    if not _probability(prob) or type(row.get("outcome")) is not int or row["outcome"] not in (0, 1):
        return False
    prediction = _time(row.get("predicted_at"))
    start = _time(row.get("event_start_at"))
    settled = _time(row.get("settled_at"))
    return all((prediction, start, settled)) and prediction < start <= settled



def _quality_metrics(verified: Sequence[tuple[float, int]]) -> dict[str, float]:
    """Observed proper scoring/calibration metrics, never a sporting forecast."""
    n = len(verified)
    brier = sum((p - y) ** 2 for p, y in verified) / n
    log_loss = -sum(y * log(p) + (1 - y) * log(1 - p) for p, y in verified) / n
    bias = sum(p - y for p, y in verified) / n
    bins: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in verified:
        bins[min(9, int(10 * p))].append((p, y))
    expected_calibration_error = sum(
        len(group) / n * abs(sum(p - y for p, y in group) / len(group))
        for group in bins.values()
    )
    return {
        "brier": brier, "log_loss": log_loss,
        "signed_calibration_bias": bias,
        "expected_calibration_error_10_bins": expected_calibration_error,
        "sample_count": n,
    }

def _temporal_quality_shift(
    matched: Sequence[tuple[datetime, float, float, int]],
) -> dict[str, Any]:
    """Earlier-vs-later descriptive shift on the SAME matched holdout events."""
    records = sorted(matched, key=lambda r: r[0])
    midpoint = len(records) // 2
    if (midpoint < 15 or len(records) - midpoint < 15
            or records[midpoint - 1][0] >= records[midpoint][0]):
        return {"status": "TEMPORAL_COMPARISON_UNAVAILABLE",
                "reason": "INSUFFICIENT_DISTINCT_CHRONOLOGICAL_WINDOWS"}
    older, newer = records[:midpoint], records[midpoint:]
    summary: dict[str, Any] = {
        "status": "DESCRIPTIVE_ONLY_NO_DRIFT_CERTIFICATION",
        "earlier_pairs": len(older), "recent_pairs": len(newer),
    }
    for name, index in (("champion", 1), ("challenger", 2)):
        past = _quality_metrics([(r[index], r[3]) for r in older])
        recent = _quality_metrics([(r[index], r[3]) for r in newer])
        summary[name + "_recent_minus_earlier_brier"] = recent["brier"] - past["brier"]
        summary[name + "_recent_minus_earlier_ece"] = (
            recent["expected_calibration_error_10_bins"]
            - past["expected_calibration_error_10_bins"]
        )
    return summary

def audit_challenger_replay(
    observations: Sequence[Mapping[str, Any]], *, minimum_pairs: int = 30,
) -> dict[str, Any]:
    """Compare ONLY exact matched, verified pregame outcomes, never market prices.

    The score is a descriptive research metric, not an upgrade decision or a
    certification gate. Duplicate, unmatched and invalid inputs block a cohort
    rather than allowing convenient cherry-picked comparisons.
    """
    if type(minimum_pairs) is not int or minimum_pairs < 1:
        raise ValueError("MINIMUM_PAIRS_INVALID")
    cohorts: dict[tuple[str, str, str], dict[tuple[str, str], dict[str, Mapping[str, Any]]]] = defaultdict(dict)
    artifact_ids: dict[tuple[str, str, str], dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    findings: list[str] = []
    invalid_cohorts: set[tuple[str, str, str]] = set()
    seen_receipts: dict[str, tuple[str, str, str]] = {}
    for index, row in enumerate(observations):
        if not isinstance(row, Mapping) or not _valid_observation(row):
            findings.append(f"ROW_{index}_EVIDENCE_UNVERIFIED")
            if isinstance(row, Mapping) and all(_text(row.get(k)) for k in (
                "sport", "market_family", "cohort_id"
            )):
                invalid_cohorts.add((row["sport"], row["market_family"], row["cohort_id"]))
            continue
        cohort = (row["sport"], row["market_family"], row["cohort_id"])
        key = (row["event_id"], row["market_id"])
        role = row["role"]
        receipt_id = row["prediction_receipt_ref"]
        prior_cohort = seen_receipts.get(receipt_id)
        if prior_cohort is not None:
            invalid_cohorts.update((cohort, prior_cohort))
            findings.append(f"ROW_{index}_PREDICTION_RECEIPT_REUSED")
        else:
            seen_receipts[receipt_id] = cohort
        pair = cohorts[cohort].setdefault(key, {})
        if role in pair:
            invalid_cohorts.add(cohort)
            findings.append(f"ROW_{index}_DUPLICATE_PREDICTION")
        else:
            pair[role] = row
        artifact_ids[cohort][role].add(row["model_artifact_id"])
    comparisons: list[dict[str, Any]] = []
    for cohort in sorted(set(cohorts) | invalid_cohorts):
        pairs = cohorts.get(cohort, {})
        mismatches = []
        champion_sq, challenger_sq = [], []
        matched_rows: list[tuple[datetime, float, float, int]] = []
        for event_key, pair in sorted(pairs.items()):
            if set(pair) != {"CHAMPION", "CHALLENGER"}:
                mismatches.append("UNMATCHED_EVENT_MARKET")
                continue
            first, second = pair["CHAMPION"], pair["CHALLENGER"]
            if first["outcome"] != second["outcome"] or first["settlement_evidence_ref"] != second["settlement_evidence_ref"]:
                mismatches.append("SETTLEMENT_DISAGREEMENT")
                continue
            if first["event_start_at"] != second["event_start_at"]:
                mismatches.append("EVENT_TIME_DISAGREEMENT")
                continue
            if first["out_of_sample_split_ref"] != second["out_of_sample_split_ref"]:
                mismatches.append("HOLDOUT_SPLIT_DISAGREEMENT")
                continue
            champion_sq.append((first["calibrated_probability"] - first["outcome"]) ** 2)
            challenger_sq.append((second["calibrated_probability"] - second["outcome"]) ** 2)
            matched_rows.append((_time(first["event_start_at"]),
                                 first["calibrated_probability"],
                                 second["calibrated_probability"], first["outcome"]))
        if any(len(s) != 1 for s in artifact_ids.get(cohort, {}).values()):
            mismatches.append("MULTIPLE_ARTIFACTS_IN_COHORT")
        if (artifact_ids[cohort]["CHAMPION"] & artifact_ids[cohort]["CHALLENGER"]):
            mismatches.append("CHAMPION_CHALLENGER_NOT_DISTINCT")
        if cohort in invalid_cohorts:
            mismatches.append("INVALID_SOURCE_ROW")
        if len(champion_sq) < minimum_pairs:
            mismatches.append("INSUFFICIENT_MATCHED_SAMPLE")
        problems = sorted(set(mismatches))
        if problems:
            findings.append("COHORT_" + "/".join(cohort) + "_" + ",".join(problems))
        eligible = not problems
        champion_quality = _quality_metrics([(a, outcome) for _, a, _, outcome in matched_rows]) if eligible else None
        challenger_quality = _quality_metrics([(b, outcome) for _, _, b, outcome in matched_rows]) if eligible else None
        temporal = _temporal_quality_shift(matched_rows) if eligible else None
        comparisons.append({
            "sport": cohort[0], "market_family": cohort[1], "cohort_id": cohort[2],
            "matched_pairs": len(champion_sq), "observed_market_pairs": len(pairs),
            "champion_brier": sum(champion_sq) / len(champion_sq) if eligible else None,
            "challenger_brier": sum(challenger_sq) / len(challenger_sq) if eligible else None,
            "challenger_minus_champion_brier": (
                (sum(challenger_sq) - sum(champion_sq)) / len(champion_sq) if eligible else None
            ),
            "champion_quality": champion_quality,
            "challenger_quality": challenger_quality,
            "temporal_quality_shift": temporal,
            "metrics_are_descriptive_not_statistical_certification": True,
            "assessment": "RESEARCH_ONLY_NO_PROMOTION" if eligible else "EVIDENCE_HOLD",
            "blockers": problems,
        })
    if not observations:
        findings.append("HISTORICAL_OBSERVATIONS_MISSING")
    return {
        "status": "RESEARCH_EVALUATION_ONLY" if not findings else "EVIDENCE_HOLD",
        "comparisons": comparisons, "findings": findings,
        "sample_size_is_not_certification": True,
        "model_promotion_authorized": False,
        "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY,
    }


def audit_confirmed_recurrence(incidents: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Identify repeat *confirmed* causes with missing preventive-test evidence.

    Incident summaries and similar error messages are NOT root-cause evidence.
    The external canonical incident ledger remains the source of truth.
    """
    causes: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    findings: list[str] = []
    for index, row in enumerate(incidents):
        identifier = row.get("incident_id") if isinstance(row, Mapping) else None
        if not _text(identifier) or identifier in seen:
            findings.append(f"INCIDENT_{index}_IDENTITY_MISSING_OR_DUPLICATE")
            continue
        seen.add(identifier)
        cause = row.get("confirmed_root_cause_id") or row.get("root_cause_code")
        if (row.get("root_cause_status") != "CONFIRMED" or not _text(cause)
                or not _text(row.get("root_cause_evidence_ref"))):
            continue
        causes[cause].append(row)
    families = []
    for cause, group in sorted(causes.items()):
        recurring = len(group) > 1
        missing = sorted(row["incident_id"] for row in group if not _text(row.get("prevention_regression_ref")))
        families.append({
            "fingerprint": hashlib.sha256(("SIRT_FAILURE|" + cause).encode()).hexdigest(),
            "confirmed_root_cause_id": cause,
            "incident_ids": sorted(row["incident_id"] for row in group),
            "occurrences": len(group),
            "recurring": recurring,
            "prevention_test_evidence_missing_for": missing if recurring else [],
            "recommendation": (
                "ENGINEERING_PREVENTION_HANDOFF" if recurring and missing
                else "INDEPENDENT_RECURRENCE_MONITOR"
            ),
        })
    return {
        "status": "EVIDENCE_HOLD" if findings else "OBSERVED_INCIDENT_HISTORY",
        "families": families, "findings": findings,
        "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY,
    }


def audit_evidence_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compose both Phase-3 audits from an externally supplied immutable snapshot.

    A JSON file is *not* automatically certified production data; consumers must
    check its identity, coverage and source provenance independently.
    """
    records = payload.get("observations") if isinstance(payload, Mapping) else None
    incidents = payload.get("incidents") if isinstance(payload, Mapping) else None
    if not isinstance(records, list) or not isinstance(incidents, list):
        return {
            "status": "EVIDENCE_HOLD", "findings": ["SNAPSHOT_SCHEMA_INCOMPLETE"],
            "production_source_verified": False, "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }
    models = audit_challenger_replay(records)
    recurrence = audit_confirmed_recurrence(incidents)
    return {
        "status": (
            "RESEARCH_EVALUATION_ONLY"
            if models["status"] == "RESEARCH_EVALUATION_ONLY"
            and recurrence["status"] == "OBSERVED_INCIDENT_HISTORY"
            else "EVIDENCE_HOLD"
        ),
        "historical_quality": models,
        "confirmed_recurrence": recurrence,
        "production_source_verified": False,
        "model_promotion_authorized": False,
        "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY,
    }


def main() -> int:
    """Offline/read-only SIRT input adapter; no secrets, DB connections or writes."""
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="SIRT read-only historical model evidence audit")
    parser.add_argument("--evidence-json", required=True, help="Immutable JSON snapshot with observations and incidents")
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.evidence_json).read_text(encoding="utf-8"))
        result = audit_evidence_snapshot(payload)
    except (OSError, ValueError, TypeError):
        result = {
            "status": "EVIDENCE_HOLD", "findings": ["SNAPSHOT_UNAVAILABLE_OR_INVALID"],
            "production_source_verified": False, "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "RESEARCH_EVALUATION_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
