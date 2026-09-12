"""Semantic completion gate for governed V17 Top-10 workflows.

Each in-scope source row must terminate exactly once as either a valid
controlling-model probability package or an explicit typed blocker. Generic
PENDING/NOT_CALLED/UNRESOLVED states, omissions, duplicate receipts, and
malformed model packages fail closed. This module never creates or changes a
sporting probability. can_execute remains false.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

TOP10_INCOMPLETE_MODEL_RECONCILIATION = "TOP10_INCOMPLETE_MODEL_RECONCILIATION"
_GENERIC_NON_BLOCKERS = {"", "NONE", "NULL", "UNKNOWN", "UNRESOLVED", "PENDING", "NOT_CALLED", "NOT CALLED", "HELD", "REJECTED", "COMPLETED", "INCOMPLETE"}


def _text(value: Any) -> str:
    return "_".join(str(value or "").strip().upper().replace("-", " ").split())


def is_required_top10_row(row: Any) -> bool:
    sport = _text(getattr(row, "sport", None) if not isinstance(row, dict) else row.get("sport"))
    stat = _text(getattr(row, "stat_type", None) if not isinstance(row, dict) else row.get("stat_type"))
    if sport in {"TENNIS", "SOCCER", "FOOTBALL_SOCCER"}:
        return True
    if sport != "MLB":
        return False
    if stat in {
        "1IP", "1ST_INNING_PITCHES", "1ST_INNING_PITCH_COUNT", "1ST_INNING_PITCHES_THROWN",
        "FIRST_INNING_PITCHES", "FIRST_INNING_PITCH_COUNT", "FIRST_INNING_PITCHES_THROWN",
        "PITCHER_STRIKEOUTS", "PITCHER_STRIKEOUT", "PITCHER_K", "PITCHER_KS", "K", "KS", "SO", "STRIKEOUT", "STRIKEOUTS",
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
    for key in ("prediction", "probability_package", "model_probability_package", "governed_probability_package", "full_model_probability"):
        nested = result.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    return candidates


def has_valid_model_package(outcome: dict[str, Any]) -> bool:
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
    if outcome.get("terminal_status") not in {"HELD", "REJECTED"}:
        return False
    code = _text(outcome.get("code"))
    if code in _GENERIC_NON_BLOCKERS:
        return False
    detail = outcome.get("detail") if isinstance(outcome.get("detail"), dict) else {}
    specialist_invoked = bool(
        detail.get("specialist_invoked") is True
        or detail.get("scorer_invoked") is True
        or outcome.get("specialist_invoked") is True
        or outcome.get("scorer_invoked") is True
    )
    if specialist_invoked and code == "MODEL_UNAVAILABLE":
        return False
    return True


def reconcile_top10_rows(source_rows: list[Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    scoped: list[tuple[str, Any]] = []
    for index, row in enumerate(source_rows):
        if not is_required_top10_row(row):
            continue
        supplied = getattr(row, "row_key", None) if not isinstance(row, dict) else row.get("row_key")
        scoped.append((str(supplied or f"row-{index + 1}"), row))

    expected_ids = [row_key for row_key, _ in scoped]
    expected_counts = Counter(expected_ids)
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
            omitted_row_ids.append(row_id); unreconciled_row_ids.append(row_id); classifications[row_id] = "OMITTED"; continue
        if len(receipts) != 1:
            duplicate_receipt_row_ids.append(row_id); unreconciled_row_ids.append(row_id); classifications[row_id] = "DUPLICATE_RECEIPTS"; continue
        outcome = receipts[0]
        if has_valid_model_package(outcome):
            valid_package_row_ids.append(row_id); classifications[row_id] = "VALID_MODEL_PACKAGE"
        elif has_typed_blocker(outcome):
            typed_blocker_row_ids.append(row_id); classifications[row_id] = "TYPED_BLOCKER"
        else:
            unreconciled_row_ids.append(row_id); classifications[row_id] = "UNRECONCILED"

    unreconciled_row_ids.extend(duplicate_source_ids)
    unreconciled_row_ids = sorted(set(unreconciled_row_ids))
    omitted_row_ids = sorted(set(omitted_row_ids))
    duplicate_receipt_row_ids = sorted(set(duplicate_receipt_row_ids))
    rows_in_scope = len(expected_ids)
    package_count = len(valid_package_row_ids)
    blocker_count = len(typed_blocker_row_ids)
    balanced = rows_in_scope == package_count + blocker_count and not unreconciled_row_ids and not duplicate_source_ids and not duplicate_receipt_row_ids

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
    out = dict(response)
    audit = reconcile_top10_rows(source_rows, list(out.get("rows") or []))
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
