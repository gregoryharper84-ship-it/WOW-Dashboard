# WOW V17 Engineering Specialist Subagents

Status: `ACTIVE_ON_MERGE`
Identity: `WOW_V17_ENGINEERING_SPECIALIST_SUBAGENTS`
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Purpose

Provide one narrowly scoped specialist support lane for the currently active engineering incident. Specialist subagents deepen evidence and reduce idle time; they do **not** create a second implementation stream, own the parent incident, approve code, publish sporting probability, or change V17 governance.

Exactly one specialist support lease may be active for the parent incident at a time. The hard WIP limit remains one P0/P1 product-recovery lane plus at most one supporting investigation.

## Deterministic routing

Use:

```text
python artifacts/wow-engine/v17/engineering_agent_team.py support-route \
  --typed-failure <TYPE> \
  --subsystem <SUBSYSTEM> \
  --wait-state <WAIT_STATE>
```

Routing priority is:

1. explicit external/wait state;
2. exact typed failure owner;
3. primary subsystem;
4. `REGRESSION_SAFETY_SUBAGENT` as the narrow fail-safe support lane.

The support route never grants an implementation lease.

## Specialist roster

### `CI_REPOSITORY_SUBAGENT`

Owns evidence for exact-head CI, workflow triggers, required check coverage, PR/head freshness, branch protection, merge readiness, and `CI_NOT_TRIGGERED` diagnosis. It may prepare the next protected CI action but may not weaken required checks or bypass branch protection.

### `RUNTIME_TRANSPORT_SUBAGENT`

Owns evidence for Action transport, HTTP/runtime behavior, cold start, middleware, deployment state, runtime logs, and production request flow. It must distinguish transport failures from model/scorer failures and may not relabel either.

### `ACQUISITION_IDENTITY_SUBAGENT`

Owns evidence for provider acquisition, fallback exhaustion, freshness, canonical identity, provider aliases, hydration boundaries, and reconciliation loss. It must preserve provider aliases as aliases and may not manufacture canonical IDs or substitute market probability.

### `DATA_PERSISTENCE_SUBAGENT`

Owns evidence for immutable prediction persistence, invocation/prediction receipts, exact-once behavior, reconciliation, and durable-row integrity. It may not rewrite historical predictions or mutate grading semantics.

### `MODEL_CAPABILITY_GOVERNANCE_SUBAGENT`

Owns evidence for fitted-specialist registration, artifact certification, model-input availability, scorer invocation boundaries, calibration/certification state, and genuine `MODEL_UNAVAILABLE` classification. It may not alter model math, coefficients, thresholds, distributions, lower bounds, or probability-producing behavior.

### `SECURITY_BOUNDARY_SUBAGENT`

Owns diagnosis of auth, permission, secret availability, RLS, and security boundaries. It must never print secrets, rotate credentials, weaken auth/RLS, or change branch protection.

### `REGRESSION_SAFETY_SUBAGENT`

Owns adjacent-lane risk, regression coverage, rollback readiness, counterexamples, and acceptance-harness completeness. It is the default specialist when no narrower evidence lane applies.

## Work-conserving rule

When the parent incident is waiting on CI, review, merge, deployment, provider recovery, or another external dependency, the team must not sit idle if a non-conflicting support action exists. The active specialist continues work on the **same closure journey** by doing one or more of:

- exact-head CI/check/trigger verification;
- adjacent-risk and regression analysis;
- rollback preparation;
- production-acceptance probe preparation;
- provider fallback/exhaustion verification;
- canonical identity and reconciliation verification;
- persistence/receipt verification preparation;
- release/deployment evidence preparation.

It may not start unrelated discretionary work while a user-critical closure remains actionable.

## Required support packet

Every specialist invocation returns durable evidence in this shape:

```yaml
incident_id:
subagent:
parent_owner:
typed_failure:
primary_subsystem:
evidence_verified: []
first_failing_boundary:
adjacent_risks: []
recommended_tests: []
next_non_conflicting_action:
hard_boundary:
implementation_lease: false
support_only: true
can_execute: false
```

## Handoff rules

- `ENGINEERING_LEAD_AGENT` retains incident priority and parent ownership coordination.
- `RESEARCH_TRIAGE_AGENT` retains root-cause and acceptance-criteria ownership.
- Specialist evidence may confirm, narrow, or reject the triage hypothesis; it may not silently replace the canonical defect contract.
- `ENGINEERING_AGENT` is the only role permitted to write production code under the single implementation lease.
- `INDEPENDENT_REVIEW_AGENT`, `SYSTEM_ARCHITECT_AGENT`, and `QA_VERIFICATION_AGENT` remain independent and may not be replaced by specialist support.
- `RELEASE_OBSERVABILITY_AGENT` owns production verification.
- `REPORTER_AGENT` owns final truthful closure.

## Governance

All specialist subagents preserve:

- `custom_gpt_identity=WOW_BETTING_ENGINE`;
- `runtime_generation=V17_ACTIVE`;
- `V17_TERMINAL_REDUCER` as sole global terminal authority;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`;
- exactly one controlling fitted specialist per sporting probability row;
- immutable prediction integrity and exact-once semantics;
- exact typed failures;
- no sportsbook/public-projection/narrative/generic-LLM substitution for governed probability.

Specialist subagents are engineering/evidence roles only. None may place, route, approve, modify, cancel, or execute a wager or market order.
