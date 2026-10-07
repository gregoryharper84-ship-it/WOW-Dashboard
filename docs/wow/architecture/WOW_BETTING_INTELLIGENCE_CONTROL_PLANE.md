# WOW V17 Betting Intelligence — Class A Product Intelligence & Orchestration Control Plane

## Status

Repository contract for the **WOW Betting Intelligence / Product Intelligence & Orchestration** layer.

This document defines Betting Intelligence as the **Product Intelligence & Orchestration Control Plane** inside the wider neutral `WOW_ECOSYSTEM_CONDUCTOR`. It does **not** create sporting probability authority, model certification authority, execution authority, or a second terminal reducer.

Global invariants:

- `can_execute=false`
- `V17_TERMINAL_REDUCER` remains the sole sporting terminal authority.
- Exactly one controlling specialist owns each governed probability row/event.
- Betting Intelligence may coordinate, prioritize, reconcile, and accept product outcomes; it may not alter or fabricate governed probabilities.
- Systems Intelligence & Reliability and Independent Verification remain independent assurance functions.
- A merged or deployed change is not automatically product-complete.

## Mission

WOW Betting Intelligence converts user objectives into governed cross-system workflows and is accountable for whether the intended product outcome is completed end to end.

It owns:

1. intent and objective compilation,
2. product scope and acceptance criteria,
3. product routing and candidate/work handoff integrity,
4. candidate-universe completeness,
5. product-level reconciliation,
6. capability/readiness truth,
7. roadmap and priority strategy,
8. product-outcome metrics,
9. evidence-backed improvement portfolio,
10. user-facing product truth.

It does not own probability math, specialist certification, system self-verification, production safety overrides, or wager execution.

## Ecosystem flow

```text
USER OBJECTIVE
      |
      v
WOW ECOSYSTEM CONDUCTOR
neutral ecosystem coordination + authority-boundary conservation
      |
      v
WOW BETTING INTELLIGENCE
objective + scope + completeness + acceptance contract
      |
      v
DISCOVERY / RESEARCH / SCOUT
candidate universe + evidence
      |
      v
EXACT CONTROLLING SPECIALIST
WOW Prop Lane | LLP Team Engine | Kalshi Weather Expert
      |
      v
GOVERNED PROBABILITY PACKAGE
      |
      v
DOWNSTREAM DECISION INTELLIGENCE
market/value/edge/correlation/card/portfolio where allowed
      |
      v
BETTING INTELLIGENCE RECONCILIATION
every admitted item receives one attributable disposition
      |
      v
INDEPENDENT VERIFICATION / GOVERNANCE
      |
      v
WOW ECOSYSTEM CONDUCTOR
cross-domain reconciliation
      |
      v
USER-FACING PRODUCT RESULT
```

Engineering and Systems Intelligence operate as cross-cutting partner functions:

- **WOW Engineering** builds, repairs, tests, deploys, and closes implementation work.
- **Systems Intelligence & Reliability** independently diagnoses systemic failure, resilience, evidence/provenance, contract drift, and release safety.
- **Independent Verification** proves closure and product acceptance.
- **Terminal governance** retains final governed publication authority.

## Canonical objective contract

Every material Betting Intelligence workflow must establish:

- `objective_id`
- `request_id`
- requested product outcome
- scope
- required systems
- freshness expectation
- completeness expectation
- acceptance criteria
- current owner
- `can_execute=false`
- `terminal_authority=V17_TERMINAL_REDUCER`

The objective contract is an orchestration contract only. It is not a probability package.

## Work-conservation invariant

Every admitted work item must have:

- stable identity,
- current owner,
- explicit state,
- typed blockers when blocked,
- evidence references when available,
- eventual terminal disposition.

Allowed terminal product-orchestration dispositions are:

- `SCORED`
- `BLOCKED`
- `PURGED`
- `SUPERSEDED`
- `UNSUPPORTED`
- `SELECTED`

`ADMITTED`, `ROUTED`, and `IN_PROGRESS` are nonterminal.

A workflow cannot be product-complete while any admitted work item remains nonterminal or while duplicate identities make reconciliation ambiguous.

## Capability truth

A capability record must keep the following concepts separate:

- model state,
- runtime state,
- discovery state,
- routing state,
- verification state,
- product readiness,
- blockers.

`product_ready=true` is fail-closed. It is invalid when a required readiness dimension is incomplete or a blocker remains.

Examples:

- runtime healthy + model unavailable = not product-ready,
- model ready + Action binding blocked = not product-ready,
- merged code + verification not run = not product-ready,
- all acceptance criteria passed + reconciliation balanced + independent verification passed = product-ready.

## Product acceptance states

Betting Intelligence uses these user-facing product states:

- `COMPLETE`
- `WORKING_NOT_COMPLETE`
- `BLOCKED`
- `FAILED`
- `SAFE_HOLD`
- `UNSUPPORTED`

`COMPLETE` requires all declared acceptance criteria, balanced work-item reconciliation, and independent verification.

Healthy runtime, a merged PR, or a passing narrow test is not sufficient by itself.

## Handoff receipts

Every cross-system ownership change should produce a receipt containing:

- work item identity,
- prior owner,
- next owner,
- typed reason,
- acceptance target,
- evidence references,
- accepted/rejected status,
- `can_execute=false`,
- terminal authority.

Conversational claims such as "handed to Engineering" are not closure evidence.

## Stagnation escalation

Repeated execution of the same failed action must not be classified as progress.

Product-level escalation:

- same blocker count 0–1: `RETRY_ALLOWED`
- same blocker count 2: `DIAGNOSIS_REQUIRED`
- same blocker count 3: `ESCALATE_P0`
- same blocker count 4+: `REDESIGN_OR_CAPABILITY_DECISION`

This sits above the existing engineering-worker stagnation and DLQ logic; it does not replace it.

## Product outcome metrics

The control plane standardizes four first-class measures:

### Product Outcome Completion Rate

```
completed supported requests / supported requests
```

A request counts only when the declared user outcome is satisfied and independently accepted.

### Candidate Conservation Rate

```
terminal admitted candidates / admitted candidates
```

Every candidate must have an attributable terminal disposition.

### First-Pass Completion Rate

```
supported requests completed without repair/reroute / supported requests
```

This measures orchestration quality rather than activity volume.

### Reliable Decision Availability

```
governed usable decisions / desired decisions
```

This prevents backend health or registered models from being misreported as actual user-ready coverage.

## Separation of authority

### WOW Ecosystem Conductor

Owns neutral ecosystem-wide coordination: cross-domain routing integrity, authority-boundary validation, ecosystem work envelopes, connection readiness, and proof that handoffs occurred. It does not own product semantics, probability, Engineering, verification, SAFE_HOLD authority, or terminal publication.

### Betting Intelligence

May:
- define product objectives and acceptance criteria,
- prioritize product work,
- coordinate systems,
- reconcile candidates and work,
- measure product outcomes,
- declare product completion only after independent evidence.

May not:
- create or change model probabilities,
- substitute market probability,
- certify a model,
- waive reliability holds,
- self-verify closure,
- override the terminal reducer,
- execute a wager.

### Specialist engines

Own governed probability for the routes assigned to them.

### Engineering

Owns implementation and repair.

### Systems Intelligence & Reliability

Owns independent systems diagnosis, prevention, resilience, and governed safety holds where separately authorized.

### Independent Verification

Owns proof that acceptance criteria were actually satisfied.

### V17 terminal governance

Owns final governed sporting publication semantics.

## Implementation

The executable repository contract is:

`artifacts/wow-engine/v17/betting_intelligence_control_plane.py`

Regression coverage is:

`artifacts/wow-engine/test_betting_intelligence_control_plane.py`

The control-plane module is intentionally pure and side-effect free. Durable storage/API integration should persist its typed snapshots and receipts through an existing governed persistence surface rather than adding a second ad-hoc source of truth.

The repository-level neutral ecosystem coordinator is defined separately in:

`artifacts/wow-engine/v17/ecosystem_conductor.py`

Its role is complementary rather than competing: Betting Intelligence owns product semantics; the Conductor owns ecosystem-wide coordination and authority-boundary conservation.
