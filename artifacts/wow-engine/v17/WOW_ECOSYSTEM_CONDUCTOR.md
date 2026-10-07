# WOW Ecosystem Conductor / Control Plane — V1

**Status:** CLASS_A_FOUNDATION  
**Runtime:** V17_ACTIVE  
**Probability authority:** NONE  
**Global terminal authority:** V17_TERMINAL_REDUCER  
**Execution authority:** can_execute=false

## Purpose

The WOW Ecosystem Conductor is the neutral coordination layer connecting the WOW product ecosystem without becoming a betting model, a weather model, Engineering, or terminal authority.

It exists to make sure intelligence, evidence, model outputs, reliability findings, engineering work, verification, durable receipts, and user-facing product outcomes move through the correct boundaries without being lost, duplicated, silently degraded, or falsely reported as healthy.

The Conductor coordinates:

- WOW V17 — Betting Intelligence / Product Orchestration
- Scout / Research
- WOW Betting Engine and governed prop specialists
- LLP Team Betting Engine and its exact team/event specialists
- Kalshi Weather Market Expert
- WOW Systems Intelligence & Reliability
- Engineering & Closure
- Independent Verification
- V17 terminal reduction
- durable persistence/publication
- final user-facing output

## Architectural decision

Systems Intelligence & Reliability is **not** the sole conduit.

That would combine diagnosis/reliability authority with product orchestration and create an avoidable single point of organizational failure.

Instead:

- **WOW Betting Intelligence** owns user intent, objective compilation, product priorities, candidate-universe completeness, product acceptance, and user-facing decision packaging.
- **Systems Intelligence & Reliability** owns connection health, dependency/contract monitoring, drift, incidents, repeat-failure intelligence, readiness, and objective SAFE_HOLD.
- **Engineering & Closure** owns implementation, repair, merge, deploy, and closure.
- **Independent Verification** proves closure independently.
- **Exact governed specialists** retain their probability authority.
- **V17_TERMINAL_REDUCER** remains the sole global V17 terminal authority.
- **WOW Ecosystem Conductor** coordinates the flow and proves that the handoffs occurred.

## Core invariants

The Conductor MUST NOT:

- calculate or alter governed sporting probability;
- calculate or alter governed meteorological probability;
- override controlling specialist ownership;
- soften or rename typed failures to create a usable result;
- use Scout/research confidence as governed probability;
- use sportsbook or Kalshi price as a substitute for independent probability;
- allow Kalshi market movement to mutate independent weather probability;
- bypass Class A/B/C governance;
- grant wagering/trading execution authority;
- verify its own engineering closure.

The following remain binding:

```text
runtime_generation = V17_ACTIVE
terminal_authority = V17_TERMINAL_REDUCER
can_execute = false
```

## Conductor responsibilities

### 1. Connection integrity

Track every required handoff as an explicit contract.

A component being healthy does not prove its upstream or downstream connection is healthy.

### 2. Golden-path readiness

Maintain separate end-to-end paths for at least:

- WOW prop workflows
- LLP team/event workflows
- Kalshi Weather workflows

Additional product paths should be added without weakening existing ones.

### 3. False-green prevention

Never infer user readiness from:

- HTTP 200;
- repository CI alone;
- model registration alone;
- Scout discovery alone;
- a healthy database alone;
- a healthy deployment alone;
- a successful individual specialist call alone.

A user workflow is ready only when every required handoff in its golden path is proven.

### 4. SAFE_HOLD

Systems Intelligence & Reliability may assert ecosystem SAFE_HOLD when objective connection, contract, governance, or invariant evidence is missing or failed.

SAFE_HOLD does not rewrite model output. It blocks an unproven release/readiness claim.

### 5. Truthful status dimensions

Keep these states separate:

- infrastructure/runtime health
- connection/handoff health
- data/evidence health
- model/specialist capability
- model readiness
- repository/governance state
- editor/MCP/Action synchronization
- persistence/publication health
- independent verification state
- user-workflow readiness

### 6. End-to-end reconciliation

Every routed unit of work must remain attributable until terminal disposition.

No candidate, incident, engineering handoff, verification request, or governed result should silently disappear between layers.

## V1 implementation

V1 introduces:

```text
artifacts/wow-engine/v17/ecosystem-registry.json
artifacts/wow-engine/v17/ecosystem_conductor.py
artifacts/wow-engine/tests/test_v17_ecosystem_conductor.py
```

The registry declares system roles, authority boundaries, handoffs, and golden paths.

The deterministic Conductor evaluates:

- registry validity;
- runtime invariants;
- component state;
- handoff state;
- broken connections;
- degraded connections;
- route-specific golden-path readiness;
- false-green conditions;
- SAFE_HOLD state.

## Next build phases

### Phase 2 — Live adapters

Attach authoritative probes for:

- GitHub/repository lifecycle
- Render/runtime/deploy
- Supabase/persistence
- Action/MCP transport
- Scout
- WOW prop paths
- LLP team/event paths
- Kalshi Weather paths
- independent verification receipts

### Phase 3 — Durable connection ledger

Persist time-stamped handoff and readiness receipts so current state can be compared with prior state and recurrent breaks can be identified.

### Phase 4 — Product capability matrix

Expose route-specific readiness by sport, market, specialist, station/contract family, and product workflow.

### Phase 5 — Opportunity and incident routing

Convert proven systemic findings into evidence-backed Engineering Opportunity Queue items or P0/P1 incidents with exact root-cause boundary, reproduction, impact, acceptance criteria, and verification requirements.

### Phase 6 — Reliability Control Plane

Provide one coherent view of:

- Reliable Decision Availability
- Product Outcome Completion Rate
- Decision Integrity Rate
- Repeat Failure Elimination Rate
- Detection-to-Proven-Root-Cause Time
- broken handoffs
- active SAFE_HOLDs
- product capability/readiness
- engineering closure flow
- independent verification state

## Acceptance condition

The Conductor is successful when the ecosystem can answer, from durable evidence:

1. What did the user ask for?
2. Which product/intelligence owned orchestration?
3. Which candidates/evidence entered the flow?
4. Which exact specialist owned each governed probability?
5. Which connections were traversed?
6. Which typed blockers occurred?
7. Was the result persisted and publication-authorized?
8. Was the user workflow genuinely complete?
9. If not, where is the first proven failing boundary?
10. Which team owns the next action?

The answer must not depend on inference from prose or on one component claiming another component succeeded.
