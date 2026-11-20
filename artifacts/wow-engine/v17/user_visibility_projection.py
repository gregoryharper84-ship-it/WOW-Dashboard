"""Read-only, fail-closed V17 prop visibility. Never scores or approves a pick."""
from __future__ import annotations
import math
from collections.abc import Mapping, Sequence
from typing import Any

CAN_EXECUTE = False

def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0.0 < value < 1.0 else None

def _codes(outcome: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    qual = result.get("probability_qualification")
    qual = qual if isinstance(qual, Mapping) else {}
    codes: list[str] = []
    for source in (outcome.get("blockers"), qual.get("blockers")):
        if isinstance(source, (list, tuple)):
            codes.extend(code for code in source if isinstance(code, str) and code)
    code = outcome.get("code")
    if isinstance(code, str) and code and code != "FINAL_APPROVED":
        codes.append(code)
    return list(dict.fromkeys(codes))

def project_pick_visibility(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Separate actual approved receipts, unapproved certified analysis and withheld rows.

    The independent model/terminal/market/payout gates remain authoritative.
    Reconcile every input row; never infer a probability or change a decision.
    """
    approved: list[dict[str, Any]] = []
    analysis: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    for outcome in outcomes:
        result = outcome.get("result")
        result = result if isinstance(result, Mapping) else {}
        prediction = result.get("prediction")
        prediction = prediction if isinstance(prediction, Mapping) else {}
        qualification = result.get("probability_qualification")
        qualification = qualification if isinstance(qualification, Mapping) else {}
        terminal = str(outcome.get("terminal_label") or outcome.get("code") or "UNKNOWN")
        base: dict[str, Any] = {
            "row_key": outcome.get("row_key"),
            "terminal_status": outcome.get("terminal_status"),
            "terminal_label": terminal,
            "blockers": _codes(outcome, result),
            "can_execute": False,
        }
        p = _probability(prediction.get("calibrated_probability"))
        lo = _probability(prediction.get("calibrated_probability_lower_bound"))
        hi = _probability(prediction.get("calibrated_probability_upper_bound"))
        if hi is None:
            hi = _probability(prediction.get("calibrated_upper_bound"))
        provenance = (
            bool(prediction.get("prediction_id"))
            and bool(outcome.get("source_snapshot_id"))
            and bool(prediction.get("model_family"))
            and bool(prediction.get("model_artifact_version"))
        )
        valid = (
            outcome.get("model_evaluated") is True
            and outcome.get("probability_publishable") is True
            and provenance
            and p is not None and lo is not None and hi is not None
            and lo <= p <= hi
            and prediction.get("calibration_status") not in (None, "", "FAILED", "INVALID")
        )
        if not valid:
            withheld.append({**base, "visibility": "WITHHELD_NOT_PUBLISHABLE",
                             "probability_present": False})
            continue
        report = {
            **base,
            "calibrated_probability": p,
            "calibrated_lower_bound": lo,
            "calibrated_upper_bound": hi,
            "prediction_id": prediction["prediction_id"],
            "source_snapshot_id": outcome["source_snapshot_id"],
            "model_family": prediction["model_family"],
            "model_artifact_version": prediction["model_artifact_version"],
            "calibration_status": prediction["calibration_status"],
            "rank_eligible": outcome.get("rank_eligible") is True,
            "money_lane_status": prediction.get("money_lane_status"),
            "recommendation_authorized": False,
        }
        fully_approved = (
            terminal == "FINAL_APPROVED"
            and outcome.get("terminal_status") == "COMPLETED"
            and outcome.get("rank_eligible") is True
            and qualification.get("final_approved_allowed") is True
            and prediction.get("money_lane_status") == "RESOLVED"
            and not base["blockers"]
            and outcome.get("downstream_portfolio_evaluation_allowed") is True
        )
        if fully_approved:
            approved.append({**report, "visibility": "MODEL_APPROVED_PICK",
                             "recommendation_authorized": True})
        else:
            analysis.append({**report, "visibility": "MODEL_ANALYSIS_NOT_APPROVED"})
    count = len(approved) + len(analysis) + len(withheld)
    if count != len(outcomes):
        raise AssertionError("PICK_VISIBILITY_RECONCILIATION_FAILED")
    return {
        "model_approved_picks": approved,
        "model_analysis_not_approved": analysis,
        "withheld_rows": withheld,
        "rows_in": len(outcomes),
        "rows_reconciled": count,
        "read_only": True,
        "no_probability_substitution": True,
        "can_execute": False,
    }
