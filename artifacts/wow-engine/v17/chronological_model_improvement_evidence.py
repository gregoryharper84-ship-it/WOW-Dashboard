"""Read-only chronological forward-evidence evaluation for certified V17 specialists.

This is never a scorer, retrainer, promotion authority, terminal reducer or
substitute for fresh live production acceptance. Inputs must be independently
attributed, time-ordered settled predictions. Unverifiable rows are held.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import isfinite, log
from typing import Any, Mapping, Sequence

AUTHORITY = "V17_TERMINAL_REDUCER"
ROLES = frozenset({"CHAMPION", "CHALLENGER"})
REQUIRED_IDENTITIES = (
    "prediction_id", "candidate_id", "event_id", "market_identity",
    "sport", "market_family", "model_version", "model_artifact_id",
    "source_snapshot_hash", "settlement_receipt_ref",
    "model_fit_receipt_ref", "forward_certification_receipt_ref",
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None and result.utcoffset() is not None else None


def _valid_row(row: Mapping[str, Any]) -> str | None:
    if not all(_text(row.get(key)) for key in REQUIRED_IDENTITIES):
        return "IDENTITY_OR_PROVENANCE_MISSING"
    if row.get("model_role") not in ROLES:
        return "MODEL_ROLE_UNVERIFIED"
    if row.get("source_verified") is not True or row.get("outcome_verified") is not True:
        return "EVIDENCE_UNVERIFIED"
    if row.get("can_execute") is not False or row.get("terminal_authority") != AUTHORITY:
        return "GOVERNANCE_UNVERIFIED"
    if (row.get("out_of_sample_certified") is not True
            or row.get("calibration_certified") is not True
            or row.get("lower_bound_certified") is not True):
        return "FORWARD_CERTIFICATION_UNVERIFIED"
    p = row.get("calibrated_probability")
    lower = row.get("calibrated_lower_bound")
    if (type(p) not in (int, float) or not isfinite(p) or not 0 < p < 1
            or type(lower) not in (int, float) or not isfinite(lower)
            or not 0 <= lower <= p):
        return "CALIBRATION_OR_BOUND_UNVERIFIED"
    y = row.get("settled_outcome")
    if type(y) is not int or y not in (0, 1):
        return "BINARY_SETTLEMENT_UNVERIFIED"
    stamps = [_utc(row.get(key)) for key in (
        "model_fit_cutoff_at", "source_snapshot_at", "issued_at",
        "event_start_at", "settled_at", "outcome_recorded_at",
    )]
    if any(stamp is None for stamp in stamps):
        return "TIMESTAMP_UNVERIFIED"
    fit_cutoff, snapshot, issued, start, settled, recorded = stamps
    # A retrospective fit after the immutable pregame snapshot is not OOS.
    if not (fit_cutoff < snapshot <= issued < start <= settled <= recorded):
        return "CHRONOLOGY_OR_LEAKAGE_VIOLATION"
    return None


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    n = len(rows)
    brier = sum((float(row["calibrated_probability"]) - row["settled_outcome"]) ** 2
                for row in rows) / n
    log_loss = -sum(
        row["settled_outcome"] * log(float(row["calibrated_probability"]))
        + (1 - row["settled_outcome"]) * log(1 - float(row["calibrated_probability"]))
        for row in rows
    ) / n
    bins: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        bins[min(9, int(float(row["calibrated_probability"]) * 10))].append(row)
    ece = sum(
        len(bucket) / n * abs(
            sum(float(row["calibrated_probability"]) for row in bucket) / len(bucket)
            - sum(row["settled_outcome"] for row in bucket) / len(bucket)
        )
        for bucket in bins.values()
    )
    return {"brier": round(brier, 8), "log_loss": round(log_loss, 8),
            "ece_10_bin": round(ece, 8)}


def evaluate_chronological_evidence(
    rows: Sequence[Mapping[str, Any]], *, min_samples: int = 25,
) -> dict[str, Any]:
    """Evaluate, never automatically promote, an out-of-sample evidence cohort.

    Returned metrics are suppressed below min_samples. Paired comparisons use
    only identical candidate, market and settled-event identity across roles;
    unmatched samples cannot manufacture challenger superiority.
    """
    if type(min_samples) is not int or min_samples < 1:
        raise ValueError("MIN_SAMPLES_INVALID")
    findings: list[dict[str, Any]] = []
    accepted: list[Mapping[str, Any]] = []
    # Each immutable prediction receipt is globally unique, including across roles.
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            findings.append({"index": index, "code": "EVIDENCE_RECORD_INVALID"})
            continue
        error = _valid_row(row)
        key = str(row.get("prediction_id"))
        if key in seen:
            error = "DUPLICATE_MODEL_PREDICTION"
        seen.add(key)
        if error:
            findings.append({"index": index, "code": error})
        else:
            accepted.append(row)

    cohorts: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in accepted:
        cohorts[(row["sport"], row["market_family"], row["model_role"],
                 row["model_version"])].append(row)
    by_lane: list[dict[str, Any]] = []
    for (sport, market, role, version), samples in sorted(cohorts.items()):
        distinct_events = len({row["event_id"] for row in samples})
        sufficient = len(samples) >= min_samples and distinct_events >= min_samples and not findings
        by_lane.append({
            "sport": sport, "market_family": market,
            "model_role": role, "model_version": version,
            "n": len(samples), "distinct_forward_events": distinct_events,
            "evidence_sufficient": sufficient,
            "metrics": _metrics(samples) if sufficient else None,
        })

    # Match on stable event/market/candidate identity, not relative row order.
    pairs: dict[tuple[str, str, str, str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in accepted:
        key = (row["sport"], row["market_family"], row["candidate_id"],
               row["event_id"], row["market_identity"])
        slot = pairs[key]
        if row["model_role"] in slot:
            findings.append({"index": -1, "code": "AMBIGUOUS_PAIRED_MODEL_VERSION"})
        slot[row["model_role"]] = row
    paired = []
    for slot in pairs.values():
        if set(slot) != ROLES:
            continue
        champion, challenger = slot["CHAMPION"], slot["CHALLENGER"]
        if (champion["model_artifact_id"] == challenger["model_artifact_id"]
                or champion["model_version"] == challenger["model_version"]):
            findings.append({"index": -1, "code": "PAIRED_MODEL_IDENTITY_COLLISION"})
            continue
        if (champion["settled_outcome"] != challenger["settled_outcome"]
                or champion["settlement_receipt_ref"] != challenger["settlement_receipt_ref"]
                or champion["event_start_at"] != challenger["event_start_at"]
                or champion["issued_at"] != challenger["issued_at"]
                or champion["source_snapshot_at"] != challenger["source_snapshot_at"]
                or champion["source_snapshot_hash"] != challenger["source_snapshot_hash"]):
            findings.append({"index": -1, "code": "PAIRED_SETTLEMENT_MISMATCH"})
            continue
        paired.append((champion, challenger))
    paired_distinct_events = len({champion["event_id"] for champion, _ in paired})
    paired_sufficient = (
        len(paired) >= min_samples and paired_distinct_events >= min_samples
        and not findings
    )
    paired_metrics = None
    if paired_sufficient:
        champion_metric = _metrics([x for x, _ in paired])
        challenger_metric = _metrics([x for _, x in paired])
        paired_metrics = {
            "champion": champion_metric, "challenger": challenger_metric,
            "brier_improvement": round(
                champion_metric["brier"] - challenger_metric["brier"], 8),
            "log_loss_improvement": round(
                champion_metric["log_loss"] - challenger_metric["log_loss"], 8),
        }

    return {
        "can_execute": False, "terminal_authority": AUTHORITY,
        "automatic_promotion_allowed": False,
        "audit_complete": not findings and bool(accepted),
        "rows_received": len(rows), "rows_verified": len(accepted),
        "rows_held": len(rows) - len(accepted), "findings": findings,
        "cohorts": by_lane, "paired_count": len(paired),
        "paired_distinct_events": paired_distinct_events,
        "paired_comparison_ready": paired_sufficient,
        "paired_metrics": paired_metrics,
        "next_owner": "MODEL_EXCELLENCE_RESEARCH" if paired_sufficient
                      else "DATA_AND_INTEGRATION_ENGINEER",
        "model_release_authorized": False,
    }
