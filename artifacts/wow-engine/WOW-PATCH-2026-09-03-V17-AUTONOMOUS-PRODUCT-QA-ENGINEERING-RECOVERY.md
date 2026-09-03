# WOW-PATCH-2026-09-03-V17-AUTONOMOUS-PRODUCT-QA-ENGINEERING-RECOVERY

## Status

```text
status=PROPOSED_ACTIVE_PROJECT_CONTRACT
patch_priority=CRITICAL
framework=WOW_V17
activation_date=2026-09-03
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
terminal_authority=V17_TERMINAL_REDUCER
```

## Purpose

Make `wow.autonomous-product-qa-engineering-recovery` the mandatory reliability supervisor for WOW V17 software incidents.

The patch exists because repeated V17 failures have crossed product, QA, engineering, deployment, host orchestration, and runtime boundaries. A single failure can otherwise be misclassified at the final symptom instead of repaired at the first incorrect stage.

This patch establishes one end-to-end owner for:

```text
symptom detection
product triage
reproduction
failure localization
root cause analysis
code/config repair
regression coverage
release readiness
deployment
production acceptance replay
incident closure
```

## Core Rule

```text
REPAIR_THE_PRODUCING_LAYER
NEVER_RELAX_GOVERNANCE_TO_CREATE_OUTPUT
```

A missing pick is not itself a defect. Correct fail-closed behavior remains correct.

## Mandatory Trigger

The supervisor must be invoked when a user explicitly reports a bug/failure or when V17 emits an unexpected system-level condition including:

```text
LIVE_GPT_ACTION_INVOCATION_BLOCKED
MODEL_SCORER_FAILED
MODEL_OUTPUT_INVALID
unexpected MODEL_UNAVAILABLE
unexpected MODEL_INPUTS_INSUFFICIENT
Action transport/auth/schema failure
wrong controlling specialist
row reconciliation mismatch
silent row loss
Daily completion accounting mismatch
sporting probability erased by downstream market blocker
backend/repository/runtime version drift
OpenAPI/Custom GPT Action drift
live GPT instruction sync drift
repeated regression fingerprint
```

## Incident Ownership

The skill owns the software incident, but it does not become the sporting-probability owner.

```text
reliability_owner=wow.autonomous-product-qa-engineering-recovery
sporting_probability_owner=unchanged controlling specialist
terminal_authority=V17_TERMINAL_REDUCER
```

## Required Internal Roles

```text
PRODUCT_TRIAGE
QA_INVESTIGATOR
ENGINEERING_REPAIR
RELEASE_DEPLOYMENT
PRODUCTION_VERIFICATION
```

These are internal responsibilities of one skill and do not create competing terminal publishers.

## Mandatory Incident Lifecycle

```text
DETECTED
→ TRIAGED
→ REPRODUCED
→ LOCALIZED
→ ROOT_CAUSE_CONFIRMED
→ FIX_IN_PROGRESS
→ FIXED_TESTED
→ PR_READY
→ MERGED
→ DEPLOYED_UNVERIFIED
→ FIXED_VERIFIED
```

Allowed alternate terminal states:

```text
PARTIAL_FIX
NOT_A_BUG
BLOCKED_EXTERNAL
ROLLED_BACK
REGRESSION_REOPENED
```

## Production Closure Gate

Production incidents may not close `FIXED_VERIFIED` until all applicable checks pass:

```text
focused regression PASS
negative regression PASS
required CI/regression PASS
runtime commit/version VERIFIED
health check PASS
exact original scenario replay PASS
terminal semantics PASS
row reconciliation PASS
V17 safety/governance invariants PASS
```

A successful deployment receipt alone is insufficient.

## Failure Semantics Regression Guard

The supervisor must protect these distinctions:

```text
absent required fitted artifact
=> MODEL_UNAVAILABLE

selected model invoked but throws/times out/no valid completion
=> MODEL_SCORER_FAILED or exact typed scorer/completion failure

malformed probability package
=> MODEL_OUTPUT_INVALID

genuinely insufficient required model inputs
=> MODEL_INPUTS_INSUFFICIENT

required host Action never attempted
=> LIVE_GPT_ACTION_INVOCATION_BLOCKED
   scoring_attempted=false
   backend_model_capability=UNKNOWN

Action attempted but transport/auth/schema fails
=> scoring_attempted=true
   transport failure reported separately

completed sporting probability + market evidence unavailable
=> sporting probability preserved
   dependent market/value/money publication held
```

## Reconciliation Gate

Any workflow that processes candidate rows must prove exact-once terminal reconciliation.

```text
no_silent_row_loss=true
no_double_terminal_rows=true
```

A reconciliation defect capable of changing published results is P1 minimum.

## Not-a-Bug Gate

The supervisor must explicitly test whether the observed behavior is required by V17 governance before changing code.

Examples that may be correct fail-closed behavior:

```text
started event rejected from pregame lane
wrong date/year removed
identity conflict blocked
unsupported fitted route MODEL_UNAVAILABLE
rank-ineligible probability excluded from official ranking
market evidence missing blocks only market/value output
```

If behavior matches the contract exactly:

```text
incident_state=NOT_A_BUG
code_change_required=false
```

## Regression Fingerprinting

Every incident records:

```text
workflow
route
first_failing_stage
error_or_terminal_code
controlling_module
contract_version
```

Equivalent prior incident found after a claimed fix:

```text
incident_state=REGRESSION_REOPENED
```

## Release Governance

The supervisor may create branches, commits, PRs, merge after required checks/permissions, and deploy through approved deployment tooling.

It may not:

```text
bypass protected branch checks
force-push protected main
skip required tests
claim deployment without receipt
claim production fix without replay
expose secrets
change betting execution authority
```

## Deployment Scope

Software deployment is permitted under this reliability skill. Betting execution is not.

```text
can_modify_software=true
can_deploy_software=true
can_execute=false
```

## Integration Requirement — WOW V17 Custom GPT Instructions

Add a controlling section equivalent to:

```text
AUTONOMOUS PRODUCT + QA + ENGINEERING RECOVERY — CONTROLLING
- For any explicit bug/debug/fix/repair/retest/deploy request, or an unexpected V17 host/action/runtime/reconciliation failure, invoke `wow.autonomous-product-qa-engineering-recovery` as the mandatory reliability supervisor.
- The supervisor owns incident triage, reproduction, first-failing-stage isolation, root cause, repair, tests, approved release/deployment, runtime verification and exact acceptance replay.
- It does not own sporting probability and cannot override the controlling specialist or V17_TERMINAL_REDUCER.
- Repair the producing layer; never weaken a gate to manufacture a pick.
- Preserve backend failure semantics exactly: absent fitted artifact = MODEL_UNAVAILABLE; invoked scorer failure = MODEL_SCORER_FAILED/typed scorer failure; malformed package = MODEL_OUTPUT_INVALID; insufficient inputs = MODEL_INPUTS_INSUFFICIENT; required Action not attempted = LIVE_GPT_ACTION_INVOCATION_BLOCKED with scoring_attempted=false.
- Production bugs are not FIXED_VERIFIED until required regression checks pass, production runtime version is confirmed, the original failing scenario is replayed successfully, and reconciliation/governance invariants pass.
- Correct fail-closed behavior may terminate NOT_A_BUG.
- `can_execute=false` remains invariant.
```

## Integration Requirement — Daily / Props / LLP / Kalshi

Every lane must route software malfunction investigation to the supervisor without transferring probability ownership.

```text
Daily failure -> recovery supervisor -> Daily remains business workflow owner
Prop failure -> recovery supervisor -> WOW prop specialist remains probability owner
Team/event failure -> recovery supervisor -> LLP_TEAM_BETTING_ENGINE remains probability owner
Kalshi failure -> recovery supervisor -> exact contract specialist remains probability owner
```

## Required Incident Output

```text
incident_id
severity
state
user_visible_symptom
expected_behavior
actual_behavior
first_failing_stage
classification
root_cause
files_changed
tests_added
focused_test_result
negative_test_result
required_regression_result
commit_hash
pull_request
deploy_receipt
runtime_commit
acceptance_replay_result
reconciliation_status
governance_invariants
unresolved_risks
can_execute=false
```

## Acceptance Tests

1. Scorer timeout cannot be rewritten as MODEL_UNAVAILABLE.
2. Missing fitted artifact remains MODEL_UNAVAILABLE.
3. Required Action not invoked is host orchestration failure, not model failure.
4. Transport failure after an invocation records scoring_attempted=true.
5. Sporting probability survives unrelated market evidence failure.
6. Correct started-event pregame block returns NOT_A_BUG.
7. Silent row disappearance causes P1 and blocks closure.
8. A repair adds a regression test or documents why the defect is configuration-only.
9. A semantics-changing repair adds a negative regression test.
10. Required CI failure prevents FIXED_VERIFIED.
11. Deploy receipt without runtime commit verification remains DEPLOYED_UNVERIFIED.
12. Runtime commit without exact acceptance replay remains DEPLOYED_UNVERIFIED.
13. Repeated equivalent defect becomes REGRESSION_REOPENED.
14. Supervisor cannot alter sporting probability merely to resolve an incident.
15. Supervisor cannot override V17_TERMINAL_REDUCER.
16. `can_execute=false` remains invariant.

## Definition of Done

The patch is operational only when:

```text
skill artifact exists
WOW V17 live instructions include mandatory trigger/routing
approved engineering tools are available for code changes
deployment tooling is connected/configured where autonomous deploy is expected
at least one end-to-end acceptance incident proves detect→repair→deploy→replay
can_execute=false
```

## One-Line Definition

**WOW-PATCH-2026-09-03-V17-AUTONOMOUS-PRODUCT-QA-ENGINEERING-RECOVERY creates one mandatory V17 reliability supervisor that owns software failures end-to-end while preserving specialist probability ownership, strict terminal semantics, protected deployment governance, and dry-run-only betting safety.**
