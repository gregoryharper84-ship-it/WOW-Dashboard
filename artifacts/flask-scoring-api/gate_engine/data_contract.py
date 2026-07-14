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


def run_intake(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0 — Section 9A, Step 1.

    Phase-1 contract check: row-level required fields only.

    Row-level fields (player, sport, prop_type, line, direction) must be
    present immediately — a missing row-level field is still an immediate
    DATA_CONTRACT_FAIL. Enrichment-level fields that are missing are noted
    as FIELD_MISSING_AT_INTAKE and returned for the acquisition ladder to
    attempt; they do NOT terminate the row here.

    Returns:
        {
          row_level_fail:     bool   — True → row is terminated
          enrichment_missing: list   — enrichment fields absent at intake
          row_missing:        list   — row-level fields that were missing
        }
    """
    enr = enrichment or {}
    row_missing:  list[str] = []
    enr_missing:  list[str] = []

    for field in ROW_REQUIRED_FIELDS:
        val = row.get(field)
        if field == "player" and not _is_present(val):
            val = row.get("team")
        if not _is_present(val):
            row_missing.append(field)

    for field in ENRICHMENT_REQUIRED_FIELDS:
        val = enr.get(field)
        if not _is_present(val):
            if field == "market_no_vig_probability" and val in (
                "SOURCE_CONFLICT", "MARKET_UNAVAILABLE"
            ):
                continue
            if field == "blocker_reason_if_blocked":
                if enr.get("validation_status") != "FAILED":
                    continue
            enr_missing.append(field)

    row_level_fail = len(row_missing) > 0

    if row_level_fail:
        row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
        for f in row_missing:
            row["blockers"].append(f"DATA_CONTRACT_FAIL:missing_field:{f}")
        row.setdefault("gates", {})["data_contract_intake"] = {
            "passed":             False,
            "row_level_fail":     True,
            "row_missing":        row_missing,
            "enrichment_missing": enr_missing,
            "code":               "DATA_CONTRACT_FAIL:ROW_LEVEL",
        }
    else:
        row.setdefault("gates", {})["data_contract_intake"] = {
            "passed":             len(enr_missing) == 0,
            "row_level_fail":     False,
            "row_missing":        [],
            "enrichment_missing": enr_missing,
            "code": (
                "CONTRACT_PASS_INTAKE"
                if not enr_missing
                else f"FIELD_MISSING_AT_INTAKE:{len(enr_missing)}_enrichment_fields"
            ),
        }

    return {
        "row_level_fail":     row_level_fail,
        "enrichment_missing": enr_missing,
        "row_missing":        row_missing,
    }


def run_deferred(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
    tracker: "Any | None" = None,
) -> dict[str, Any]:
    """
    WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0 — Section 9A, Step 4.

    Phase-2 contract check: enrichment fields only, after the acquisition
    ladder has had a chance to retrieve or reconstruct missing data.

    Only called when run_intake() found enrichment_missing and the row was
    not terminated. If fields are still missing after acquisition, the row
    receives DATA_CONTRACT_FAIL here.

    Returns the same shape as run().
    """
    enr = enrichment or {}
    missing:  list[str] = []

    for field in ENRICHMENT_REQUIRED_FIELDS:
        val = enr.get(field)
        if not _is_present(val):
            if field == "market_no_vig_probability" and val in (
                "SOURCE_CONFLICT", "MARKET_UNAVAILABLE"
            ):
                continue
            if field == "blocker_reason_if_blocked":
                if enr.get("validation_status") != "FAILED":
                    continue
            missing.append(field)

    passed = len(missing) == 0

    result: dict[str, Any] = {
        "passed":         passed,
        "missing_fields": missing,
        "checked_fields": list(ENRICHMENT_REQUIRED_FIELDS),
        "code":           "CONTRACT_PASS" if passed else "DATA_CONTRACT_FAIL",
        "detail": (
            "All enrichment fields present after acquisition."
            if passed
            else f"{len(missing)} enrichment field(s) still missing after acquisition: {', '.join(missing)}"
        ),
        "phase": "deferred",
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
