"""
gate_engine/command_center/candidate_intake.py
WOW Sports Intelligence Command Center — Phase 1

Canonical candidate envelope + batch intake validation.

Every candidate entering the command center is normalized to this envelope.
Unknown or missing required fields produce CC:INTAKE_* blockers; the
candidate still passes through (with its blockers) so the run log is
complete — no silent drops.

can_execute = False (unconditional)
"""
from __future__ import annotations

import uuid
from typing import Any

from .cc_labels import (
    CAN_EXECUTE, ALL_FAMILIES,
    CC_INTAKE_INVALID,
    CC_INTAKE_MISSING_FAMILY,
    CC_INTAKE_MISSING_DATE,
    CC_INTAKE_MISSING_IDENTITY,
    FAMILY_PROP, FAMILY_LLP,
    FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER,
)

# Fields each engine family must produce in its result envelope
_ENGINE_RESULT_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    FAMILY_PROP:           frozenset({"terminal_labels", "final_card", "can_execute"}),
    FAMILY_LLP:            frozenset({"terminal_label", "can_execute"}),
    FAMILY_KALSHI_SPORTS:  frozenset({"final_ranked_singles", "can_execute"}),
    FAMILY_KALSHI_WEATHER: frozenset({"final_ranked_singles", "can_execute"}),
}


# ---------------------------------------------------------------------------
# Canonical candidate envelope
# ---------------------------------------------------------------------------

def make_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise one raw candidate dict into the canonical CC envelope.
    Fills defaults; never raises.  Blockers are added for missing fields.
    """
    candidate_id = str(raw.get("candidate_id") or uuid.uuid4())
    market_family = (raw.get("market_family") or "").upper().strip()
    slate_date    = (raw.get("slate_date") or raw.get("target_date") or "").strip()
    sport         = (raw.get("sport") or "").upper().strip()
    player        = raw.get("player") or raw.get("player_name") or None
    event_id      = raw.get("event_id") or raw.get("event_key") or None
    prop_type     = raw.get("prop_type") or None
    line          = raw.get("line") or None
    direction     = (raw.get("direction") or "").upper().strip() or None

    cc_blockers: list[str] = list(raw.get("cc_blockers") or [])

    # Validate market family
    if market_family not in ALL_FAMILIES:
        cc_blockers.append(CC_INTAKE_MISSING_FAMILY)
        intake_valid = False
    else:
        intake_valid = True

    # Validate date
    if not slate_date:
        cc_blockers.append(CC_INTAKE_MISSING_DATE)
        intake_valid = False

    # Validate identity — need at least player OR event_id
    if not player and not event_id:
        cc_blockers.append(CC_INTAKE_MISSING_IDENTITY)
        intake_valid = False

    if cc_blockers and CC_INTAKE_INVALID not in cc_blockers:
        cc_blockers.append(CC_INTAKE_INVALID)

    return {
        # Identity
        "candidate_id":   candidate_id,
        "market_family":  market_family if market_family in ALL_FAMILIES else None,
        "sport":          sport or None,
        "player":         player,
        "event_id":       event_id,
        "prop_type":      prop_type,
        "line":           line,
        "direction":      direction,
        "slate_date":     slate_date or None,
        # Raw data preserved
        "raw_data":       raw,
        # Engine result — populated after routing + engine dispatch
        "engine_result":  None,
        "engine_label":   None,
        "engine_blockers": [],
        # CC orchestration fields
        "cc_ceiling":     None,
        "cc_blockers":    cc_blockers,
        "cc_namespace":   None,
        "intake_valid":   intake_valid,
        # Kalshi isolation
        "kalshi_recovery_caps_applied": False,
        "kalshi_recovery_rejection":    None,
        # Shared service results
        "slate_integrity_ok":   None,
        "exposure_conflict":    False,
        "final_refresh_ok":     None,
        "exact_line_audit_ok":  None,
        # Reconciliation
        "reconciliation_status": None,
        "final_label":           None,
        # Governance
        "can_execute":  CAN_EXECUTE,   # always False
        "dry_run_only": True,
    }


def validate_batch(
    raw_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Normalise and intake-validate a list of raw candidates.

    Returns
    -------
    (valid, invalid)  — two lists of canonical envelopes.
    'invalid' envelopes have CC:INTAKE_INVALID in cc_blockers.
    Both lists are complete; callers decide how to handle invalids.
    """
    valid:   list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for raw in raw_candidates:
        env = make_envelope(raw)
        if env["intake_valid"]:
            valid.append(env)
        else:
            invalid.append(env)

    return valid, invalid


def check_engine_result_keys(
    family: str, result: dict[str, Any]
) -> list[str]:
    """
    Return list of missing required keys from an engine result.
    Empty list → result is well-formed for this family.
    """
    required = _ENGINE_RESULT_REQUIRED_KEYS.get(family, frozenset())
    return [k for k in required if k not in result]


def extract_engine_label(
    family: str, engine_result: dict[str, Any]
) -> str | None:
    """
    Extract the primary terminal label from an engine result envelope.
    Returns None if the structure is unrecognised.
    """
    if not engine_result:
        return None

    if family == FAMILY_PROP:
        # gate_engine returns terminal_labels = {row_id: label}
        # and final_card = [rows with terminal_label]
        labels = engine_result.get("terminal_labels") or {}
        if labels:
            return list(labels.values())[0]

    elif family == FAMILY_LLP:
        return engine_result.get("terminal_label")

    elif family in (FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER):
        # final_ranked_singles is a list; first entry has the label
        pool = engine_result.get("final_ranked_singles") or []
        if pool:
            return pool[0].get("terminal_label") or pool[0].get("label")

    return engine_result.get("terminal_label") or engine_result.get("label")
