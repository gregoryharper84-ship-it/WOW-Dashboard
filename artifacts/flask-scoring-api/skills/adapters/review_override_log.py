"""
skills/adapters/review_override_log.py

Mandatory logged structure for any ChatGPT override of a REPAIR_REQUIRED or
BLOCKED recommendation from wow.governed-red-team-reviewer.

AUTHORITY CONTRACT (unconditional):
  - Only ChatGPT may issue an override.
  - An ordinary override (governing_spec_change=None) CANNOT clear a P0 finding.
    P0 requires either:
      (a) the underlying condition is resolved and the work resubmitted, or
      (b) a governing-spec change is explicitly documented in governing_spec_change.
  - Every override must be validated before it is logged.
  - This module never executes, deploys, trades, or approves on its own authority.

FINAL_AUTHORITY: CHATGPT_ONLY.

Governance invariants:
  can_execute              = False
  PRODUCTION_AUTHORITY     = False
  USER_OUTPUT_AUTHORITY    = False
  TERMINAL_LABEL_AUTHORITY = False
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False

# Recommendations that an override may apply to
_OVERRIDABLE_RECOMMENDATIONS: frozenset[str] = frozenset({
    "REPAIR_REQUIRED",
    "BLOCKED",
})

_SEVERITY_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


# ---------------------------------------------------------------------------
# Helper (must be defined BEFORE the dataclass that references it)
# ---------------------------------------------------------------------------

def _generate_override_id() -> str:
    """Generate a unique override ID based on current UTC time."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"OVERRIDE-{ts}"


# ---------------------------------------------------------------------------
# Override record
# ---------------------------------------------------------------------------

@dataclass
class ChatGPTOverrideRecord:
    """
    Mandatory structured record for any ChatGPT override of a reviewer
    REPAIR_REQUIRED or BLOCKED recommendation.

    Fields:
        original_recommendation: The recommendation being overridden.
        findings_overridden: List of finding dicts (must include severity + finding_id).
        max_severity_overridden: The maximum severity among overridden findings.
        reason: Human-readable rationale for the override.
        evidence_basis: New evidence or context ChatGPT has that the reviewer lacked.
        risk_accepted: Explicit statement of the risk being accepted.
        conditions: Conditions under which this override remains valid.
        timestamp: ISO-8601 UTC timestamp of the override decision.
        reviewer_run_id: The run_id of the reviewer result being overridden.
        packet_hash: The packet_sha256/packet_hash of the reviewed packet.
        reviewer_version: The version of the reviewer that produced the recommendation.
        p0_present: Whether any P0 finding is among the overridden findings.
        governing_spec_change: Required when p0_present=True. Documents the explicit
            governing-spec change that authorizes setting aside the P0.
        override_id: Auto-generated unique identifier for this override record.
    """
    original_recommendation: str
    findings_overridden:      list
    max_severity_overridden:  str
    reason:                   str
    evidence_basis:           str
    risk_accepted:            str
    conditions:               list
    timestamp:                str
    reviewer_run_id:          str
    packet_hash:              str
    reviewer_version:         str
    p0_present:               bool
    governing_spec_change:    str | None = None
    override_id:              str = field(default_factory=_generate_override_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compute_record_hash(self) -> str:
        """SHA-256 of the canonical record (excluding override_id)."""
        payload = {k: v for k, v in asdict(self).items() if k != "override_id"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class OverrideValidationError(ValueError):
    """Raised when an override record fails validation."""


def validate_override_record(record: ChatGPTOverrideRecord) -> list[str]:
    """
    Validate a ChatGPT override record.

    Returns a list of validation errors.  An empty list means the record is valid.

    Key rule: a P0 finding cannot be cleared by an ordinary override.
    """
    errors: list[str] = []

    if record.original_recommendation not in _OVERRIDABLE_RECOMMENDATIONS:
        errors.append(
            f"original_recommendation must be one of "
            f"{sorted(_OVERRIDABLE_RECOMMENDATIONS)}; "
            f"got {record.original_recommendation!r}"
        )

    if not record.findings_overridden:
        errors.append(
            "findings_overridden must be non-empty — at least one finding "
            "must be explicitly identified for the override to be logged."
        )

    if not record.reason or not str(record.reason).strip():
        errors.append("reason must be a non-empty string.")

    if not record.evidence_basis or not str(record.evidence_basis).strip():
        errors.append(
            "evidence_basis must be non-empty — ChatGPT must state what "
            "new evidence or context supports the override."
        )

    if not record.risk_accepted or not str(record.risk_accepted).strip():
        errors.append(
            "risk_accepted must be a non-empty explicit risk acceptance statement."
        )

    if not record.conditions:
        errors.append(
            "conditions must be non-empty — state the conditions under which "
            "this override remains valid."
        )

    # P0 rule: cannot be cleared by an ordinary override
    p0_findings = [f for f in record.findings_overridden
                   if (f.get("severity") if isinstance(f, dict) else None) == "P0"]
    if p0_findings and not record.governing_spec_change:
        ids = [f.get("finding_id", "?") for f in p0_findings
               if isinstance(f, dict)]
        errors.append(
            f"P0 finding(s) present ({ids}): "
            f"an ordinary override cannot clear a P0. Either resolve the underlying "
            f"condition and resubmit, or provide an explicit governing_spec_change "
            f"that authorizes setting aside the P0."
        )

    if record.p0_present and not p0_findings:
        errors.append(
            "p0_present=True but no P0 findings are listed in findings_overridden. "
            "Set p0_present correctly."
        )

    if not record.reviewer_run_id or not str(record.reviewer_run_id).strip():
        errors.append("reviewer_run_id must reference the reviewer result being overridden.")

    if not record.packet_hash or not str(record.packet_hash).strip():
        errors.append("packet_hash must reference the reviewed packet.")

    if not record.timestamp or not str(record.timestamp).strip():
        errors.append("timestamp must be a non-empty ISO-8601 UTC timestamp.")

    return errors


# ---------------------------------------------------------------------------
# Structured log entry builder
# ---------------------------------------------------------------------------

def build_override_log_entry(
    record: ChatGPTOverrideRecord,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build a structured log entry from an override record.

    If validation_errors is provided and non-empty, the log entry is marked
    INVALID and must not be treated as an approved override.
    """
    errors = validation_errors or []
    return {
        "schema":                  "WOW_CHATGPT_OVERRIDE_LOG_v1",
        "override_id":             record.override_id,
        "is_valid":                not errors,
        "validation_errors":       errors,
        "original_recommendation": record.original_recommendation,
        "findings_overridden":     record.findings_overridden,
        "max_severity_overridden": record.max_severity_overridden,
        "p0_present":              record.p0_present,
        "governing_spec_change":   record.governing_spec_change,
        "reason":                  record.reason,
        "evidence_basis":          record.evidence_basis,
        "risk_accepted":           record.risk_accepted,
        "conditions":              record.conditions,
        "reviewer_run_id":         record.reviewer_run_id,
        "packet_hash":             record.packet_hash,
        "reviewer_version":        record.reviewer_version,
        "timestamp":               record.timestamp,
        "record_hash":             record.compute_record_hash(),
        "can_execute":             False,
        "authority_statement": (
            "FINAL_AUTHORITY: CHATGPT_ONLY. "
            "This override log is a record only. It does not grant execution, "
            "deployment, capital, or approval authority to any automated system."
        ),
    }


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def make_override_record(
    original_recommendation: str,
    findings_overridden: list,
    reason: str,
    evidence_basis: str,
    risk_accepted: str,
    conditions: list,
    reviewer_run_id: str,
    packet_hash: str,
    reviewer_version: str,
    governing_spec_change: str | None = None,
    timestamp: str | None = None,
) -> ChatGPTOverrideRecord:
    """
    Convenience factory for building a ChatGPTOverrideRecord.

    Automatically computes:
      - max_severity_overridden from findings_overridden
      - p0_present from findings_overridden
      - timestamp (UTC now) if not provided
    """
    if findings_overridden:
        sev_values = [
            (f.get("severity") if isinstance(f, dict) else "P3") or "P3"
            for f in findings_overridden
        ]
        max_sev = min(sev_values, key=lambda s: _SEVERITY_ORDER.get(s, 99))
    else:
        max_sev = "P3"

    p0_present = any(
        (f.get("severity") if isinstance(f, dict) else None) == "P0"
        for f in findings_overridden
    )
    ts = timestamp or datetime.now(timezone.utc).isoformat()

    return ChatGPTOverrideRecord(
        original_recommendation=original_recommendation,
        findings_overridden=findings_overridden,
        max_severity_overridden=max_sev,
        reason=reason,
        evidence_basis=evidence_basis,
        risk_accepted=risk_accepted,
        conditions=conditions,
        timestamp=ts,
        reviewer_run_id=reviewer_run_id,
        packet_hash=packet_hash,
        reviewer_version=reviewer_version,
        p0_present=p0_present,
        governing_spec_change=governing_spec_change,
    )
