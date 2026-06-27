"""
data_contract.py  —  Module B: Required Data Contract Enforcement
WOW v16 / Section 9A

Every prop object must carry all required fields before any gate or scoring
runs. A prop with any missing required field receives terminal bucket
DATA_CONTRACT_FAIL and approval scoring does not run. The prop still appears
in full-board output with terminal label DATA_CONTRACT_FAIL and missing-field
blockers listed — no hidden cuts.

Raw data present but not scored = INPUT_FAILURE (set by board_intake).
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

# Row-level fields (present on the normalized prop row after board_intake)
ROW_REQUIRED_FIELDS: list[str] = [
    "player",          # or team
    "sport",
    "prop_type",       # market
    "line",
    "direction",       # side: MORE / LESS / OVER / UNDER
]

# Enrichment-level fields (supplied via the enrichment dict at pipeline time)
ENRICHMENT_REQUIRED_FIELDS: list[str] = [
    "opponent",
    "game_date",
    "book_or_platform",
    "odds_or_payout",
    "data_timestamp",
    "status_timestamp",
    "role_timestamp",
    "l5_values",
    "l10_values",
    "l10_median",
    "l10_mean",
    "l5_line_used",
    "market_no_vig_probability",
    "model_probability_ledger",
    "payout_context",
    "failure_path_matrix",
    "directional_exposure_tags",
    "provisional_label",
    "validation_status",
    "blocker_reason_if_blocked",
]

ALL_REQUIRED_FIELDS: list[str] = ROW_REQUIRED_FIELDS + ENRICHMENT_REQUIRED_FIELDS


def _is_present(value: Any) -> bool:
    """Return True when a value is considered 'present' (not missing)."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def run(row: dict[str, Any], enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Check every required field. Returns a contract result dict and,
    if any field is missing, stamps the row with DATA_CONTRACT_FAIL.

    The row is modified in-place (terminal_label, blockers).

    Returns:
        {
          passed:         bool
          missing_fields: list[str]
          checked_fields: list[str]
          code:           "CONTRACT_PASS" | "DATA_CONTRACT_FAIL"
          detail:         str
        }
    """
    enr = enrichment or {}
    missing: list[str] = []
    checked: list[str] = []

    # Check row-level fields
    for field in ROW_REQUIRED_FIELDS:
        checked.append(field)
        val = row.get(field)
        # Special case: "player" can be substituted by "team"
        if field == "player" and not _is_present(val):
            val = row.get("team")
        if not _is_present(val):
            missing.append(field)

    # Check enrichment-level fields
    for field in ENRICHMENT_REQUIRED_FIELDS:
        checked.append(field)
        val = enr.get(field)
        if not _is_present(val):
            # Some fields have explicit "not available" sentinels that are valid
            if field == "market_no_vig_probability" and val in (
                "SOURCE_CONFLICT", "MARKET_UNAVAILABLE"
            ):
                continue
            if field == "blocker_reason_if_blocked":
                # Only required when validation_status == FAILED
                vs = enr.get("validation_status")
                if vs != "FAILED":
                    continue
            missing.append(field)

    passed = len(missing) == 0

    result: dict[str, Any] = {
        "passed":         passed,
        "missing_fields": missing,
        "checked_fields": checked,
        "code":           "CONTRACT_PASS" if passed else "DATA_CONTRACT_FAIL",
        "detail": (
            "All required fields present." if passed
            else f"{len(missing)} required field(s) missing: {', '.join(missing)}"
        ),
    }

    if not passed:
        row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
        for f in missing:
            row["blockers"].append(f"DATA_CONTRACT_FAIL:missing_field:{f}")
        row.setdefault("gates", {})["data_contract"] = result
    else:
        row.setdefault("gates", {})["data_contract"] = result

    return result


def check_fields_present(prop: dict[str, Any], fields: list[str]) -> list[str]:
    """
    Utility: return list of fields that are NOT present in prop.
    Useful for partial checks (e.g. checking only enrichment subset).
    """
    return [f for f in fields if not _is_present(prop.get(f))]
