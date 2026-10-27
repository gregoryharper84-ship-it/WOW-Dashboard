"""WOW V17 minimal autonomous-engineering decision contract (non-authorizing).

This is an advisory classifier for admission and handoffs, NOT merge, model,
release, writer-lease or credential authority. No GitHub/API calls or side effects.
The existing protected ruleset and V17 terminal reducer are always authoritative.

Routine Class A may follow the established no-human-review path only if the real
branch-protection checks pass. A source PR, a bot source copy, or this classification
alone never confers authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class Track(str, Enum):
    ROUTINE = "ROUTINE_AUTONOMOUS"
    ENGINEERING = "ENGINEERING_INDEPENDENT_CHECK"
    MODEL = "MODEL_CERTIFICATION"
    TRUST_ROOT = "TRUST_ROOT_AUTHORIZATION"
    HOLD = "UNRESOLVED_SCOPE_HOLD"


# These are actual effects, not PR author/label-controlled declarations.
# The caller MUST attest provenance of the exact diff and the exact SHA.
DANGEROUS_EFFECTS = frozenset({
    "sporting_probability_math", "fitted_artifact", "calibration",
    "calibrated_lower_bound", "qualification_threshold", "model_promotion",
    "scorer_registration", "market_price_interpretation",
})
ENGINEERING_EFFECTS = frozenset({
    "event_identity", "roster_eligibility", "lineup_availability",
    "evidence_hydration", "evidence_persistence", "routing",
    "terminal_provenance", "writer_lease", "provider_auth",
    "prod_deployment", "source_admission",
})
TRUST_EFFECTS = frozenset({
    "auth_principal", "branch_protection", "required_check",
    "governance_decision", "credential_scope", "codeowners",
})
KNOWN_EFFECTS = DANGEROUS_EFFECTS | ENGINEERING_EFFECTS | TRUST_EFFECTS | {"none"}
TRUST_PREFIXES = (".github/", ".agents/", ".claude/")
TRUST_EXACT = frozenset({"render.yaml", "artifacts/wow-engine/v17/autonomy_governance_profile.py",
    "artifacts/wow-engine/v17/agent_identity_policy.py",
    "artifacts/wow-engine/v17/independent_release_checks.py",
    "artifacts/wow-engine/v17/engineering_authorized_adoption.py",
    "artifacts/wow-engine/v17/engineering_remote_fallback.py"})
# A narrow subset that can never silently carry a new model coefficient.
# Anything outside this list needs an independently determined effect audit.
SAFE_DOCUMENTATION_PREFIXES = ("docs/runbooks/", "docs/operations/")


@dataclass(frozen=True)
class Assessment:
    track: Track
    code: str
    requires_model_certification: bool
    requires_existing_protected_authorization: bool
    owner_approval_by_default: bool
    no_execution: bool = True
    terminal_authority: str = "V17_TERMINAL_REDUCER"


def _assess(track: Track, code: str, *, model_effect: bool = False) -> Assessment:
    # Mixed model+trust-root diffs must satisfy BOTH independent contracts.
    return Assessment(track, code, track is Track.MODEL or model_effect,
                      track is Track.TRUST_ROOT,
                      track is Track.TRUST_ROOT)


def assess(*, paths: Iterable[str], effects: Iterable[str],
           exact_head_verified: bool, diff_evidence_verified: bool,
           source_role_verified: bool) -> Assessment:
    """Select a review route, never grant approval or release a worker.

    effects must be attested by an independent diff inspector, not by a
    possibly malicious PR body. Unknown/missing evidence fails closed.
    Even an empty proposed change is held rather than producing a false PASS.
    """
    files = tuple(paths)
    changes = tuple(effects)
    if not exact_head_verified or not diff_evidence_verified or not source_role_verified:
        return _assess(Track.HOLD, "AUTONOMY_EVIDENCE_UNVERIFIED")
    if not files or not changes:
        return _assess(Track.HOLD, "AUTONOMY_SCOPE_MISSING")
    if (len(set(files)) != len(files) or len(set(changes)) != len(changes)
        or any(not isinstance(p, str) or not p or p.startswith("/") or
               ".." in p.split("/") or "\\" in p for p in files)):
        return _assess(Track.HOLD, "AUTONOMY_SCOPE_INVALID")
    if any(e not in KNOWN_EFFECTS for e in changes) or ("none" in changes and len(changes) != 1):
        return _assess(Track.HOLD, "AUTONOMY_EFFECT_AMBIGUOUS")
    if (any(p.startswith(TRUST_PREFIXES) or p in TRUST_EXACT for p in files) or
            any(e in TRUST_EFFECTS for e in changes)):
        return _assess(Track.TRUST_ROOT, "AUTONOMY_TRUST_ROOT_SCOPE",
                       model_effect=any(e in DANGEROUS_EFFECTS for e in changes))
    if any(e in DANGEROUS_EFFECTS for e in changes):
        return _assess(Track.MODEL, "AUTONOMY_MODEL_SCOPE")
    if any(e in ENGINEERING_EFFECTS for e in changes):
        return _assess(Track.ENGINEERING, "AUTONOMY_ENGINEERING_SCOPE")
    if changes == ("none",) and all(p.startswith(SAFE_DOCUMENTATION_PREFIXES) for p in files):
        return _assess(Track.ROUTINE, "AUTONOMY_NARROW_ROUTINE_SCOPE")
    return _assess(Track.HOLD, "AUTONOMY_SCOPE_REVIEW_REQUIRED")


def remote_writer_reentry(*, terminated_provider_receipt_verified: bool,
                          original_reservation_verified: bool,
                          exclusive_lease_epoch_verified: bool,
                          no_newer_active_run_verified: bool,
                          atomic_release_supported: bool,
                          fresh_readback_verified: bool) -> str:
    """Compute safe handoff phase only; NEVER mutate a lease or PR review.

    An authenticated terminal session may close its session separately from
    PR code review. The durable reservation changes only via real owner/epoch
    compare-and-swap (CAS), followed by current-state readback.
    """
    if not terminated_provider_receipt_verified or not original_reservation_verified:
        return "CURSOR_TERMINAL_OR_RESERVATION_UNVERIFIED"
    if not exclusive_lease_epoch_verified or not no_newer_active_run_verified:
        return "WRITER_FENCE_UNVERIFIED"
    if not atomic_release_supported:
        return "WRITER_CAS_FINALIZER_UNAVAILABLE"
    if not fresh_readback_verified:
        return "WRITER_RELEASE_READBACK_MISSING"
    return "WRITER_RELEASE_PROOF_COMPLETE"
