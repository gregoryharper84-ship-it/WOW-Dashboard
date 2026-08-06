"""
gate_engine/wnba/missing_field_detector.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL
WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS

After primary API / backend acquisition, compares the WNBA opportunity
packet against the required field set and produces an explicit
missing_fields list.  A non-empty list triggers fallback routing.

This module does NOT make any acquisition decisions — it only observes
what is present and what is absent.

Three-tier field classification per WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS:

  CRITICAL_BLOCKING:
    Missing any of these after full fallback exhaustion →
    PACKET_INCOMPLETE_REJECTED (row blocked, no exceptions).

  QUALIFICATION_BLOCKING:
    Missing after exhaustion (when DATA_UNOBTAINABLE) →
    PACKET_PARTIAL_HOLD (critical fields satisfied, but downstream
    probability-qualified labels are capped).

  OPTIONAL_OR_MARKET_DEPENDENT:
    Missing is fine — not listed in REQUIRED_PACKET_FIELDS.

can_execute=False is unconditional.
"""
from __future__ import annotations

from typing import Any

can_execute = False

# ---------------------------------------------------------------------------
# Required packet fields
# Dot-notation paths into the packet dict.
# ---------------------------------------------------------------------------

REQUIRED_PACKET_FIELDS: list[str] = [
    # CRITICAL_BLOCKING — missing after exhaustion → PACKET_INCOMPLETE_REJECTED
    "event_status",
    "role_status.active_status",
    "role_status.role_timestamp",
    "role_status.projected_minutes",
    "box_score_log",
    "l5_ledger",
    "l10_ledger",
    # QUALIFICATION_BLOCKING — missing after exhaustion → PACKET_PARTIAL_HOLD
    "matchup",
    "market_comparison",
    "news_contradiction_check",
]

# ---------------------------------------------------------------------------
# Three-tier field classification
# ---------------------------------------------------------------------------

CRITICAL_BLOCKING_FIELDS: frozenset[str] = frozenset({
    "event_status",
    "role_status.active_status",
    "role_status.role_timestamp",
    "role_status.projected_minutes",
    "box_score_log",
    "l5_ledger",
    "l10_ledger",
})

QUALIFICATION_BLOCKING_FIELDS: frozenset[str] = frozenset({
    "matchup",
    "market_comparison",
    "news_contradiction_check",
})


def get_field_tier(field_path: str) -> str:
    """Return 'CRITICAL', 'QUALIFICATION', or 'OPTIONAL' for a field path."""
    if field_path in CRITICAL_BLOCKING_FIELDS:
        return "CRITICAL"
    if field_path in QUALIFICATION_BLOCKING_FIELDS:
        return "QUALIFICATION"
    return "OPTIONAL"


# ---------------------------------------------------------------------------
# Empty-list semantics
# ---------------------------------------------------------------------------

# Empty list means no data was retrieved — treated as absent for ALL fields.
# The opportunity engine separately gates on MIN_GAMES_REQUIRED; we do not
# need to treat an empty ledger as "present" here.
_ALLOW_EMPTY_LIST: frozenset[str] = frozenset()


def _get_nested(obj: dict[str, Any], dotted_path: str) -> Any:
    """Resolve a dot-notation path into a nested dict.  Returns None if absent."""
    parts   = dotted_path.split(".")
    current: Any = obj
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _is_present(value: Any, field_key: str) -> bool:
    """
    Return True when value is considered 'present' for the given field key.

    Rules:
    - None → absent
    - Empty string → absent
    - Empty list → absent (opportunity engine handles count separately)
    - Empty dict → absent (market_comparison, news_contradiction_check must have content)
    - Any other value → present
    """
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, list) and len(value) == 0:
        return field_key in _ALLOW_EMPTY_LIST
    if isinstance(value, dict) and len(value) == 0:
        return False
    return True


def detect_missing(packet: dict[str, Any]) -> list[str]:
    """
    Compare the packet against REQUIRED_PACKET_FIELDS.

    Returns a list of field paths (dot-notation) that are absent or
    failed presence check.  Empty list = all required fields present.
    """
    missing: list[str] = []
    for field_path in REQUIRED_PACKET_FIELDS:
        value = _get_nested(packet, field_path)
        if not _is_present(value, field_path.split(".")[-1]):
            missing.append(field_path)
    return missing


def classify_missing_fields(missing_fields: list[str]) -> dict[str, list[str]]:
    """
    Classify missing fields into categories for the fallback router.

    Categories match the fallback routing configuration in fallback_router.py:
      event_status, role_status, box_score_log, matchup,
      market_comparison, news_contradiction, other.
    """
    categories: dict[str, list[str]] = {
        "event_status":        [],
        "role_status":         [],
        "box_score_log":       [],
        "matchup":             [],
        "market_comparison":   [],
        "news_contradiction":  [],
        "other":               [],
    }
    for field in missing_fields:
        if "event_status" in field:
            categories["event_status"].append(field)
        elif field.startswith("role_status"):
            categories["role_status"].append(field)
        elif "box_score_log" in field or "l5_ledger" in field or "l10_ledger" in field:
            categories["box_score_log"].append(field)
        elif "matchup" in field:
            categories["matchup"].append(field)
        elif "market_comparison" in field:
            categories["market_comparison"].append(field)
        elif "news_contradiction" in field:
            categories["news_contradiction"].append(field)
        else:
            categories["other"].append(field)
    return {k: v for k, v in categories.items() if v}


def build_coverage_audit(
    packet: dict[str, Any],
    missing_fields: list[str],
) -> dict[str, Any]:
    """
    Build the COVERAGE_AUDIT gate result stored in the acquisition_audit.

    Returns a dict describing which fields are present, which are absent,
    and the pre-fallback packet coverage rate.
    """
    total   = len(REQUIRED_PACKET_FIELDS)
    present = total - len(missing_fields)

    # Tier breakdown
    critical_missing      = [f for f in missing_fields if f in CRITICAL_BLOCKING_FIELDS]
    qualification_missing = [f for f in missing_fields if f in QUALIFICATION_BLOCKING_FIELDS]

    return {
        "gate":                     "COVERAGE_AUDIT",
        "total_required_fields":    total,
        "present_count":            present,
        "missing_count":            len(missing_fields),
        "coverage_pct":             round(100 * present / total, 1) if total else 100.0,
        "missing_fields":           list(missing_fields),
        "critical_missing":         critical_missing,
        "qualification_missing":    qualification_missing,
        "fallback_required":        len(missing_fields) > 0,
        "categories":               classify_missing_fields(missing_fields),
    }
