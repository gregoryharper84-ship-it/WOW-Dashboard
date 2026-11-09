"""Read-only fail-closed registry triad boundary for V17 fitted-model paths.

The registry reader is a separate source-owned component (PR #2537 follow-up).
Its absence must never become a passing model check, inferred certification,
a model-probability fallback, or permission to execute. This module cannot
create or verify a fitted probability; it only checks typed evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from v17.model_evidence_bundle_contract import ModelBundleScope

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

# Distinguish absent DB, absent reader, failed read and invalid contract from
# validly read but incomplete/invalid evidence. Do not label any as
# MODEL_UNAVAILABLE: specialist capability and evidence integrity differ.
TRIAD_DB_UNAVAILABLE = "MODEL_BUNDLE_DB_UNAVAILABLE"
TRIAD_READER_UNAVAILABLE = "MODEL_BUNDLE_READER_UNAVAILABLE"
TRIAD_READER_FAILED = "MODEL_BUNDLE_READER_READ_FAILED"
TRIAD_READER_INVALID = "MODEL_BUNDLE_READER_CONTRACT_INVALID"
TRIAD_EVIDENCE_UNVERIFIED = "MODEL_BUNDLE_EVIDENCE_UNVERIFIED"


@dataclass(frozen=True)
class TriadBoundaryVerdict:
    status: str
    blocker: str | None
    blockers: tuple[str, ...]
    model_invoked: bool = False
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def as_hold(self) -> dict[str, Any]:
        return {
            "code": self.blocker or TRIAD_EVIDENCE_UNVERIFIED,
            "blocker_code": self.blocker or TRIAD_EVIDENCE_UNVERIFIED,
            "blockers": list(self.blockers),
            "failed_contract_scope": ["MODEL_EVIDENCE_TRIAD"],
            "model_invoked": False,
            "scoring_attempted": False,
            "model_evaluated": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "global_terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
        }


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _failure(code: str, blockers: tuple[str, ...] = ()) -> TriadBoundaryVerdict:
    return TriadBoundaryVerdict(
        status="BLOCKED", blocker=code, blockers=blockers or (code,),
    )


def assess_scoring_boundary(
    db: Any,
    scope: ModelBundleScope,
    *,
    reader: Any = None,
) -> TriadBoundaryVerdict:
    """Call the exact-model registry reader; do not infer its verification.

    Caller must supply a scope built from server-owned certified artifact
    identity, never request-side probabilities or specialist guesses. A complete
    bundle means ready for independent review, NOT automatically publishable.
    The scorer's existing certification, bounds and terminal reducer still own
    final publication. Every malformed/missing reader response fails closed.
    """
    if not isinstance(scope, ModelBundleScope):
        return _failure(TRIAD_READER_INVALID)
    if db is None:
        return _failure(TRIAD_DB_UNAVAILABLE)
    if reader is None:
        try:
            from v17.model_evidence_registry_readers import (
                assess_registry_model_bundle as reader,
            )
        except ImportError:
            return _failure(TRIAD_READER_UNAVAILABLE)
    if not callable(reader):
        return _failure(TRIAD_READER_INVALID)
    try:
        result = reader(db, scope)
    except Exception:  # no untrusted query text or credential leak into receipt
        return _failure(TRIAD_READER_FAILED)

    # Freebuff's reader contract includes a verdict and source/missing-field
    # metadata. Accept a direct contract verdict or the nested "assessment"
    # form, but never a bare PASS/boolean or claimed verified registry column.
    assessment = _field(result, "assessment", result)
    kind = _field(assessment, "model_kind")
    sport = _field(assessment, "sport")
    family = _field(assessment, "model_family")
    version = _field(assessment, "model_artifact_version")
    status = _field(assessment, "status")
    complete = _field(assessment, "bundle_complete")
    blockers = _field(assessment, "blockers")
    if (
        kind != scope.model_kind or sport != scope.sport
        or family != scope.model_family
        or version != scope.model_artifact_version
        or not isinstance(blockers, (list, tuple))
        or not all(isinstance(v, str) and v for v in blockers)
        or type(complete) is not bool
        or status not in ("READY_FOR_INDEPENDENT_REVIEW", "BLOCKED")
    ):
        return _failure(TRIAD_READER_INVALID)
    if complete is not True or blockers or status != "READY_FOR_INDEPENDENT_REVIEW":
        codes = tuple(blockers) if blockers else (TRIAD_EVIDENCE_UNVERIFIED,)
        return _failure(codes[0], codes)

    # A triad ready for independent review cannot authorize a wager/model
    # publication. A caller must still apply its independent V17 gates.
    return TriadBoundaryVerdict(
        status="READY_FOR_INDEPENDENT_REVIEW",
        blocker=None,
        blockers=(),
    )


__all__ = [
    "CAN_EXECUTE", "TERMINAL_AUTHORITY", "TriadBoundaryVerdict",
    "TRIAD_DB_UNAVAILABLE", "TRIAD_READER_UNAVAILABLE",
    "TRIAD_READER_FAILED", "TRIAD_READER_INVALID",
    "TRIAD_EVIDENCE_UNVERIFIED", "assess_scoring_boundary",
]
