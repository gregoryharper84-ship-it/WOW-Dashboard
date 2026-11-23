"""WOW V17: read-only proper-score and discrete-prop distribution diagnostics.

Diagnostic companion to the existing immutable LLP and prop graders. No fitted
model, certification, production thresholds, terminal reduction, or orders.
Input provenance must be from frozen, authenticated source receipts; assertions
inside this module deliberately do not authenticate receipt signatures.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import floor, isfinite
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
AUTO_PROMOTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


def _num(value: Any, low: float = 0.0, high: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if not isfinite(v) or v < low or (high is not None and v > high):
        return None
    return v


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else None


def binary_murphy_diagnostics(pairs: Iterable[tuple[float, int]], *,
                              bins: int = 10, min_n: int = 30) -> dict[str, Any]:
    """Binned Murphy components, with explicit within-bin approximation error.

    Only pass pairs from an independently validated immutable pregame+official
    binary-outcome join. Do not include soccer 1X2 as independent selections.
    Binned REL - RES + UNC is generally not *exactly* equal to the raw Brier
    when forecast probabilities vary inside the same bin.
    """
    if type(bins) is not int or not 2 <= bins <= 100:
        raise ValueError("INVALID_BIN_COUNT")
    if type(min_n) is not int or min_n < 1:
        raise ValueError("INVALID_MIN_N")
    data = list(pairs)
    for p, y in data:
        if _num(p, 0, 1) is None or type(y) is not int or y not in (0, 1):
            raise ValueError("INVALID_BINARY_FORECAST_PAIR")
    if not data:
        return {"n": 0, "status": "INSUFFICIENT_SAMPLE", "can_execute": False,
                "automatic_parameter_mutation": False}
    groups: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in data:
        groups[min(floor(p * bins), bins - 1)].append((p, y))
    n = len(data)
    base = mean(y for _, y in data)
    rel = sum(len(rows) / n * (mean(p for p, _ in rows) - mean(y for _, y in rows)) ** 2
              for rows in groups.values())
    res = sum(len(rows) / n * (mean(y for _, y in rows) - base) ** 2
              for rows in groups.values())
    unc = base * (1 - base)
    actual = mean((p - y) ** 2 for p, y in data)
    reconstructed = rel - res + unc
    return {
        "n": n,
        "status": "DIAGNOSTIC_ONLY" if n >= min_n else "INSUFFICIENT_SAMPLE",
        "brier": actual,
        "murphy_reliability_binned": rel,
        "murphy_resolution_binned": res,
        "murphy_uncertainty": unc,
        "murphy_binned_reconstruction": reconstructed,
        "murphy_binning_residual": actual - reconstructed,
        "binning_residual_is_not_model_drift": True,
        "bins_occupied": len(groups),
        "automatic_parameter_mutation": False,
        "can_execute": False,
    }


def _prop_identity(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
    # An exact offer must remain exact; do not turn 7+ into 6.5.
    fields = ("sport", "official_event_id", "player_id", "stat_type", "period",
              "exact_offer", "direction", "settlement_basis")
    values = tuple(row.get(k) for k in fields)
    if any(x is None or x == "" for x in values):
        return None
    if str(row.get("direction")).upper() not in ("MORE", "LESS"):
        return None
    return values


def discrete_prop_distribution_audit(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], *,
    bins: int = 10, min_n: int = 30,
) -> dict[str, Any]:
    """Compute discrete CRPS and deterministic integrated (nonrandomized) PIT.

    Each PMF is ordered P(X=0), P(X=1), ... for a nonnegative count.
    The PIT histogram spreads each observation uniformly over its *frozen*
    CDF jump interval in expectation (no RNG); this is an integrated randomized
    PIT diagnostic, not a production conformal interval or certification.
    """
    if type(bins) is not int or not 2 <= bins <= 100:
        raise ValueError("INVALID_BIN_COUNT")
    if type(min_n) is not int or min_n < 1:
        raise ValueError("INVALID_MIN_N")
    seen: set[str] = set()
    blocked: list[dict[str, str]] = []
    pit_mass = [0.0] * bins
    pit_unavailable: list[str] = []
    crps_scores: list[float] = []
    for index, (prediction, settlement) in enumerate(pairs):
        receipt = str(prediction.get("prediction_id") or "").strip()
        reason = None
        if not receipt or receipt in seen:
            reason = "PREDICTION_ID_MISSING_OR_DUPLICATE"
        else:
            seen.add(receipt)
        start = _utc(prediction.get("event_start_utc"))
        frozen = _utc(prediction.get("model_timestamp_utc"))
        material = _utc(prediction.get("latest_material_update_at_utc"))
        finalized = _utc(settlement.get("settled_at_utc"))
        if not reason and (
            start is None or frozen is None or not frozen < start
            or material is None or material > frozen
            or finalized is None or finalized < start
        ):
            reason = "PREGAME_OR_SETTLEMENT_CHRONOLOGY_INVALID"
        if not reason and (
            prediction.get("immutable_pregame") is not True
            or not prediction.get("model_artifact_digest")
            or not prediction.get("source_snapshot_id")
            or not prediction.get("controlling_specialist")
            or _prop_identity(prediction) is None
            or _prop_identity(prediction) != _prop_identity(settlement)
        ):
            reason = "EXACT_IDENTITY_OR_FROZEN_MODEL_EVIDENCE_MISSING"
        if not reason and (
            settlement.get("source_type") != "INDEPENDENT_OFFICIAL"
            or not settlement.get("settlement_source_id")
            or settlement.get("official_result") not in ("WIN", "LOSS")
        ):
            reason = "OFFICIAL_SETTLEMENT_MISSING_OR_NONBINARY"
        actual = settlement.get("actual_count")
        if not reason and (type(actual) is not int or actual < 0):
            reason = "ACTUAL_COUNT_INVALID"
        pmf = prediction.get("predictive_count_pmf")
        if not reason and (not isinstance(pmf, (list, tuple)) or not 1 <= len(pmf) <= 10001):
            reason = "FROZEN_COUNT_PMF_UNAVAILABLE"
        probs = []
        if not reason:
            probs = [_num(p, 0, 1) for p in pmf]
            if any(p is None for p in probs) or abs(sum(probs) - 1.0) > 1e-6:
                reason = "FROZEN_COUNT_PMF_INVALID"
        if not reason:
            line = prediction["exact_offer"]
            # Exact settlement is independently declared. Check the numerical
            # threshold only for numeric offers, never reinterpret '7+'.
            if type(line) in (float, int) and not isinstance(line, bool):
                if actual == line:
                    reason = "RESULT_CONTRADICTS_EXACT_LINE"
                expected = (actual > line) if prediction["direction"] == "MORE" else (actual < line)
                if not reason and expected != (settlement["official_result"] == "WIN"):
                    reason = "RESULT_CONTRADICTS_EXACT_LINE"
        if reason:
            blocked.append({"row": str(index), "prediction_id": receipt, "reason": reason})
            continue
        cdf = 0.0
        cdf_before, cdf_after = 0.0, 0.0
        error = 0.0
        for k in range(max(len(probs), actual + 1)):
            pk = probs[k] if k < len(probs) else 0.0
            if k == actual:
                cdf_before = cdf
            cdf += pk
            if k == actual:
                cdf_after = cdf
            error += (cdf - float(k >= actual)) ** 2
        crps_scores.append(error)
        width = cdf_after - cdf_before
        if width <= 0:
            # Observation has zero mass in fitted discrete PMF: PIT interval
            # degenerates; do not fabricate a PIT histogram contribution.
            pit_unavailable.append(receipt)
            continue
        for j in range(bins):
            low, high = j / bins, (j + 1) / bins
            pit_mass[j] += max(0.0, min(high, cdf_after) - max(low, cdf_before)) / width
    n = len(crps_scores)
    pit_n = n - len(pit_unavailable)
    return {
        "rows_received": len(pairs),
        "rows_graded": n,
        "rows_pit_graded": pit_n,
        "pit_unavailable_prediction_ids": pit_unavailable,
        "rows_blocked": len(blocked),
        "blockers": blocked,
        "reconciled": len(pairs) == n + len(blocked),
        "status": "DIAGNOSTIC_ONLY" if n >= min_n else "INSUFFICIENT_SAMPLE",
        "mean_discrete_crps": mean(crps_scores) if crps_scores else None,
        "nonrandomized_pit_bin_proportions": [x / pit_n for x in pit_mass] if pit_n else None,
        "pit_method": "INTEGRATED_RANDOMIZED_PIT_EXPECTATION_DISCRETE",
        "source_receipts_cryptographically_verified": False,
        "automatic_parameter_mutation": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
