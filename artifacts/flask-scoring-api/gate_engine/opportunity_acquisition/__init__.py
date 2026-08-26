"""
gate_engine/opportunity_acquisition
====================================
Opportunity, Event & Exact-Market Acquisition Layer for NBA/WNBA composite props.

Provides:
  - OpportunityState: normalized player-event opportunity record
  - AcquisitionOrchestrator: multi-vendor acquisition, quorum resolution, invalidation
  - MarketIdentity / compare_identity: exact vs adjacent vs proxy board-line matching
  - run_composite_simulation: correlated joint PTS/REB/AST simulation
  - InvalidationTracker: detects material status/line/minutes changes

can_execute=False is unconditional in every output from this package.
"""
from .types import (
    AcquisitionStatus,
    LineupStatus,
    MinutesDistribution,
    ComponentOpportunityRates,
    VendorPacket,
    OpportunityState,
    PropFamily,
)
from .orchestrator import AcquisitionOrchestrator
from .quorum import resolve_quorum, QuorumResult
from .market_identity import (
    MarketIdentity,
    IdentityMatch,
    canonicalize,
    compare_identity,
)
from .composite_simulator import run_composite_simulation, CompositeSimResult
from .invalidation import InvalidationTracker, InvalidationResult

__all__ = [
    "AcquisitionStatus",
    "LineupStatus",
    "MinutesDistribution",
    "ComponentOpportunityRates",
    "VendorPacket",
    "OpportunityState",
    "PropFamily",
    "AcquisitionOrchestrator",
    "resolve_quorum",
    "QuorumResult",
    "MarketIdentity",
    "IdentityMatch",
    "canonicalize",
    "compare_identity",
    "run_composite_simulation",
    "CompositeSimResult",
    "InvalidationTracker",
    "InvalidationResult",
]

# Package-level invariant annotation
can_execute = False
