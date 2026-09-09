"""Semantic completion gate for governed V17 Top-10 workflows.

A terminal-looking row is not automatically reconciled.  For the target Top-10
families, each source row must terminate exactly once as either a valid
controlling-model probability package or an explicit typed blocker.  Generic
PENDING/NOT_CALLED/UNRESOLVED states, omissions, duplicate receipts, and
malformed model packages fail closed.

This module never creates or changes a sporting probability.  It only audits
whether the controlling route produced a valid governed result or an explicit
failure receipt.  can_execute remains false.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable


TOP10_INCOMPLETE_MODEL_RECONCILIATION = "TOP10_INCOMPLETE_MODEL_RECONCILIATION"

_GENERIC_NON_BLOCKERS = {
    "",
    "NONE",
    "NULL",
    "UNKNOWN",
    "UNRESOLVED",
    "PENDING",
    "NOT_CALLED",
    "NOT CALLED",
    "HELD",
    "REJECTED",
    "COMPLETED",
    "INCOMPLETE",
}


def _text(value: Any) -> str:
    return "_".join(str(value or "").strip().upper().replace("-", " ").split())


def is_required_top10_row(row: Any) -> bool:
    """Return True only for the families covered by the Sept-9 completion fix.

    MLB coverage is intentionally narrow: 1IP, pitcher strikeouts, Fantasy
    Score, and 95+ MPH pitch rows.  Tennis and soccer scalar/player rows routed
    through /score-pick-request are all in scope.  Team/event tennis and soccer
    outcomes belong to the LLP lane and are enforced by the host/daily
    finalizer contract rather than being misrouted through this prop endpoint.
    """
    sport = _text(getattr(row, "sport", None) if not isinstance(row, dict) else row.get("sport"))
    stat = _text(getattr(row, "stat_type", None) if not isinstance(row, dict) else row.get("stat_type"))

    if sport in {"TENNIS", "SOCCER", "FOOTBALL_SOCCER"}:
        return True
    if sport != "MLB":
        return False

    if stat in {
        "1IP",
        "1ST_INNING_PITCHES",
        "1ST_INNING_PITCH_COUNT",
        "1ST_INNING_PITCHES_THROWN",
        "FIRST_INNING_PITCHES",
        "FIRST_INNING_PITCH_COUNT",
        "FIRST_INNING_PITCHES_THROWN",
        "PITCHER_STRIKEOUTS",
        "PITCHER_STRIKEOUT",
        "PITCHER_K",
        "PITCHER_KS",
        "K",
        "KS",
        "SO",
        "STRIKEOUT",
        "STRIKEOUTS",
    }:
        return True
    if "FANTASY" in stat and "SCORE" in stat:
        return True
    if "95" in stat and "MPH" in stat:
        return True
    return False


def _finite_probability(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and 0.0 <= numeric <= 1.0


def _candidate_probability_dicts(outcome: dict[str, Any]) -> Iterable[dict[str, Any]]:
    result = outcome.get("result")
    if not isinstance(result, dict):
        return []
    candidates: list[dict[str, Any]] = [result]
    for key in (
        "prediction",
        "probability_package",
        "model_probability_package",
        "governed_probability_package",
        "full_model_probability",
    ):
        nested = result.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    return candidates


def has_valid_model_package(outcome: dict[str, Any]) -> bool:
    """Validate the minimum numeric package needed for governed ranking.

    The runtime uses both ``calibrated_probability_lower_bound`` and
    ``calibrated_lower_bound`` across specialist generations, so both are
    accepted.  A generic ``lower_bound`` is accepted only inside an evaluated
    specialist result (not as standalone evidence).  This deliberately does
    not require market/payout evidence; sporting probability is a separate
    contract.
    """
    if outcome.get("model_evaluated") is not True:
        return False

    for package in _candidate_probability_dicts(outcome):
        calibrated = package.get("calibrated_probability")
        lower = package.get("calibrated_probability_lower_bound")
        if lower is None:
            lower = package.get("calibrated_lower_bound")
        if lower is None:
            lower = package.get("lower_bound")
        if _finite_probability(calibrated) and _finite_probability(lower):
            return True
    return False


def has_typed_blocker(outcome: dict[str, Any]) -> bool:
    """Return True only for an explicit terminal failure/blocker receipt."""
    if outcome.get("terminal_status") not in {"HELD", "REJECTED"}:
        return False

    code = _text(outcome.get("code"))
    if code in _GENERIC_NON_BLOCKERS:
        return False

    # V17 semantics: once a scorer was invoked, MODEL_UNAVAILABLE cannot be
    # used as a catch-all for a thrown/timed-out/invalid scorer invocation.
    detail = outcome.get("detail") if isinstance(outcome.get("detail"), dict) else {}
    scorer_invoked = bool(
        outcome.get("scoring_attempted") is True
        or detail.get("scoring_attempted") is True
        or detail.get("specialist_invoked") is True
    )
    if scorer_invoked and code == "MODEL_UNAVAILABLE":
        return False

    return True


def reconcile_top10_rows(source_rows: list[Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Audit exact-once semantic reconciliation for the target Top-10 rows."""
    scoped: list[tuple[str, Any]] = []
    for index, row in enumerate(source_rows):
        if not is_required_top10_row(row):
            continue
        supplied = getattr(row, "row_key", None) if not isinstance(row, dict) else row.get("row_key")
        row_key = str(supplied or f"row-{index + 1}")
        scoped.append((row_key, row))

    expected_ids = [row_key for row_key, _ in scoped]
    expected_counts = Counter(expected_ids)
    outcome_counts = Counter(str(outcome.get("row_key") or "") for outcome in outcomes)
    outcomes_by_id: dict[str, list[dict[str, Any]]] = {}
    for outcome in outcomes:
        outcomes_by_id.setdefault(str(outcome.get("row_key") or ""), []).append(outcome)

    duplicate_source_ids = sorted(row_id for row_id, count in expected_counts.items() if count != 1)
    omitted_row_ids: list[str] = []
    duplicate_receipt_row_ids: list[str] = []
    valid_package_row_ids: list[str] = []
    typed_blocker_row_ids: list[str] = []
    unreconciled_row_ids: list[str] = []
    classifications: dict[str, str] = {}

    for row_id in expected_ids:
        receipts = outcomes_by_id.get(row_id, [])
        if not receipts:
            omitted_row_ids.append(row_id)
            unreconciled_row_ids.append(row_id)
            classifications[row_id] = "OMITTED"
            continue
        if len(receipts) != 1:
            duplicate_receipt_row_ids.append(row_id)
            unreconciled_row_ids.append(row_id)
            classifications[row_id] = "DUPLICATE_RECEIPTS"
            continue

        outcome = receipts[0]
        model_package = has_valid_model_package(outcome)
        typed_blocker = has_typed_blocker(outcome)
        if model_package:
            # Model-package classification takes precedence so a model-rejected
            # package is counted once, not simultaneously as package+blocker.
            valid_package_row_ids.append(row_id)
            classifications[row_id] = "VALID_MODEL_PACKAGE"
        elif typed_blocker:
            typed_blocker_row_ids.append(row_id)
            classifications[row_id] = "TYPED_BLOCKER"
        else:
            unreconciled_row_ids.append(row_id)
            classifications[row_id] = "UNRECONCILED"

    # Duplicate source identities make exact-once accounting ambiguous even if
    # the raw receipt count happens to balance numerically.
    unreconciled_row_ids.extend(duplicate_source_ids)
    unreconciled_row_ids = sorted(set(unreconciled_row_ids))
    omitted_row_ids = sorted(set(omitted_row_ids))
    duplicate_receipt_row_ids = sorted(set(duplicate_receipt_row_ids))

    rows_in_scope = len(expected_ids)
    package_count = len(valid_package_row_ids)
    blocker_count = len(typed_blocker_row_ids)
    balanced = (
        rows_in_scope == package_count + blocker_count
        and not unreconciled_row_ids
        and not duplicate_source_ids
        and not duplicate_receipt_row_ids
    )

    return {
        "required": rows_in_scope > 0,
        "rows_in_scope": rows_in_scope,
        "rows_with_valid_model_package": package_count,
        "rows_with_typed_blocker": blocker_count,
        "balanced": balanced,
        "completion_blocker": None if balanced else TOP10_INCOMPLETE_MODEL_RECONCILIATION,
        "valid_model_package_row_ids": valid_package_row_ids,
        "typed_blocker_row_ids": typed_blocker_row_ids,
        "unreconciled_row_ids": unreconciled_row_ids,
        "omitted_row_ids": omitted_row_ids,
        "duplicate_source_row_ids": duplicate_source_ids,
        "duplicate_receipt_row_ids": duplicate_receipt_row_ids,
        "classifications": classifications,
        "can_execute": False,
    }


def enforce_top10_completion(response: dict[str, Any], source_rows: list[Any]) -> dict[str, Any]:
    """Attach the audit and fail closed without mutating row probabilities."""
    out = dict(response)
    outcomes = list(out.get("rows") or [])
    audit = reconcile_top10_rows(source_rows, outcomes)
    out["top10_model_reconciliation"] = audit

    if audit["required"] and not audit["balanced"]:
        out["ok"] = False
        out["run_controller_status"] = "BLOCKED"
        out["reconciliation_pass"] = False
        out["completion_blocker"] = TOP10_INCOMPLETE_MODEL_RECONCILIATION
        blockers = list(out.get("blockers") or [])
        if TOP10_INCOMPLETE_MODEL_RECONCILIATION not in blockers:
            blockers.append(TOP10_INCOMPLETE_MODEL_RECONCILIATION)
        out["blockers"] = blockers

    return out
