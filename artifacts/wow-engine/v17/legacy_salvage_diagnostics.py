"""V17 legacy-salvage *diagnostics only*: no scoring, qualification, or terminal authority.

This module is deliberately unconnected to live V17 routing. Engineering may
integrate a verified subset into the V17 evidence lane only after independent
tests and applicable certification. It never produces a model probability,
terminal verdict, wager approval, or execution action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

PREGAME_EXCLUDED = frozenset(
    {"STARTED", "LIVE", "FINAL", "POSTPONED", "CANCELED", "CANCELLED"}
)
PREGAME_ALLOWED = frozenset({"SCHEDULED", "PREGAME", "PRE", "NOT_STARTED"})
MARKET_EVIDENCE_TYPES = frozenset(
    {"EXACT_LINE", "ADJACENT_LINE", "NO_MARKET"}
)


def _utc(value: Any) -> datetime | None:
    """Parse explicitly zone-aware ISO timestamps; no naive-local guessing."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _required_fields(mode: str) -> tuple[str, ...]:
    common = (
        "row_key", "official_event_id", "event_status",
        "participant", "market", "source_timestamp", "scheduled_start_utc",
    )
    if mode == "PROP":
        return common + ("stat_type", "period", "exact_line", "direction")
    if mode == "TEAM_EVENT":
        return common + ("exact_selection", "outcome_space", "settlement_rule")
    raise ValueError("mode must be PROP or TEAM_EVENT")


def audit_candidate(
    record: Mapping[str, Any],
    *,
    mode: str,
    now_utc: datetime,
    source_max_age_seconds: int,
) -> dict[str, Any]:
    """Return evidence deficits only, never an approval or probability.

    source_max_age_seconds must be set by the authorized V17 route. The legacy
    25-hour WNBA default is NOT globally imposed on lineup/status/market data.
    """
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone aware")
    if not isinstance(source_max_age_seconds, int) or isinstance(
        source_max_age_seconds, bool
    ) or source_max_age_seconds < 0:
        raise ValueError("source_max_age_seconds must be a nonnegative int")

    mode = mode.upper()
    missing = [
        field for field in _required_fields(mode)
        if record.get(field) is None or
        (isinstance(record.get(field), str) and not record.get(field).strip())
    ]
    status = str(record.get("event_status") or "").upper()
    source_ts = _utc(record.get("source_timestamp"))
    start_ts = _utc(record.get("scheduled_start_utc"))
    model_ts = _utc(record.get("model_timestamp"))
    latest_update = _utc(record.get("latest_material_update_at"))
    now = now_utc.astimezone(timezone.utc)

    issues: list[str] = []
    if record.get("scheduled_start_utc") and start_ts is None:
        issues.append("invalid_or_naive_start_timestamp")
    elif start_ts and start_ts <= now:
        issues.append("scheduled_event_has_started")
    if status in PREGAME_EXCLUDED:
        issues.append("pregame_event_ineligible")
    elif status and status not in PREGAME_ALLOWED:
        issues.append("event_status_unverified")
    if record.get("source_timestamp") and source_ts is None:
        issues.append("invalid_or_naive_source_timestamp")
    if source_ts is not None:
        if source_ts > now:
            issues.append("source_timestamp_in_future")
        elif (now - source_ts).total_seconds() > source_max_age_seconds:
            issues.append("source_evidence_stale")
    if latest_update and not model_ts:
        issues.append("material_update_without_model_timestamp")
    elif latest_update and model_ts and latest_update > model_ts:
        issues.append("material_update_after_model")
    elif record.get("latest_material_update_at") and latest_update is None:
        issues.append("invalid_material_update_timestamp")
    if record.get("model_timestamp") and model_ts is None:
        issues.append("invalid_or_naive_model_timestamp")

    critical_status = str(record.get("critical_participant_status") or "").strip().upper()
    if critical_status in {"OUT", "INACTIVE", "SCRATCHED", "IR", "SEASON_OUT"}:
        issues.append("critical_participant_unavailable")

    evidence_type = record.get("market_evidence_type") or "NO_MARKET"
    if evidence_type not in MARKET_EVIDENCE_TYPES:
        issues.append("unrecognized_market_evidence_type")
        evidence_type = "NO_MARKET"

    # Missing/adjacent-line market pricing is independent of sporting inputs.
    market_notes = []
    if evidence_type == "NO_MARKET":
        market_notes.append("market_price_unavailable")
    if evidence_type == "ADJACENT_LINE":
        market_notes.append("adjacent_line_not_exact_line_evidence")

    return {
        "row_key": record.get("row_key"),
        "mode": mode,
        "source_fields_missing": missing,
        "sporting_evidence_issues": issues,
        "market_evidence_type": evidence_type,
        "market_evidence_notes": market_notes,
        "model_rerun_recommended": "material_update_after_model" in issues,
        "source_is_fresh": (
            source_ts is not None
            and source_ts <= now
            and (now - source_ts).total_seconds() <= source_max_age_seconds
        ),
        "pregame_eligible_by_status": status in PREGAME_ALLOWED and (
            start_ts is not None and start_ts > now
        ) if status else None,
        "diagnostic_only": True,
        "controlling_specialist_invoked": False,
        "model_probability": None,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "terminal_label": None,
        "can_execute": False,
    }


def audit_rows(
    rows: list[Mapping[str, Any]],
    *,
    mode: str,
    now_utc: datetime,
    source_max_age_seconds: int,
) -> dict[str, Any]:
    """One result per input row (including duplicates); no silent omissions."""
    result = [
        audit_candidate(
            row, mode=mode, now_utc=now_utc,
            source_max_age_seconds=source_max_age_seconds,
        )
        for row in rows
    ]
    seen: set[Any] = set()
    for item in result:
        row_key = item["row_key"]
        if not isinstance(row_key, str) or not row_key.strip():
            item["sporting_evidence_issues"].append("invalid_row_key")
            continue
        if row_key in seen:
            item["sporting_evidence_issues"].append("repeated_row_key_check_identity")
        seen.add(row_key)
    assert len(result) == len(rows)
    return {
        "rows_received": len(rows),
        "rows_reported": len(result),
        "rows": result,
        "diagnostic_only": True,
        "can_execute": False,
    }
