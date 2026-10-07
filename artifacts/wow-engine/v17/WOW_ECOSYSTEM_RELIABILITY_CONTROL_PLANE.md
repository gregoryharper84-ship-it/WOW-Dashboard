# WOW Ecosystem Reliability Control Plane — Phases 3–6

Status: Class A control-plane implementation
Runtime: V17_ACTIVE
Probability authority: none
SAFE_HOLD authority: SYSTEMS_INTELLIGENCE_RELIABILITY
Independent closure authority: INDEPENDENT_VERIFICATION
Global terminal authority: V17_TERMINAL_REDUCER
Execution authority: can_execute=false

## Purpose

This layer completes the durable operating surface around the neutral WOW Ecosystem Conductor. It answers from durable evidence: which connection is broken, where the first proven failing boundary is, which capabilities are user-ready, who owns the next action, whether a failure is recurring, and whether the intended user outcome is actually complete.

It never creates sporting probability, weather probability, calibration, lower bounds, rankings, specialist ownership, terminal authority, or execution authority.

## Layering

~~~text
LIVE AUTHORITATIVE PROBES
        |
        v
DURABLE EVIDENCE LEDGER
        |
        +--> work ownership / handoff receipts
        +--> connection evidence
        +--> readiness snapshots
        +--> incident / opportunity findings
        |
        v
CAPABILITY / READINESS MATRIX
        |
        v
SYSTEMS INTELLIGENCE INCIDENT ROUTER
        |
        +--> incident -> Engineering closure
        +--> repeat pattern -> Engineering Opportunity Queue
        |
        v
RELIABILITY CONTROL PLANE
~~~

## Durable ledger

Migration: artifacts/wow-engine/migrations/20261007181000_wow_ecosystem_control_plane.sql

Tables:
- wow_ecosystem_probe_receipts
- wow_ecosystem_handoff_receipts
- wow_ecosystem_work_items
- wow_ecosystem_readiness_snapshots
- wow_ecosystem_findings

All tables enable RLS, revoke access from public/anon/authenticated, allow only required service-role operations, preserve can_execute=false, and retain typed failure plus first-failing-boundary evidence.

Application adapter: artifacts/wow-engine/v17/ecosystem_ledger.py. It redacts secret-bearing keys before persistence and validates work-item authority and verification rules through the Conductor contract.

## Capability readiness

Module: artifacts/wow-engine/v17/ecosystem_capability_matrix.py

Readiness is multi-dimensional. A healthy service cannot compensate for a missing model, missing identity, unverified route, missing settlement contract, absent persistence proof, or incomplete user workflow.

Default WOW Prop and LLP Team/Event dimensions: discovery, identity, data, model, calibration, routing, terminal, persistence, verification, user_workflow.

Default Kalshi Weather dimensions: contract, settlement, data, model, calibration, routing, terminal, persistence, verification, user_workflow.

Missing required dimensions become UNKNOWN and cannot be called READY.

## Incident and opportunity routing

Module: artifacts/wow-engine/v17/ecosystem_incident_router.py

Systems Intelligence remains diagnostic owner. Engineering remains closure owner. Each failing handoff receives a stable fingerprint from handoff_id + first_failing_boundary + typed_failure.

Preliminary classification is fail-safe: model math/calibration/lower-bound/threshold/artifact behavior -> Class C; identity/routing/hydration/registration/feature contract -> Class B; ordinary reliability/observability/persistence/control-plane repair -> Class A.

At three observed occurrences of the same failure fingerprint, the router may also create an OPPORTUNITY finding so recurring friction becomes prevention work rather than another isolated ticket.

A finding cannot be VERIFIED_CLOSED unless the boundary is cleared, exact-head regression passes, and Independent Verification passes.

## Reliability metrics

Module: artifacts/wow-engine/v17/ecosystem_reliability_control_plane.py

First-class metrics:
- Reliable Decision Availability = ready intended capabilities / intended capabilities
- Product Outcome Completion Rate = completed supported requests / supported requests
- Candidate Conservation Rate = terminal admitted candidates / admitted candidates
- First-Pass Completion Rate = supported requests completed without repair/reroute / supported requests
- Decision Integrity Rate = published decisions with complete lineage/governance / published decisions
- Repeat Failure Elimination Rate = verified eliminated recurring fingerprints / recurring fingerprints
- Detection-to-Proven-Root-Cause Time = mean seconds from first detection to proven root cause

For ratio metrics, denominator zero is UNKNOWN, never 100%. A missing root-cause proof makes Detection-to-Proven-Root-Cause Time UNKNOWN rather than silently dropping unresolved incidents.

## Truth hierarchy

The control plane keeps component/runtime, connection/handoff, data/evidence, specialist/model, calibration/certification, persistence, repository/exact-head, Independent Verification, product capability, and user-workflow states independently visible.

## Completion gate

The full Conductor build is not COMPLETE until:
1. #1487 Conductor v2 foundation is merged on exact-head protected green CI.
2. #1489 authoritative live probes are restacked onto current main, exact-head green, merged, and verified.
3. The Phase 3–6 implementation is exact-head green and merged.
4. The durable ledger migration is applied and read-back verified.
5. A real control-plane run consumes live probe receipts and writes durable probe/handoff/readiness evidence.
6. WOW Prop, LLP Team/Event, and Kalshi Weather paths each produce an authoritative readiness result.
7. Broken-path injection proves false-green prevention in production-equivalent acceptance.
8. Independent Verification proves closure.
9. can_execute=false and V17_TERMINAL_REDUCER remain invariant.

Until those conditions are satisfied, status remains WORKING_NOT_COMPLETE.
