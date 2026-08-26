"""
Central handoff policy for the LLP Moneyline Probability Expert.

This module governs object transport only.  It does not calculate probability,
calibration, edge, stake, or terminal labels.  Specialist objects are research
artifacts: they are permanently non-executable and remain shadow-only until a
structurally complete calibration-ledger record and a sport-specific weight
profile are both supplied as independent proof.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

can_execute: bool = False
dry_run_only: bool = True

CONTROLLING_SKILL = "wow.llp-moneyline-probability-expert"
WATCH_ONLY_UNTIL_CALIBRATED = "WATCH_ONLY_UNTIL_CALIBRATED"
CALIBRATED_RESEARCH_RANKING_ELIGIBLE = "CALIBRATED_RESEARCH_RANKING_ELIGIBLE"

_FORBIDDEN_AUTHORITY_FIELDS = frozenset({
    "terminal_label", "final_label", "llp_label", "approved", "playable",
    "stake", "execute", "can_execute", "can_approve_bets", "auto_execute",
    "stake_sizing", "bankroll_allocation",
})
_PROTECTED_FIELDS = (
    "independent_probability",
    "raw_probability",
    "calibrated_probability",
    "calibrated_probability_lower_bound",
    "calibrated_probability_upper_bound",
    "lower_bound",
    "upper_bound",
    "net_edge",
    "blockers",
)
_IDENTITY_FIELDS = ("sport", "team", "opponent", "event_id", "market_type", "slate_date")
_MONEYLINE_MARKET_ALIASES = frozenset({
    "h2h",
    "moneyline",
    "money_line",
    "game_winner",
    "match_winner",
    "winner",
    "outright_winner",
    "outright",
    "1x2",
})


def is_llp_moneyline_candidate(record: Any) -> bool:
    """
    Identify the protected full-game moneyline family from canonical semantics.

    Routing must not depend on optional producer metadata: an unmarked h2h or
    moneyline candidate is still an LLP probability candidate and therefore
    cannot fall through to ordinary prop ranking.
    """
    if not isinstance(record, dict):
        return False
    if record.get("controlling_skill") == CONTROLLING_SKILL:
        return True
    if record.get("market_family") == "OUTRIGHT_WINNER":
        return True
    if record.get("objective") == "OUTRIGHT_WIN_PROBABILITY_ONLY":
        return True
    for key in ("market_type", "market", "stat_key", "prop_type"):
        normalized = str(record.get(key) or "").lower().strip()
        normalized = normalized.replace("-", "_").replace(" ", "_")
        if normalized in _MONEYLINE_MARKET_ALIASES:
            return True
    return False


def _first_mapping(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return value
    return None


def _collect_forbidden_fields(*sources: Any) -> list[str]:
    found: set[str] = set()
    for source in sources:
        if isinstance(source, dict):
            found.update(key for key in source if key in _FORBIDDEN_AUTHORITY_FIELDS)
    return sorted(found)


def _server_key() -> bytes | None:
    """Use the existing server-only provenance key; no caller key is accepted."""
    from gate_engine.runtime_provenance import _attestation_key
    return _attestation_key()


def _profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "sport": profile.get("sport"),
        "profile_id": profile.get("profile_id"),
        "weights": profile.get("weights"),
    }


def attest_sport_specific_weight_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """
    Attach an internal server attestation to a registered weight profile.

    This is for server-side profile registration only.  The route policy below
    verifies, but never creates, this attestation from request data.
    """
    key = _server_key()
    if key is None:
        return {**profile, "attestation": None}
    payload = json.dumps(
        _profile_payload(profile), sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return {
        **profile,
        "attestation": hmac.new(key, payload, hashlib.sha256).hexdigest(),
    }


def _valid_weight_profile(profile: Any, sport: str) -> bool:
    """Accept only a named, sport-matched profile attested by the server."""
    if not isinstance(profile, dict):
        return False
    profile_sport = str(profile.get("sport") or "").upper().strip()
    profile_id = str(profile.get("profile_id") or "").strip()
    weights = profile.get("weights")
    key = _server_key()
    supplied = profile.get("attestation")
    if key is None or not isinstance(supplied, str):
        return False
    expected = attest_sport_specific_weight_profile(profile).get("attestation")
    return (
        profile_sport == sport
        and bool(profile_id)
        and isinstance(weights, dict)
        and bool(weights)
        and isinstance(expected, str)
        and hmac.compare_digest(supplied, expected)
    )


def _calibration_proof(ledger_ref: Any, sport: str) -> tuple[bool, str]:
    """
    Resolve a historical calibration record from the persisted ledger.

    The route never treats a caller-supplied record body as proof.  A matching
    stored record, sport, and source snapshot are required before the existing
    LLP ledger validator is consulted.
    """
    if not isinstance(ledger_ref, dict):
        return False, "CALIBRATION_LEDGER_REQUIRED"
    ledger_id = ledger_ref.get("id") or ledger_ref.get("calibration_ledger_id")
    if ledger_id in (None, ""):
        return False, "CALIBRATION_LEDGER_ID_REQUIRED"
    from gate_engine.llp_governance import get_calibration_ledger, validate_calibration_ledger
    try:
        persisted = next(
            (
                record for record in get_calibration_ledger(limit=500)
                if str(record.get("id")) == str(ledger_id)
            ),
            None,
        )
    except Exception:
        return False, "CALIBRATION_LEDGER_UNAVAILABLE"
    if not isinstance(persisted, dict):
        return False, "CALIBRATION_LEDGER_NOT_FOUND"
    if str(persisted.get("sport") or "").upper().strip() != sport:
        return False, "CALIBRATION_LEDGER_SPORT_MISMATCH"
    if not persisted.get("source_snapshot_id"):
        return False, "CALIBRATION_LEDGER_PROVENANCE_REQUIRED"

    verdict = validate_calibration_ledger({"calibration_ledger": persisted})
    if verdict.get("passed") is True:
        return True, str(verdict.get("code") or "CALIBRATION_LEDGER_COMPLETE")
    return False, str(verdict.get("code") or "CALIBRATION_LEDGER_INCOMPLETE")


def _integrity_hash(payload: dict[str, Any]) -> str | None:
    """HMAC-bind the full authority-neutral object, not just probabilities."""
    key = _server_key()
    if key is None:
        return None
    attested = {
        key: value for key, value in payload.items()
        if key != "handoff_integrity_hash"
    }
    canonical = json.dumps(
        attested, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def build_specialist_handoff(
    *,
    row: dict[str, Any],
    enrichment: dict[str, Any] | None,
    probability_snapshot: dict[str, Any] | None,
    blockers: list[str] | None,
    governance_ceiling: str | None,
    model_id: str | None,
    model_status: str | None,
) -> dict[str, Any]:
    """
    Build the only specialist object permitted to leave the moneyline scorer.

    Authority-like fields are intentionally absent.  The legacy caller may
    retain its own terminal ceiling for compatibility, but neither it nor an
    untrusted feeder can inject a terminal decision into this object.
    """
    enrichment = enrichment or {}
    snapshot = probability_snapshot or {}
    sport = str(row.get("sport") or "").upper().strip()
    supplied_profile = _first_mapping(
        row.get("sport_specific_weight_profile"),
        enrichment.get("sport_specific_weight_profile"),
    )
    supplied_ledger = _first_mapping(
        row.get("calibration_ledger"),
        enrichment.get("calibration_ledger"),
    )
    profile_ok = _valid_weight_profile(supplied_profile, sport)
    ledger_ok, ledger_status = _calibration_proof(supplied_ledger, sport)

    routing_blockers = list(blockers or [])
    if not profile_ok:
        routing_blockers.append(f"SPORT_SPECIFIC_WEIGHT_PROFILE_REQUIRED:sport={sport or 'UNKNOWN'}")
    if not ledger_ok:
        routing_blockers.append(ledger_status)
    routing_blockers = list(dict.fromkeys(str(item) for item in routing_blockers))

    ranking_eligible = profile_ok and ledger_ok
    displayed_tier = (
        CALIBRATED_RESEARCH_RANKING_ELIGIBLE
        if ranking_eligible
        else WATCH_ONLY_UNTIL_CALIBRATED
    )
    object_payload: dict[str, Any] = {
        "contract": "LLP_MONEYLINE_PROBABILITY_RESEARCH_V1",
        "controlling_skill": CONTROLLING_SKILL,
        "objective": "OUTRIGHT_WIN_PROBABILITY_ONLY",
        "specialist_status": (
            "CALIBRATED_RESEARCH_ONLY" if ranking_eligible else "SHADOW_ONLY"
        ),
        "displayed_tier": displayed_tier,
        "ranking_eligible": ranking_eligible,
        "calibration_ledger_status": ledger_status,
        "sport_specific_weight_profile_status": (
            "VERIFIED" if profile_ok else "REQUIRED"
        ),
        "model_id": model_id,
        "model_status": model_status,
        "blockers": routing_blockers,
        "can_execute": False,
        "dry_run_only": True,
        "advisory_only": True,
        "rejected_authority_fields": _collect_forbidden_fields(row, enrichment, snapshot),
    }
    for key in _IDENTITY_FIELDS:
        object_payload[key] = row.get(key)
    for key in (
        "independent_probability", "raw_probability", "calibrated_probability",
        "calibrated_probability_lower_bound", "calibrated_probability_upper_bound",
        "lower_bound", "upper_bound", "net_edge", "snapshot_hash",
    ):
        object_payload[key] = snapshot.get(key)

    # Preserve existing aliases without deriving any new probability.
    if object_payload["raw_probability"] is None:
        object_payload["raw_probability"] = object_payload["independent_probability"]
    if object_payload["lower_bound"] is None:
        object_payload["lower_bound"] = object_payload["calibrated_probability_lower_bound"]
    if object_payload["upper_bound"] is None:
        object_payload["upper_bound"] = object_payload["calibrated_probability_upper_bound"]

    object_payload["handoff_integrity_hash"] = _integrity_hash(object_payload)
    return object_payload


def is_verified_specialist_handoff(value: Any) -> bool:
    """Return true only for an unmodified, permanently non-executable object."""
    if not isinstance(value, dict):
        return False
    if value.get("contract") != "LLP_MONEYLINE_PROBABILITY_RESEARCH_V1":
        return False
    if value.get("controlling_skill") != CONTROLLING_SKILL:
        return False
    if value.get("can_execute") is not False or value.get("dry_run_only") is not True:
        return False
    if any(key in value for key in _FORBIDDEN_AUTHORITY_FIELDS - {"can_execute"}):
        return False
    supplied_hash = value.get("handoff_integrity_hash")
    expected_hash = _integrity_hash(value)
    return (
        isinstance(supplied_hash, str)
        and isinstance(expected_hash, str)
        and hmac.compare_digest(supplied_hash, expected_hash)
    )


def specialist_is_ranking_eligible(value: Any) -> bool:
    """Graduation is allowed only for a verified object with both proofs."""
    return bool(
        is_verified_specialist_handoff(value)
        and value.get("ranking_eligible") is True
        and value.get("displayed_tier") == CALIBRATED_RESEARCH_RANKING_ELIGIBLE
        and value.get("calibration_ledger_status", "").startswith("CALIBRATION_LEDGER_COMPLETE")
        and value.get("sport_specific_weight_profile_status") == "VERIFIED"
    )