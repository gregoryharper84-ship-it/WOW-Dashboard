"""
skills/adapters/review_packet.py

Deterministic, non-editorial frozen review packet builder and validator
for wow.governed-red-team-reviewer.

DESIGN CONTRACT
---------------
The packet builder collects required artifacts without editorial judgment.
It MUST NOT:
  • Select favorable evidence
  • Summarize correctness
  • Apply interpretive framing

Hash binding:
  packet_hash = SHA-256(canonical_json(all_fields_except_packet_hash))
  sorted-key, no-whitespace JSON; default=str for non-serialisable values.
  Any drift after freeze (candidate_commit_sha change, artifact substitution)
  is detected by recomputing this hash. Mismatch → P0 PACKET_DRIFT_DETECTED.

Governance invariants (unconditional):
  can_execute              = False
  PRODUCTION_AUTHORITY     = False
  USER_OUTPUT_AUTHORITY    = False
  TERMINAL_LABEL_AUTHORITY = False
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False

# ---------------------------------------------------------------------------
# Required field registry
# ---------------------------------------------------------------------------

REQUIRED_PACKET_FIELDS: frozenset[str] = frozenset({
    "work_item_id",
    "review_attempt",
    "spec_version",
    "spec_hash",
    "base_commit_sha",
    "candidate_commit_sha",
    "diff_manifest",
    "acceptance_criteria",
    "test_commands",
    "test_artifacts",
    "test_counts",
    "runtime_governance_hash",
    "tested_edge_cases",
    "tested_negative_cases",
    "prior_review_history",
    "prior_blockers",
    "packet_creation_timestamp",
    "packet_hash",
})

REQUIRED_PRIOR_BLOCKER_FIELDS: frozenset[str] = frozenset({
    "blocker_id",
    "description",
    "severity",
    "status",
})

VALID_BLOCKER_STATUSES: frozenset[str] = frozenset({
    "RESOLVED",
    "STILL_PRESENT",
    "REGRESSED",
    "NOT_EVIDENCED",
})

# Defect classes (canonical)
DEFECT_CLASSES: frozenset[str] = frozenset({
    "implementation_defect",
    "evidence_defect",
    "specification_defect",
    "governance_defect",
})

# Severity levels (ordered P0 most severe)
SEVERITY_LEVELS: tuple[str, ...] = ("P0", "P1", "P2", "P3")
_SEVERITY_RANK: dict[str, int] = {s: i for i, s in enumerate(SEVERITY_LEVELS)}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ReviewPacketError(ValueError):
    """Raised when a review packet is malformed and cannot be processed."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PacketValidationResult:
    """Result of packet structure and hash validation."""
    is_valid:         bool
    hash_valid:       bool
    missing_fields:   list[str] = field(default_factory=list)
    malformed_fields: list[str] = field(default_factory=list)
    warnings:         list[str] = field(default_factory=list)
    errors:           list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------

def compute_packet_hash(packet: dict[str, Any]) -> str:
    """
    Compute the canonical SHA-256 hash of a review packet.

    Excludes the 'packet_hash' field itself (to avoid circularity).
    Uses deterministic JSON: sorted keys, no whitespace, default=str.

    Returns the lowercase hex digest.
    """
    payload = {k: v for k, v in packet.items() if k != "packet_hash"}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Structure validation
# ---------------------------------------------------------------------------

def validate_packet_structure(packet: Any) -> PacketValidationResult:
    """
    Validate that a packet is a dict with all required fields present
    and structurally correct.  Does NOT verify the packet_hash field —
    call validate_packet_hash() separately for that.
    """
    if not isinstance(packet, dict):
        return PacketValidationResult(
            is_valid=False, hash_valid=False,
            errors=[f"review_packet must be a dict; got {type(packet).__name__!r}"],
        )

    missing   = sorted(REQUIRED_PACKET_FIELDS - set(packet.keys()))
    malformed: list[str] = []
    warnings:  list[str] = []
    errors:    list[str] = []

    if missing:
        errors.append(f"Missing required fields: {missing}")

    # ── Per-field checks ──────────────────────────────────────────────────────

    if "work_item_id" in packet:
        _require_nonempty_str(packet, "work_item_id", malformed)

    if "review_attempt" in packet:
        v = packet.get("review_attempt")
        if not isinstance(v, int) or v < 1:
            malformed.append("review_attempt must be a positive int")

    if "spec_version" in packet:
        _require_nonempty_str(packet, "spec_version", malformed)

    if "spec_hash" in packet:
        h = packet.get("spec_hash")
        if not isinstance(h, str) or len(h) < 8:
            malformed.append("spec_hash must be a non-trivial hex string (≥8 chars)")

    if "base_commit_sha" in packet:
        _require_nonempty_str(packet, "base_commit_sha", malformed)

    if "candidate_commit_sha" in packet:
        _require_nonempty_str(packet, "candidate_commit_sha", malformed)

    if "diff_manifest" in packet:
        dm = packet.get("diff_manifest")
        if not isinstance(dm, list):
            malformed.append("diff_manifest must be a list")
        else:
            for i, entry in enumerate(dm):
                if not isinstance(entry, dict):
                    malformed.append(f"diff_manifest[{i}] must be a dict")
                elif "file" not in entry:
                    malformed.append(f"diff_manifest[{i}] missing 'file' key")

    if "acceptance_criteria" in packet:
        ac = packet.get("acceptance_criteria")
        if not isinstance(ac, list):
            malformed.append("acceptance_criteria must be a list")
        elif len(ac) == 0:
            warnings.append("acceptance_criteria is empty — nothing to verify")

    if "test_commands" in packet:
        tc = packet.get("test_commands")
        if not isinstance(tc, list):
            malformed.append("test_commands must be a list")
        elif len(tc) == 0:
            malformed.append("test_commands is empty — reproduction is impossible")

    if "test_artifacts" in packet:
        ta = packet.get("test_artifacts")
        if not isinstance(ta, list):
            malformed.append("test_artifacts must be a list")

    if "test_counts" in packet:
        tc2 = packet.get("test_counts")
        if not isinstance(tc2, dict):
            malformed.append("test_counts must be a dict")
        else:
            for key in ("passed", "failed"):
                if key not in tc2:
                    warnings.append(f"test_counts missing '{key}' key")

    if "prior_blockers" in packet:
        pb = packet.get("prior_blockers")
        if not isinstance(pb, list):
            malformed.append("prior_blockers must be a list")
        else:
            for i, b in enumerate(pb):
                if not isinstance(b, dict):
                    malformed.append(f"prior_blockers[{i}] must be a dict")
                    continue
                missing_b = sorted(REQUIRED_PRIOR_BLOCKER_FIELDS - set(b.keys()))
                if missing_b:
                    malformed.append(f"prior_blockers[{i}] missing fields: {missing_b}")
                elif b.get("status") not in VALID_BLOCKER_STATUSES:
                    malformed.append(
                        f"prior_blockers[{i}].status must be one of "
                        f"{sorted(VALID_BLOCKER_STATUSES)}; got {b.get('status')!r}"
                    )

    is_valid = not errors and not malformed
    return PacketValidationResult(
        is_valid=is_valid,
        hash_valid=False,   # checked separately
        missing_fields=missing,
        malformed_fields=malformed,
        warnings=warnings,
        errors=errors,
    )


def validate_packet_hash(packet: dict[str, Any]) -> tuple[bool, str]:
    """
    Verify packet_hash against the deterministic computed hash.
    Returns (hash_valid: bool, detail: str).
    """
    stored = packet.get("packet_hash")
    if not stored:
        return False, "packet_hash field is absent or empty"
    computed = compute_packet_hash(packet)
    if str(stored).strip().lower() != computed:
        return False, (
            f"PACKET_DRIFT_DETECTED: stored={stored!r} "
            f"computed={computed!r}"
        )
    return True, "packet_hash verified"


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------

def severity_rank(sev: str) -> int:
    """Lower rank = more severe (P0 = 0, P3 = 3)."""
    return _SEVERITY_RANK.get(sev, 99)


def max_severity(findings: list[dict]) -> str | None:
    """Return the most severe (lowest rank) severity in a list of findings."""
    if not findings:
        return None
    return min(
        (f.get("severity", "P3") for f in findings),
        key=severity_rank,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _require_nonempty_str(packet: dict, key: str, malformed: list[str]) -> None:
    v = packet.get(key)
    if not isinstance(v, str) or not v.strip():
        malformed.append(f"{key!r} must be a non-empty string; got {type(v).__name__!r}")
