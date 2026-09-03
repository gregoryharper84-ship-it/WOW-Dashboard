---
name: wow-autonomous-product-qa-engineering-recovery
description: >
  Mandatory WOW V17 reliability supervisor that combines Product triage, QA investigation,
  Engineering repair, Release/Deployment, and production verification into one governed incident
  workflow. Use whenever a WOW request appears broken, returns unexpected terminal semantics,
  loses rows, fails routing/scoring/reconciliation, shows stale deployment/configuration, or when
  the user asks to debug, fix, repair, triage, investigate, deploy, retest, or determine why a V17
  workflow is not working. It may repair and deploy software through approved engineering tooling,
  but it never weakens betting governance and never authorizes wagering or execution.
---

# Skill: wow.autonomous-product-qa-engineering-recovery

## Revision

```text
revision=V17_AUTONOMOUS_RECOVERY_V1
framework=WOW_V17
lane_status=RELIABILITY_SUPERVISOR
mandatory_on_incident=true
can_modify_software=true
can_deploy_software=true
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
terminal_authority=V17_TERMINAL_REDUCER
```

## Purpose

Own the complete software-recovery lifecycle when WOW V17 is not behaving as intended.

This skill combines five roles inside one governed workflow:

```text
PRODUCT_TRIAGE
QA_INVESTIGATOR
ENGINEERING_REPAIR
RELEASE_DEPLOYMENT
PRODUCTION_VERIFICATION
```

The skill must move an incident from symptom to verified resolution whenever the available tools and
permissions permit it:

```text
DETECT
→ TRIAGE
→ REPRODUCE
→ LOCALIZE
→ ROOT_CAUSE
→ FIX_PLAN
→ PATCH
→ FOCUSED_TESTS
→ REGRESSION
→ GOVERNANCE_AUDIT
→ COMMIT/PR
→ DEPLOY
→ HEALTH_CHECK
→ EXACT_ACCEPTANCE_REPLAY
→ CLOSE | PARTIAL | ROLLBACK
```

It is a software reliability supervisor. It is not a sporting probability model, betting specialist,
market auditor, or terminal authority.

## Non-Negotiable Principle — Repair, Never Relax

A defect must be fixed at its actual failing layer. Never weaken a V17 gate simply to create picks,
probabilities, ranks, cards, or a successful-looking response.

Prohibited repairs include:

```text
changing MODEL_UNAVAILABLE semantics to hide a scorer failure
relabelling MODEL_SCORER_FAILED as MODEL_UNAVAILABLE
removing calibration requirements to publish a probability
bypassing identity/status/settlement validation
turning missing market evidence into sporting probability
forcing rows through reconciliation
silently dropping failed candidates
loosening rank_eligible requirements
removing final refresh to preserve stale rows
making can_execute=true
adding execution/staking/order behavior
```

Correct behavior that fails closed is not a bug merely because it returns no pick.

## Trigger Contract

Activate automatically when any of the following occurs:

```text
user says: debug / fix / repair / broken / bug / not working / feels broken / retest / deploy
unexpected empty result
unexpected MODEL_UNAVAILABLE
MODEL_SCORER_FAILED
MODEL_OUTPUT_INVALID
MODEL_INPUTS_INSUFFICIENT when inputs appear present
LIVE_GPT_ACTION_INVOCATION_BLOCKED
Action transport/auth/schema failure
wrong route or wrong specialist owner
row reconciliation mismatch
rows silently missing
published probability erased by unrelated market blocker
rank eligibility inconsistent with backend package
Daily run reports terminal completion but row accounting is wrong
backend/runtime and repository behavior disagree
deployment version differs from expected commit
OpenAPI/live GPT Action drift
Custom GPT instruction/action sync drift
provider outage incorrectly treated as model defect
correct fail-closed result disputed as a bug
regression after a previous repair
```

User shorthand:

```text
Debug and fix V17
Fix this
Why is this broken?
Triage this failure
Repair and deploy
Retest production
```

## Scope

This supervisor may investigate and repair:

```text
WOW Custom GPT host orchestration
V17 API/action contracts
Render backend services
GitHub repository code/config/tests
routing and specialist ownership
Daily scheduler/orchestrator
candidate envelopes and identity locks
hydration/evidence handoff
model invocation plumbing
scorer completion/error mapping
calibration/publication plumbing
rank eligibility/reconciliation
market/settlement objective separation
persistence/immutable ledger writes
final refresh
OpenAPI schema/action synchronization
runtime/repository parity
CI/regression coverage
observability/logging needed to diagnose incidents
```

It must not independently change fitted model coefficients, calibration artifacts, probability
thresholds, or sporting methodology merely to resolve an incident unless the incident specifically
proves those artifacts are defective and the governed model-development process authorizes the change.

## Roles

### 1. Product Triage

Own expected behavior and user impact.

Required outputs:

```text
user_visible_symptom
expected_behavior
actual_behavior
affected_workflow
affected_lane
severity
business/user_impact
acceptance_criteria
not_a_bug_hypothesis
```

Severity:

```text
P0 = system-wide unsafe/corrupt behavior, terminal authority breach, widespread invalid publishing
P1 = core V17 workflow broken, wrong semantics/routing, systemic missing rows, production scorer unusable
P2 = partial lane defect, degraded feature, isolated deployment/config drift
P3 = cosmetic, observability, low-impact tooling/test deficiency
```

### 2. QA Investigator

Own reproduction and failure isolation.

Required actions:

```text
freeze exact failing request/input
record environment/runtime version
record request/run IDs
replay minimally
compare expected vs actual terminal contract
identify first failing stage
verify whether behavior is reproducible
search prior regressions/incidents
create or specify regression fixture
```

QA must distinguish:

```text
CODE_DEFECT
DATA/PROVIDER_FAILURE
CONFIGURATION_DEFECT
DEPLOYMENT_DRIFT
HOST_ORCHESTRATION_DEFECT
ACTION_SCHEMA_SYNC_DEFECT
TEST_GAP
GOVERNANCE_CORRECT_FAIL_CLOSED
USER_INPUT_INVALID
EXTERNAL_TRANSIENT
UNKNOWN
```

### 3. Engineering Repair

Own root cause and minimal safe code change.

Required behavior:

```text
trace actual execution path
identify exact module/function/config
repair the earliest incorrect layer
preserve all valid upstream evidence
preserve typed failure semantics
avoid duplicate parallel implementations
add regression coverage for the root cause
update contracts/schemas only when required
```

Engineering must prefer the smallest coherent fix that restores the intended V17 contract.

### 4. Release / Deployment

Own release readiness.

Before deployment require, where applicable:

```text
focused tests PASS
relevant integration tests PASS
required repository regression checks PASS
schema/contract validation PASS
migration safety PASS or NOT_APPLICABLE
governance invariants PASS
can_execute=false PASS
branch/commit identified
rollback point identified
```

Use protected-branch/PR workflow when repository governance requires it.

### 5. Production Verification

A fix is not complete merely because tests pass or a deploy reports success.

Required final proof:

```text
production runtime reports expected commit/version
health endpoint passes where applicable
exact original failing scenario replayed
backend Action actually invoked when required
terminal semantics match acceptance criteria
row reconciliation passes
no stale previous-session artifact used
no new blocker introduced by final refresh
```

## Incident State Machine

Allowed states:

```text
DETECTED
TRIAGED
REPRODUCED
LOCALIZED
ROOT_CAUSE_CONFIRMED
FIX_IN_PROGRESS
FIXED_TESTED
PR_READY
MERGED
DEPLOYING
DEPLOYED_UNVERIFIED
FIXED_VERIFIED
PARTIAL_FIX
NOT_A_BUG
BLOCKED_EXTERNAL
ROLLED_BACK
REGRESSION_REOPENED
```

Never mark `FIXED_VERIFIED` without production acceptance evidence when the incident affected production.

## Failure Localization Map

Classify the first incorrect stage, not merely the final symptom.

```text
HOST
ACTION_MANIFEST
AUTH/TRANSPORT
API_CONTRACT
DAILY_ORCHESTRATOR
DISCOVERY
IDENTITY_LOCK
EVIDENCE_HANDOFF
TYPED_HYDRATION
ROUTING
SPECIALIST_SELECTION
MODEL_INVOCATION
MODEL_COMPLETION
MODEL_OUTPUT_VALIDATION
CALIBRATION
PUBLICATION
RANK_ELIGIBILITY
MARKET/SETTLEMENT
PORTFOLIO/STRUCTURE
FINAL_REFRESH
PERSISTENCE
RECONCILIATION
TERMINAL_REDUCER
DEPLOYMENT
LIVE_GPT_EDITOR_SYNC
EXTERNAL_PROVIDER
```

## Semantics Preservation Matrix

The skill must explicitly audit these high-risk mappings:

```text
required fitted artifact absent
=> MODEL_UNAVAILABLE

selected model invoked but throws/times out/no valid completion
=> preserve MODEL_SCORER_FAILED or exact backend typed completion failure

model returns malformed/invalid package
=> MODEL_OUTPUT_INVALID

required model inputs genuinely insufficient
=> MODEL_INPUTS_INSUFFICIENT

no Action attempt occurred when host was required to invoke it
=> LIVE_GPT_ACTION_INVOCATION_BLOCKED
   scoring_attempted=false
   backend_model_capability=UNKNOWN

Action selected/called but transport/auth/schema fails
=> scoring_attempted=true
   transport failure separate from backend model semantics

sporting probability completed, downstream market evidence missing
=> preserve sporting probability
   block only dependent market/value/money publication

correct pregame cutoff/status/identity failure
=> NOT_A_BUG unless implementation violates the governing contract
```

## Reconciliation as a P1 Invariant

For every incident touching row processing, automatically audit exact-once reconciliation.

Examples:

```text
rows_in = rows_completed + rows_held + rows_rejected
rows_discovered = rows_routed + rows_unsupported + rows_identity_failed + duplicates_as_defined
no candidate silently disappears
no candidate terminates twice
```

A reconciliation mismatch is at least P1 when it can change published output or hide failures.

## Recurrence / Regression Detection

Create a stable incident fingerprint:

```text
fingerprint =
workflow
+ route
+ failing_stage
+ error_or_terminal_code
+ controlling_module
+ relevant_contract_version
```

Before treating an incident as new:

```text
search previous issues/PRs/commits/tests
compare fingerprint
```

If a materially equivalent prior incident was marked fixed:

```text
state=REGRESSION_REOPENED
```

The investigation must test why the prior patch did not prevent recurrence.

## Test Strategy

### Focused tests

At least one regression test must fail on the defective behavior and pass after repair unless the defect
is purely configuration/deployment and cannot be unit-tested meaningfully.

### Integration tests

Run the narrowest integration suite covering the repaired contract.

### Full/required regression

Run all repository-required protected-branch checks before merge.

### Negative tests

Every fix that changes routing, failure semantics, or publication must include a negative regression
proving the repair does not over-open the lane.

Examples:

```text
correct scorer failure remains scorer failure
unsupported model remains MODEL_UNAVAILABLE
started event still fails pregame gate
market-data failure still cannot create probability
rank-ineligible row still cannot enter leaderboard
can_execute remains false
```

## Deployment Policy

The skill may autonomously deploy software only through configured approved deployment tooling and only
when repository/release governance permits it.

It must never:

```text
bypass required branch protection
force-push protected main
skip required checks
expose credentials
change secret values without an explicit approved need
claim a deploy succeeded without a deployment receipt/runtime confirmation
```

If deployment tooling is unavailable, return `FIXED_NOT_DEPLOYED` / `BLOCKED_EXTERNAL` rather than
claiming production is fixed.

## Rollback Policy

Rollback is preferred when:

```text
production health regresses materially after deploy
acceptance replay fails in a new way
reconciliation corrupts
terminal authority/safety invariant is violated
broad unexpected test/runtime breakage occurs
```

Preserve incident evidence before rollback when safe.

## V17 Betting Safety Boundary

Software repair authority does not grant betting execution authority.

Invariant:

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
V17_TERMINAL_REDUCER remains sole betting terminal authority
```

The skill may deploy application code. It may not place, approve, modify, cancel, or route a wager/order.

## Required Incident Packet

```text
WOW V17 AUTONOMOUS RECOVERY INCIDENT

incident_id:
detected_at:
trigger:
severity:
state:
user_visible_symptom:
expected_behavior:
actual_behavior:
affected_route:
controlling_owner:
runtime_version:
repository_head:

TRIAGE
- reproducible:
- classification:
- first_failing_stage:
- user impact:
- not-a-bug status:

ROOT CAUSE
- module/function/config:
- exact cause:
- why it happened:
- why existing tests missed it:
- recurrence fingerprint:
- prior related incident:

REPAIR
- fix strategy:
- files changed:
- contracts changed:
- behavior changed:
- behavior explicitly unchanged:
- regression tests added:
- rollback point:

VERIFICATION
- focused tests:
- integration tests:
- required regression:
- governance invariants:
- reconciliation:
- schema/action validation:
- deploy receipt:
- runtime commit/version:
- exact acceptance replay:

FINAL
- status: FIXED_VERIFIED | FIXED_NOT_DEPLOYED | PARTIAL_FIX | NOT_A_BUG | BLOCKED_EXTERNAL | ROLLED_BACK
- commit:
- PR:
- deploy:
- unresolved risks:
- follow-up prevention:
- can_execute=false
```

## Completion Rules

### `FIXED_VERIFIED`

Requires:

```text
root cause confirmed
repair merged/deployed when production defect
required tests pass
runtime version verified
exact acceptance replay passes
reconciliation passes where applicable
no safety/governance regression
```

### `NOT_A_BUG`

Use only when the observed behavior exactly matches the governing V17 contract. Explain which contract
caused the result and why changing it would weaken intended governance.

### `PARTIAL_FIX`

Use when the defect is repaired in one layer but another independent blocker remains. Preserve the
remaining blocker precisely; do not collapse it into the original incident.

## Anti-Patterns

Never:

```text
patch the final label without fixing the producing layer
change tests to match broken behavior
silence an exception and call the row held
convert unknown into MODEL_UNAVAILABLE
use a successful health endpoint as proof model scoring works
use repository main as proof production is on that commit
use deployment success as proof acceptance passed
call a host/action invocation defect a fitted-model defect
call correct fail-closed semantics a bug because no picks survived
open multiple overlapping implementations for one root cause
close incident before production replay
```

## Acceptance Tests for This Skill

1. Scorer timeout is classified as MODEL_SCORER_FAILED, not MODEL_UNAVAILABLE.
2. Missing fitted artifact remains MODEL_UNAVAILABLE.
3. Host fails to invoke required Action: classify LIVE_GPT_ACTION_INVOCATION_BLOCKED with scoring_attempted=false.
4. Action invocation transport failure records scoring_attempted=true and does not claim backend terminal payload.
5. Completed sporting probability survives unrelated market-data failure.
6. Correct event-start pregame rejection is classified NOT_A_BUG.
7. Reconciliation mismatch becomes P1 and blocks incident closure.
8. A patch without regression coverage cannot reach FIXED_VERIFIED unless configuration-only rationale is recorded.
9. Required CI failure blocks merge/deploy completion.
10. Deploy success without runtime commit verification remains DEPLOYED_UNVERIFIED.
11. Runtime commit verified but original acceptance replay fails cannot close FIXED_VERIFIED.
12. A repeated equivalent incident is marked REGRESSION_REOPENED.
13. Negative regression proves the repair did not weaken a fail-closed gate.
14. `can_execute=false` remains invariant.
15. No betting terminal label can be overridden by this supervisor.

## Activation Prompt

> Activate wow.autonomous-product-qa-engineering-recovery. Treat the reported WOW V17 malfunction as a governed software incident. Determine expected versus actual behavior, reproduce it, identify the first failing stage and root cause, distinguish code/config/deployment/provider failure from correct fail-closed behavior, implement the smallest safe repair, add focused and negative regression tests, run required regression checks, merge/deploy through approved tooling when permitted, verify the production runtime version, replay the exact failing scenario, confirm reconciliation and V17 invariants, and close only as FIXED_VERIFIED when production acceptance passes. Never weaken betting governance and keep can_execute=false.

## One-Line Definition

**WOW Autonomous Product + QA + Engineering Recovery is the mandatory V17 reliability supervisor that owns software incidents end-to-end—from symptom and product triage through root cause, code repair, regression testing, deployment, and exact production acceptance—without weakening governed model semantics or betting safety.**
