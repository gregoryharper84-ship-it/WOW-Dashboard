"""Independent read-only NFL forward evidence replay (never certification).

Consumes rows from the existing verified-grade view and a historical health
snapshot. No DB connection, coefficient fitting, grade write, promotion, or
publication decision occurs here. Source/right and outcome authenticity are
external scientific acceptance requirements, not inferred from this function.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite, log
from typing import Any, Mapping, Sequence

MIN_FORWARD_GRADED = 100
CAN_EXECUTE = False
_TOLERANCE = 1e-9


def _number(value: Any) -> float | None:
    """Accept only finite, non-boolean numerical evidence."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _instant(value: Any) -> datetime | None:
    """Normalize one timezone-aware instant or reject an unproven clock."""
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None
    return stamp if stamp.utcoffset() is not None else None


def _result(code: str, n: int, metrics: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Use one nonauthorizing report envelope in both success and failure."""
    consistent = code == "NFL_EXACT_VERSION_EVIDENCE_CONSISTENT"
    return {
        "status": "EVIDENCE_CONSISTENT" if consistent else "HOLD",
        "code": code,
        "verified_grade_count": n,
        "metrics": dict(metrics or {}),
        "independent_certification_required": True,
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": CAN_EXECUTE,
    }


def audit_exact_version(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_model_version: str,
    expected_model_artifact_id: str,
    expected_artifact_checksum: str,
    health: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay a verified cohort, failing closed on any mixed or altered receipt.

    This is an *independent arithmetic consistency observer*, not fitted-model
    certification. In particular, PASS cannot attest source-rights, independent
    grade provenance, model sharpness, or the protected terminal reducer.
    """
    n = len(rows) if isinstance(rows, (list, tuple)) else 0
    if n < MIN_FORWARD_GRADED:
        return _result("NFL_FORWARD_EVIDENCE_INSUFFICIENT", n)
    if not isinstance(health, Mapping):
        return _result("NFL_FORWARD_HEALTH_NOT_PASS", n)
    minimum = health.get("minimum_forward_required")
    if (type(minimum) is not int or minimum < MIN_FORWARD_GRADED
            or type(health.get("graded_n")) is not int
            or health["graded_n"] != n):
        return _result("NFL_FORWARD_HEALTH_COHORT_MISMATCH", n)
    if n < minimum:
        return _result("NFL_FORWARD_EVIDENCE_INSUFFICIENT", n)
    if (health.get("status") != "PASS"
            or health.get("certification_recommendation") != "FORWARD_EVIDENCE_PASS"
            or health.get("blockers") != []):
        return _result("NFL_FORWARD_HEALTH_NOT_PASS", n)
    if (not expected_model_version or not expected_model_artifact_id
            or len(expected_artifact_checksum) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_artifact_checksum.lower())):
        return _result("NFL_FORWARD_GRADE_INVALID", n)

    events: set[str] = set()
    predictions: set[str] = set()
    pairs: list[tuple[float, int, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            return _result("NFL_FORWARD_GRADE_INVALID", n)
        if (row.get("model_version") != expected_model_version
                or str(row.get("model_artifact_id")) != expected_model_artifact_id
                or row.get("artifact_checksum") != expected_artifact_checksum):
            return _result("NFL_EXACT_VERSION_COHORT_MISMATCH", n)
        event = str(row.get("official_event_id") or "")
        prediction = str(row.get("event_prediction_id") or "")
        if (not event or not prediction or event in events or prediction in predictions
                or row.get("source_game_id") != event):
            return _result("NFL_FORWARD_GRADE_IDENTITY_INVALID", n)
        events.add(event)
        predictions.add(prediction)
        start, created = _instant(row.get("event_start_time_utc")), _instant(row.get("prediction_created_at"))
        if start is None or created is None or created >= start:
            return _result("NFL_FORWARD_CHRONOLOGY_INVALID", n)
        p, lower, upper = (_number(row.get(k)) for k in (
            "calibrated_probability", "calibrated_lower_bound", "calibrated_upper_bound"))
        y = row.get("outcome")
        if (None in (p, lower, upper) or not (0.0 < p < 1.0)
                or not (0 <= lower <= p <= upper <= 1)
                or type(y) is not int or y not in (0, 1)
                or row.get("schema_version") != "NFL_FORWARD_SHADOW_GRADE_V1"
                or row.get("can_execute") is not False):
            return _result("NFL_FORWARD_GRADE_INVALID", n)
        computed_brier = (p - y) ** 2
        clipped = min(max(p, 1e-12), 1.0 - 1e-12)
        computed_log = -(y * log(clipped) + (1 - y) * log(1 - clipped))
        actual_brier, actual_log = _number(row.get("brier")), _number(row.get("log_loss"))
        if (actual_brier is None or actual_log is None
                or abs(actual_brier - computed_brier) > _TOLERANCE
                or abs(actual_log - computed_log) > _TOLERANCE):
            return _result("NFL_FORWARD_GRADE_ARITHMETIC_MISMATCH", n)
        pairs.append((p, y, lower))

    bins: list[list[tuple[float, int, float]]] = [[] for _ in range(10)]
    for p, y, lower in pairs:
        bins[min(9, int(p * 10))].append((p, y, lower))
    ece = sum(len(b) / n * abs(sum(p for p, _, _ in b) / len(b) -
                                sum(y for _, y, _ in b) / len(b))
              for b in bins if b)
    metrics = {
        "brier": sum((p - y) ** 2 for p, y, _ in pairs) / n,
        "log_loss": sum(-(y * log(min(max(p, 1e-12), 1 - 1e-12)) +
                           (1 - y) * log(1 - min(max(p, 1e-12), 1 - 1e-12)))
                        for p, y, _ in pairs) / n,
        "ece": ece,
        "calibration_bias": sum(p - y for p, y, _ in pairs) / n,
        "lower_bound_reliability_gap": sum(y - lower for _, y, lower in pairs) / n,
    }
    if any((_number(health.get(name)) is None or
            abs(_number(health[name]) - value) > _TOLERANCE)
           for name, value in metrics.items()):
        return _result("NFL_FORWARD_METRIC_REPLAY_MISMATCH", n, metrics)
    return _result("NFL_EXACT_VERSION_EVIDENCE_CONSISTENT", n, metrics)
