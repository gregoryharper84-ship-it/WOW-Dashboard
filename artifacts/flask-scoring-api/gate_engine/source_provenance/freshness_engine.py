"""
gate_engine/source_provenance/freshness_engine.py

Freshness evaluation engine.

INVARIANT-1: Freshness is NOT computed as ``now - retrieved_at``.
The freshness_basis field from the FactPolicy determines which timestamp
is used as the age anchor.  retrieved_at is only the last-resort basis.

evaluate_freshness() returns a (FreshnessStatus, age_seconds | None) tuple
so callers can log exact age without re-computing it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .evidence_contract import FreshnessBasis, FreshnessStatus, StructuredEvidence
from .fact_policy_registry import FactPolicy


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def evaluate_freshness(
    evidence: StructuredEvidence,
    policy: FactPolicy,
    as_of: datetime | None = None,
) -> tuple[FreshnessStatus, Optional[float]]:
    """
    Evaluate the freshness of *evidence* using the *policy*'s freshness_basis.

    Returns
    -------
    (status, age_seconds)
        status      : FreshnessStatus result
        age_seconds : float age in seconds, or None if the anchor timestamp is absent

    Design notes
    ------------
    - Uses policy.freshness_basis to select the timestamp; falls back through
      (published_at → effective_at → observed_at → retrieved_at) when the
      primary basis timestamp is absent.
    - retrieved_at is used as a last resort only.
    - A fact with *no* relevant timestamp returns UNVERIFIABLE (not STALE).
    """
    now = _ensure_utc(as_of) or _utc_now()

    # Select anchor timestamp according to policy's freshness_basis
    anchor = _select_anchor(evidence, policy.freshness_basis)

    if anchor is None:
        # The required timestamp is absent; we cannot compute age
        return FreshnessStatus.UNVERIFIABLE, None

    anchor = _ensure_utc(anchor)
    age_seconds = (now - anchor).total_seconds()

    if age_seconds < 0:
        # Anchor is in the future — treat as fresh (clocks may vary slightly)
        return FreshnessStatus.FRESH, age_seconds

    # Threshold tiers:
    # FRESH   : age <= max_age_seconds
    # STALE   : age <= 3× max_age_seconds
    # EXPIRED : age > 3× max_age_seconds
    if age_seconds <= policy.max_age_seconds:
        return FreshnessStatus.FRESH, age_seconds
    elif age_seconds <= policy.max_age_seconds * 3:
        return FreshnessStatus.STALE, age_seconds
    else:
        return FreshnessStatus.EXPIRED, age_seconds


def _select_anchor(evidence: StructuredEvidence, basis: FreshnessBasis) -> datetime | None:
    """
    Return the appropriate timestamp for the given basis, with graceful fallback.

    Fallback chain per basis:
      PUBLISHED_AT  → published_at  → effective_at → observed_at → retrieved_at
      EFFECTIVE_AT  → effective_at  → published_at → observed_at → retrieved_at
      OBSERVED_AT   → observed_at   → published_at → effective_at → retrieved_at
      RETRIEVED_AT  → retrieved_at  → (no further fallback — last resort basis)
    """
    if basis == FreshnessBasis.PUBLISHED_AT:
        return (
            evidence.published_at
            or evidence.effective_at
            or evidence.observed_at
            or evidence.retrieved_at
        )
    elif basis == FreshnessBasis.EFFECTIVE_AT:
        return (
            evidence.effective_at
            or evidence.published_at
            or evidence.observed_at
            or evidence.retrieved_at
        )
    elif basis == FreshnessBasis.OBSERVED_AT:
        return (
            evidence.observed_at
            or evidence.published_at
            or evidence.effective_at
            or evidence.retrieved_at
        )
    elif basis == FreshnessBasis.RETRIEVED_AT:
        return evidence.retrieved_at
    return None
