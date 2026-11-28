"""Verified Decision Availability: a product SLO for governed model output.

Service uptime, green jobs and successful deployments do not prove that an
eligible candidate received a valid, calibrated, publishable probability before
its event began.  This module measures that, and only that, from an attested
candidate inventory.

Every metric has an honest denominator.  A metric whose scope or timestamps are
unattested is reported as ``None`` with a finding, never as zero and never as a
pass.  Missing inputs are never relabelled as model unavailability, and no
metric here can publish, recalibrate or rank anything.

``failure_classes`` keeps capability absence separate from missing inputs,
registry, scoring, Action-invocation and certification failures so the same
repair is not dispatched for six different causes (#1021 observability).
"""
from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

AVAILABILITY_METRICS = (
    "action_completion_rate",
    "model_ready_coverage",
    "publishable_probability_rate",
    "deadline_completion_rate",
    "repeated_blocker_rate",
    "user_facing_availability",
)

DISPOSITIONS = frozenset({"SCORED", "HELD", "REJECTED", "PURGED", "PENDING"})

# Native failure causes.  CAPABILITY_ABSENT is deliberately last-resort and is
# only attested by the caller; nothing here can derive it from an exception.
FAILURE_CLASSES = (
    "CAPABILITY_ABSENT",
    "INPUTS_MISSING",
    "REGISTRY_FAILURE",
    "SCORING_FAILURE",
    "ACTION_FAILURE",
    "CERTIFICATION_HOLD",
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_ts(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _repeated_blockers(
    history: Sequence[Mapping[str, Any]], findings: list[str]
) -> tuple[float | None, int]:
    """Share of repaired blockers that returned after being marked repaired."""
    repaired = 0
    recurred = 0
    for index, item in enumerate(history):
        if not isinstance(item, Mapping):
            findings.append(f"BLOCKER_{index}_NOT_A_MAPPING")
            continue
        if not _text(item.get("code")):
            findings.append(f"BLOCKER_{index}_CODE_MISSING")
            continue
        repaired_at = _parse_ts(item.get("repaired_at"))
        if repaired_at is None:
            # Not yet repaired: it is not eligible to be counted as a repeat.
            continue
        repaired += 1
        recurred_at = _parse_ts(item.get("recurred_at"))
        if recurred_at is not None and recurred_at >= repaired_at:
            recurred += 1
    return _rate(recurred, repaired), repaired


def verified_decision_availability(
    rows: Sequence[Mapping[str, Any]],
    *,
    blocker_history: Sequence[Mapping[str, Any]] = (),
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the availability SLO from an attested eligible-candidate inventory."""
    findings: list[str] = []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
            "audit_complete": False,
            "findings": ["CANDIDATE_INVENTORY_NOT_A_SEQUENCE"],
            "eligible_candidates": None,
            "metrics": {name: None for name in AVAILABILITY_METRICS},
            "failure_classes": {},
            "repaired_blockers": None,
            "targets": {},
        }

    eligible = 0
    unknown_scope = False
    action_proven = 0
    action_unknown = False
    ready_proven = 0
    ready_unknown = False
    publishable = 0
    publish_contested = False
    timely = 0
    deadline_unknown = False
    user_facing = 0
    by_class: Counter[str] = Counter()
    seen: set[str] = set()

    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            findings.append(f"ROW_{index}_NOT_A_MAPPING")
            unknown_scope = True
            continue
        candidate_id = row.get("candidate_id")
        if not _text(candidate_id):
            findings.append(f"ROW_{index}_CANDIDATE_ID_MISSING")
            unknown_scope = True
            continue
        if candidate_id in seen:
            findings.append(f"ROW_{index}_DUPLICATE_CANDIDATE_ID")
            continue
        seen.add(candidate_id)

        eligibility = row.get("eligible")
        if eligibility is not True:
            if eligibility is not False:
                findings.append(f"ROW_{index}_ELIGIBILITY_UNATTESTED")
                unknown_scope = True
            continue
        eligible += 1

        disposition = row.get("disposition")
        if disposition not in DISPOSITIONS:
            findings.append(f"ROW_{index}_DISPOSITION_INVALID")
            unknown_scope = True
            continue

        action_invoked = row.get("action_invoked")
        if action_invoked is True:
            action_proven += 1
        elif action_invoked is not False:
            findings.append(f"ROW_{index}_ACTION_INVOCATION_UNATTESTED")
            action_unknown = True

        model_ready = row.get("model_ready")
        if model_ready is True:
            ready_proven += 1
        elif model_ready is not False:
            findings.append(f"ROW_{index}_MODEL_READY_UNATTESTED")
            ready_unknown = True

        event_start = _parse_ts(row.get("event_start_at"))
        if event_start is None:
            findings.append(f"ROW_{index}_EVENT_START_UNATTESTED")
            deadline_unknown = True
        scored_at = _parse_ts(row.get("scored_at"))
        if disposition == "SCORED" and scored_at is None:
            findings.append(f"ROW_{index}_SCORED_WITHOUT_TIMESTAMP")
            deadline_unknown = True

        claimed_publishable = row.get("publishable")
        is_publishable = claimed_publishable is True
        if claimed_publishable not in (True, False):
            findings.append(f"ROW_{index}_PUBLISHABLE_UNATTESTED")
            publish_contested = True
        if is_publishable and (disposition != "SCORED" or scored_at is None):
            findings.append(f"ROW_{index}_PUBLISHABLE_WITHOUT_SCORED_OUTPUT")
            publish_contested = True
        if is_publishable:
            publishable += 1

        on_time = (
            disposition == "SCORED"
            and event_start is not None
            and scored_at is not None
            and scored_at < event_start
        )
        if on_time:
            timely += 1
        if is_publishable and on_time:
            user_facing += 1

        if disposition != "SCORED":
            failure_class = row.get("failure_class")
            if failure_class in FAILURE_CLASSES:
                by_class[str(failure_class)] += 1
            else:
                findings.append(f"ROW_{index}_FAILURE_CLASS_UNATTESTED")

    repeated_rate, repaired_count = _repeated_blockers(blocker_history, findings)

    def metric(numerator: int, *, blocked: bool) -> float | None:
        if unknown_scope or blocked or not eligible:
            return None
        return _rate(numerator, eligible)

    metrics = {
        "action_completion_rate": metric(action_proven, blocked=action_unknown),
        "model_ready_coverage": metric(ready_proven, blocked=ready_unknown),
        "publishable_probability_rate": metric(publishable, blocked=publish_contested),
        "deadline_completion_rate": metric(timely, blocked=deadline_unknown),
        "repeated_blocker_rate": repeated_rate,
        "user_facing_availability": metric(
            user_facing, blocked=deadline_unknown or publish_contested
        ),
    }

    return {
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "audit_complete": not findings,
        "findings": findings,
        "eligible_candidates": None if unknown_scope else eligible,
        "metrics": metrics,
        "failure_classes": dict(sorted(by_class.items())),
        "repaired_blockers": repaired_count,
        "targets": evaluate_availability_targets(metrics, thresholds or {}),
    }


def evaluate_availability_targets(
    metrics: Mapping[str, Any], targets: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare measured metrics to explicitly supplied targets.

    No target is inferred from history and no threshold is relaxed here: an
    unmeasured metric or an absent target is ``UNKNOWN``, never ``PASS``.
    """
    result: dict[str, Any] = {}
    for name in AVAILABILITY_METRICS:
        target = targets.get(name)
        value = metrics.get(name)
        if target is None:
            result[name] = {"target": None, "value": value, "status": "UNKNOWN"}
            continue
        valid_target = (
            not isinstance(target, bool)
            and isinstance(target, (int, float))
            and isfinite(target)
            and 0 <= target <= 1
        )
        if not valid_target:
            result[name] = {"target": target, "value": value, "status": "INVALID_TARGET"}
            continue
        if value is None or not isinstance(value, (int, float)):
            result[name] = {"target": float(target), "value": None, "status": "UNKNOWN"}
            continue
        result[name] = {
            "target": float(target),
            "value": value,
            "status": "PASS" if value >= target else "BREACH",
        }
    return result


__all__ = [
    "AVAILABILITY_METRICS",
    "CAN_EXECUTE",
    "DISPOSITIONS",
    "FAILURE_CLASSES",
    "TERMINAL_AUTHORITY",
    "evaluate_availability_targets",
    "verified_decision_availability",
]
