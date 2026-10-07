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
- **WOW Ecosystem Conductor** is the neutral ecosystem control plane for routing, handoff integrity, lifecycle state, and work conservation. It coordinates the flow but does not acquire product, probability, verification, safety, engineering, or terminal authority.

Engineering is an execution function, not part of the independent assurance function. Independent Verification remains separate from the implementation team whose work it verifies.

The canonical user path is:

```text
USER
  -> WOW_ECOSYSTEM_CONDUCTOR
  -> WOW_BETTING_INTELLIGENCE
  -> discovery/evidence as needed
  -> exact controlling specialist
  -> V17 terminal governance where applicable
  -> persistence/publication where applicable
  -> WOW_BETTING_INTELLIGENCE reconciliation
  -> WOW_ECOSYSTEM_CONDUCTOR
  -> USER
```

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

### 6. End-to-end reconciliation and ecosystem work conservation

Betting Intelligence retains its product-level objective, candidate lifecycle, reconciliation, acceptance, and metric contracts. The Conductor does not redefine those semantics.

For cross-system coordination, every admitted unit of work is additionally carried in a typed **ecosystem ownership envelope** so Reliability, Engineering, Verification, model hosts, terminal governance, and persistence can be reconciled without taking over the product work model.

The ecosystem envelope carries:

```text
work_item_id
request_id
objective_id
candidate_id
source
current_owner
next_owner
state
blocking_reason
evidence_refs
specialist_route
verification_state
terminal_state
authority_domain
change_class
decision_right
required_verifier
promotion_state
updated_at
lease_expires_at
```

The invariant is: **everything admitted must either remain validly owned and advancing, be explicitly blocked with an exact reason, or terminate with an attributable terminal state.**

Active ownership is time-bounded. A nonterminal envelope must carry an offset-aware `updated_at` and a future `lease_expires_at`; an expired lease fails closed instead of allowing indefinite `IN_PROGRESS`.

When `current_owner=ENGINEERING_CLOSURE`, terminal dispositions are restricted to:

```text
FIXED_AND_VERIFIED
PR_CREATED
EXPERIMENT_CREATED
DUPLICATE
NOT_REPRODUCIBLE
BLOCKED_WITH_EXACT_REASON
DEFERRED_WITH_JUSTIFICATION
```

The Conductor fails closed on missing ownership, invalid authority domains, unregistered next owners, silent terminal loss, duplicate ecosystem work IDs, blockers without reasons, Engineering self-verification, `FIXED_AND_VERIFIED` without Independent Verification, or Class C promotion without verified independent review.

No candidate, incident, engineering handoff, verification request, or governed result may silently disappear between layers.

## Relationship to Betting Intelligence

WOW Betting Intelligence remains the **Product Intelligence & Orchestration Control Plane**. It owns objective contracts, candidate/work semantics within the product workflow, product acceptance, stagnation policy, and the metric family.

WOW Ecosystem Conductor is the **neutral ecosystem-wide control plane**. It owns cross-domain routing integrity, authority-boundary validation, ecosystem ownership envelopes, connection readiness, and proof that handoffs across independent domains occurred.

This separation is intentional:

- product semantics stay in Betting Intelligence;
- cross-system coordination stays in the Conductor;
- probability stays in exact specialists;
- implementation stays in Engineering;
- SAFE_HOLD authority stays in Systems Intelligence & Reliability;
- proof stays in Independent Verification;
- terminal authority stays in V17_TERMINAL_REDUCER.

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
- typed ecosystem-envelope conservation state;
- authority-domain consistency;
- self-verification violations;
- Class C promotion safety;
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

### Phase 3 — Durable work and connection ledger

Persist time-stamped ecosystem-envelope, product work-item, handoff, ownership, verification, and readiness receipts so current state can be compared with prior state, stale ownership can be detected, and recurrent breaks can be attributed.

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
7. Who owns every still-active work item, under which authority domain and change class?
8. Did any work item disappear, duplicate, self-verify, or cross an authority boundary incorrectly?
9. Was the result persisted and publication-authorized where required?
10. Was the user workflow genuinely complete?
11. If not, where is the first proven failing boundary?
12. Which team owns the next action?

The answer must not depend on inference from prose or on one component claiming another component succeeded.
