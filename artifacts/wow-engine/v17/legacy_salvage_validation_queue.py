"""Non-authoritative WOW V17 recovery queue over evidence preflight diagnostics.

Never changes the scorer, model, probability, calibrated bound, terminal
reducer, execution authority or immutable pregame records. Advice only.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEBT_FIELDS: tuple[str, ...] = (
    "ledger_debt",
    "median_debt",
    "hit_count_debt",
    "status_debt",
    "role_debt",
    "market_debt",
    "payout_debt",
    "failure_path_debt",
    "source_provenance_debt",
    "cache_debt",
)

_SOURCE_ISSUES = {
    "invalid_or_naive_source_timestamp",
    "source_timestamp_in_future",
    "source_evidence_stale",
    "invalid_material_update_timestamp",
}
_STATUS_ISSUES = {
    "pregame_event_ineligible",
    "event_status_unverified",
    "scheduled_event_has_started",
    "invalid_or_naive_start_timestamp",
    "critical_participant_unavailable",
    "material_update_after_model",
    "material_update_without_model_timestamp",
}
_IDENTITY_FIELDS = {
    "row_key", "official_event_id", "participant", "exact_selection", "outcome_space",
    "settlement_rule", "stat_type", "period", "exact_line", "direction",
}


def _action_list(missing: list[str], issues: list[str], market: str) -> list[str]:
    actions: list[str] = []
    if "invalid_row_key" in issues or "row_key" in missing:
        actions.append("RESOLVE_CANONICAL_ROW_KEY")
    if any(f in _IDENTITY_FIELDS for f in missing) or "event_status_unverified" in issues:
        actions.append("VERIFY_OFFICIAL_IDENTITY_AND_EXACT_CONTRACT")
    if any(f in {"scheduled_start_utc", "event_status"} for f in missing) or (
        {"scheduled_event_has_started", "pregame_event_ineligible", "invalid_or_naive_start_timestamp"} & set(issues)
    ):
        actions.append("REFRESH_OFFICIAL_EVENT_SCHEDULE_STATUS")
    if _SOURCE_ISSUES.intersection(issues) or "source_timestamp" in missing:
        actions.append("REFRESH_AND_TIMESTAMP_SOURCE_EVIDENCE")
    if "critical_participant_unavailable" in issues:
        actions.append("VERIFY_PARTICIPANT_AVAILABILITY_AND_ROUTE")
    if {"material_update_after_model", "material_update_without_model_timestamp"} & set(issues):
        actions.append("REHYDRATE_CERTIFIED_MODEL_INPUTS_IF_PREGAME")
    if market != "EXACT_LINE":
        actions.append("FIND_EXACT_LINE_PRICE_FOR_MARKET_AUDIT_ONLY")
    return actions


def build_validation_queue(
    preflight: Mapping[str, Any],
    *,
    market_analysis_requested: bool = False,
) -> dict[str, Any]:
    """Map every evidence audit row to diagnostic debt and recovery hints.

    Debt values are True, False or None (unknown). Unknown is never zero.
    Market debt does not become model debt. This is not a scoring API.
    """
    rows = preflight.get("rows")
    if not isinstance(rows, list) or preflight.get("rows_received") != len(rows):
        raise ValueError("exact-once preflight batch required")
    if preflight.get("can_execute") is not False or preflight.get("diagnostic_only") is not True:
        raise ValueError("must receive a dry-run diagnostic preflight")
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("diagnostic_only") is not True:
            raise ValueError("invalid audit row")
        missing = list(row.get("source_fields_missing") or ())
        issues = list(row.get("sporting_evidence_issues") or ())
        market = row.get("market_evidence_type") or "NO_MARKET"
        debts: dict[str, bool | None] = {field: None for field in DEBT_FIELDS}
        debts["source_provenance_debt"] = True if (missing or (_SOURCE_ISSUES & set(issues))
                                                  or "invalid_row_key" in issues) else None
        debts["status_debt"] = True if (set(issues) & _STATUS_ISSUES or
                                    any(f in {"event_status", "scheduled_start_utc"}
                                        for f in missing)) else None
        debts["market_debt"] = True if (market_analysis_requested and market != "EXACT_LINE") else None
        actions = _action_list(missing, issues, market if market_analysis_requested else "EXACT_LINE")
        output.append({
            "row_key": row.get("row_key"),
            "missing_fields": missing,
            "evidence_issues": issues,
            "market_evidence_type": market,
            "confidence_debt": debts,
            "recovery_actions": actions,
            "market_analysis_requested": bool(market_analysis_requested),
            "diagnostic_only": True,
            "terminal_label": None,
            "model_probability": None,
            "calibrated_probability": None,
            "calibrated_lower_bound": None,
            "can_execute": False,
        })
    return {
        "rows_received": len(rows),
        "rows_reported": len(output),
        "rows": output,
        "diagnostic_only": True,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "probability_authority": False,
    }
