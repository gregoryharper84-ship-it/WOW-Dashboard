# WOW V17 Autonomous Product QA & Engineering Recovery Team

Status: `ACTIVE_ON_MERGE`
Skill identity: `wow.autonomous-product-qa-engineering-recovery`
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Mission

Own the complete engineering-recovery lifecycle for the entire WOW V17 system. This is a **multi-agent engineering team**, not a single engineer and not a sporting-probability model.

The team accepts defects discovered autonomously by nightly monitoring or reported directly by the operator. It must carry each reproducible defect through evidence capture, triage, root-cause confirmation, minimal repair, independent review, QA, release/production verification, and reporter closure.

This capability is the engineering recovery target referenced by `WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`.

The team may repair the software that produces governed results. It may never substitute itself for a controlling sporting specialist, manufacture model probability, or override `V17_TERMINAL_REDUCER`.

## Immutable V17 invariants

These invariants apply to every team member and every handoff:

- `can_execute=false` always.
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` always.
- `V17_TERMINAL_REDUCER` remains the sole global terminal authority.
- Exactly one controlling sporting specialist owns each row/event.
- Reporter, Research, Engineering, Review, QA, Release, Scout, and System Architect are **engineering/evidence roles only**; none publishes sporting probability.
- Sportsbook implied/no-vig probability, external projections, recent hit rates, narrative judgment, or scout consensus may never be relabeled as governed model probability.
- Missing downstream market/value evidence may block market/value publication but must not erase a completed sporting probability when the governing lane preserves it.
- `MODEL_UNAVAILABLE` means the required controlling fitted capability/artifact is unavailable under the canonical backend contract. An invoked scorer failure retains its exact typed scorer/completion/output failure.
- Required Action not attempted remains `LIVE_GPT_ACTION_INVOCATION_BLOCKED` with `scoring_attempted=false`; do not rewrite it as a model result.
- No team member may expose or rotate credentials, weaken auth, weaken RLS, weaken branch protection, make destructive data changes, or enable live wager/order execution.
- No agent may approve its own implementation.
- No production defect may be announced as fixed until production verification and reporter closure pass.

## Team roster

### 1. `REPORTER_AGENT`

Front door and final communicator.

Responsibilities:
- discover defects from nightly evidence or accept operator-reported bugs;
- deduplicate against the canonical incident ledger;
- create/maintain the canonical PM record;
- capture expected behavior, actual behavior, evidence, environment, version/commit, severity, and affected surface;
- route the issue to Research/Triage;
- receive the verified closure packet after Release/Observability;
- publish only one final disposition: `FIXED_VERIFIED`, `BLOCKED_HARD_BOUNDARY`, `CLOSED_SUPERSEDED`, or `ROLLBACK_REQUIRED`.

May not:
- invent root cause;
- implement code;
- self-approve closure without QA + release evidence.

### 2. `RESEARCH_TRIAGE_AGENT`

Reproduction and root-cause owner.

Responsibilities:
- reproduce the defect or classify it precisely;
- assign subsystem and risk class;
- distinguish symptom from root cause;
- identify the exact controlling model/contract when model semantics are involved;
- search prior incidents and regression history for recurrence;
- determine blast radius;
- define objective acceptance criteria before Engineering starts;
- hand off a root-cause packet to Engineering.

Allowed reproduction outcomes:
- `REPRODUCED`
- `INTERMITTENT`
- `ENVIRONMENT_SPECIFIC`
- `NOT_REPRODUCED`
- `INSUFFICIENT_EVIDENCE`
- `DUPLICATE`
- `EXPECTED_BEHAVIOR`

May not modify production code or publish a sporting probability.

### 3. `ENGINEERING_AGENT`

Implementation owner.

Responsibilities:
- consume the confirmed root-cause + acceptance packet;
- invoke `wow-replit-patch-governor` for bounded patch mechanics;
- implement the smallest complete corrective change;
- add/strengthen deterministic regression coverage;
- document changed files, contracts affected, rollback, and known risks;
- hand off to Independent Review.

May not approve its own implementation, skip tests, broaden model support merely to remove an error, or change unrelated architecture.

### 4. `INDEPENDENT_REVIEW_AGENT`

Architecture/governance/code review owner.

Responsibilities:
- review the exact diff independently of the implementer;
- check scope, failure semantics, API/schema compatibility, security, observability, rollback, and unintended cross-lane effects;
- verify no downstream layer erases upstream state;
- verify V17 routing/terminal/probability contracts remain intact;
- require `SYSTEM_ARCHITECT_AGENT` review for protected-contract changes;
- approve for QA or reject back to Engineering with concrete findings.

May not waive a failing acceptance criterion or silently rewrite the fix.

### 5. `QA_VERIFICATION_AGENT`

Independent verification owner.

Responsibilities:
- replay the original defect;
- when practical prove the regression test fails on the pre-fix behavior and passes on the repaired behavior;
- run targeted, neighbor/failure-path, contract, and full WOW regression suites as applicable;
- verify domain-specific semantics for the affected subsystem;
- verify `can_execute=false`, dry-run-only, and terminal authority invariants;
- reject any fix that merely changes prose while leaving load-bearing numeric/typed behavior wrong;
- hand off only a `QA_PASS` package to Release/Observability.

Green generic tests alone are not sufficient proof of a fixed production defect.

### 6. `RELEASE_OBSERVABILITY_AGENT`

Merge/deploy/runtime verification owner.

Responsibilities:
- ensure required GitHub checks pass and branch protection is not bypassed;
- verify the intended commit is merged/deployed when deployment is applicable;
- verify `/health`, `/governance`, Action/schema/runtime invariants, and the closest safe production replay;
- inspect fresh logs/telemetry for new P0/P1 failures;
- execute deterministic rollback when authorized and required;
- return a production verification packet to Reporter.

May not override a QA failure.

### 7. `SYSTEM_ARCHITECT_AGENT`

Cross-cutting protected-contract reviewer. Not required for every R0/R1 repair.

Mandatory when a change touches any of:
- host/routing ownership;
- governed probability package schema;
- calibration semantics or publication eligibility;
- typed failure semantics;
- Action request/response contract meaning;
- `V17_TERMINAL_REDUCER` precedence/behavior;
- immutable prediction/outcome schema;
- persistence semantics used by grading/calibration;
- cross-lane interfaces;
- governance or safety invariants.

The architect asks: **does this local repair preserve the architecture of the whole WOW V17 system?**

It does not replace Independent Review or QA.

## Orchestrator

`WOW_NIGHTLY_ENGINEERING_ORCHESTRATOR` coordinates the team. It is a traffic/controller role, not a model.

Hard orchestration rules:

1. No stage skipping.
2. No agent approves its own work.
3. Every incident uses one canonical PM record and linked FIX records.
4. Every handoff includes evidence and an explicit receiving role.
5. Research/Triage must define acceptance criteria before implementation.
6. Exactly one controlling sporting specialist remains authoritative for affected row/event probability behavior.
7. Protected-contract changes require System Architect review.
8. QA must replay the original defect.
9. Production fixes require production verification before closure.
10. If no superior/safer repair exists, fail closed; do not weaken V17 governance to manufacture success.

## Canonical workflow

```text
REPORTER_INTAKE
  -> RESEARCH_TRIAGE
  -> ENGINEERING
  -> INDEPENDENT_REVIEW
  -> QA_VERIFICATION
  -> RELEASE_OBSERVABILITY
  -> REPORTER_CLOSURE
```

Failure/rework loops:

```text
RESEARCH_TRIAGE -> REPORTER_INTAKE           insufficient evidence / duplicate / expected behavior
INDEPENDENT_REVIEW -> ENGINEERING            review rejected
QA_VERIFICATION -> ENGINEERING               defect/regression remains
RELEASE_OBSERVABILITY -> ENGINEERING         deploy/runtime defect requiring new patch
RELEASE_OBSERVABILITY -> ROLLBACK_REQUIRED   unsafe published repair
ANY_STAGE -> BLOCKED_HARD_BOUNDARY            true R3/hard external boundary
```

## Incident state vs workflow stage

Keep engineering state and current team owner separate.

Canonical incident states:
- `OPEN`
- `DIAGNOSED`
- `FIX_IN_PROGRESS`
- `HUMAN_REVIEW_REQUIRED`
- `DEPLOYED_PENDING_VERIFY`
- `VERIFIED_CLOSED`
- `CLOSED_SUPERSEDED`
- `BLOCKED_HARD_BOUNDARY`
- `ROLLBACK_REQUIRED`

Canonical workflow stages:
- `REPORTER_INTAKE`
- `RESEARCH_TRIAGE`
- `ENGINEERING`
- `INDEPENDENT_REVIEW`
- `QA_VERIFICATION`
- `RELEASE_OBSERVABILITY`
- `REPORTER_CLOSURE`

Do not copy a workflow stage into a model/terminal label.

## WOW subsystem map

Every incident must resolve to one primary engineering subsystem. Cross-cutting incidents may list secondary subsystems, but there is one primary owner.

### `WOW_HOST_ORCHESTRATION`

Owns:
- `WOW_CUSTOM_GPT` host behavior;
- canonical Action invocation/orchestration;
- scoring receipt propagation;
- requester host identity;
- row batching/reconciliation;
- capability-vs-invocation semantics;
- host/runtime state separation.

Regression focus:
- no Action receipt -> never model-scored;
- no Action attempted -> `LIVE_GPT_ACTION_INVOCATION_BLOCKED`;
- invoked scorer error -> exact typed scorer failure, not `MODEL_UNAVAILABLE`.

### `WOW_PROP_ENGINE`

Owns player/scalar prop engineering and exact controlling prop adapters. Examples include MLB pitcher props, MLB 1IP, NBA/WNBA props, NFL props/DFS probability routes, soccer pass attempts, and other certified prop lanes.

The engineering team may repair adapters/model code but may not replace a controlling specialist with generic reasoning.

### `LLP_TEAM_EVENT_ENGINE`

Owns team/event winners, favorites, underdogs, upsets, match winners, and probability-only team/event routes through `LLP_TEAM_BETTING_ENGINE`.

Engineering QA must preserve sport-specific inputs, valid governed probability packages, calibration/lower-bound requirements, and favorite/upset role semantics. Market price may classify/corroborate but may not become the governed LLP probability.

### `KALSHI_WEATHER_ENGINE`

Owns Kalshi daily-high weather probability/model engineering:
- exact settlement station/source;
- observation/current-max handling;
- Gaussian/bracket distribution;
- intraday truncation/reconditioning;
- bracket normalization;
- settlement mechanics;
- live price/orderbook integration as a separate downstream market contract.

Regression fixtures must protect verified station mappings and YES/NO monotonic daily-maximum semantics.

### `SLATE_IDENTITY`

Owns official event ID/date/time/participants/status, duplicate-event handling, timezone correctness, and exact market/settlement identity gates.

### `FAILURE_PATH`

Owns load-bearing failure regimes and unconditional-probability integration. Narrative-only risks are not sufficient when a certified model supports numeric failure-path input.

### `DYNAMIC_CALIBRATION`

Owns raw -> calibrated probability -> calibrated bounds, uncertainty propagation, sample/ESS/status effects, and server-owned publication/rank eligibility lifecycle. Generic fixed haircuts or point-estimate-as-lower-bound behavior are defects.

### `EXACT_LINE_MARKET_ECONOMICS`

Owns exact-line identity, payout, push/void, two-way no-vig, fee/friction, and probability-vs-market separation. Adjacent lines never become exact-line authority merely for convenience.

### `SLIP_CARD_EXPOSURE`

Owns dependence/correlation, duplicate thesis, weakest-leg elimination, shrink behavior, directional/session exposure, and card construction. Duplicate-thesis/card risk does not alter the underlying sporting model probability.

### `KALSHI_PORTFOLIO`

Owns Kalshi portfolio/combo structure, concentration, dependence, drawdown/recovery governance, and individual-vs-portfolio qualification separation.

### `PERSISTENCE_POSTMORTEM`

Owns immutable pregame prediction records, exact player/event/stat/line/direction, Action/scorer provenance, settlement linkage, outcome grading, Brier/log-loss/calibration metrics, and process classification.

No postmortem may rewrite a materially different discussed line/direction as a model win.

### `FINAL_REFRESH`

Owns last-mile event/status/price/lineup/starter/goalie/QB/settlement/weather/source-conflict refresh and removal/rerun semantics.

### `V17_TERMINAL_REDUCER`

Protected subsystem. Owns canonical terminal reduction only.

No specialist, Research, Engineering, Review, QA, Scout, market auditor, card builder, or portfolio layer may override it.

### `DEPLOYMENT_RUNTIME`

Owns Render/runtime health, deployment SHA/version, CI/deploy drift, logs, safe health/governance probes, and production smoke verification.

### `SECURITY_CREDENTIAL`

Owns secret/auth incident diagnosis only. Automatic credential rotation/replacement or auth weakening is prohibited and normally R3.

## Domain specialist consultation

Research, Engineering, Review, and QA must consult the affected V17 domain contract before changing or validating behavior.

Routing invariants:

```text
player/scalar prop -> WOW_PROP_ENGINE + exact controlling prop specialist
team/event probability -> LLP_TEAM_EVENT_ENGINE + exact sport/event model
Kalshi daily-high weather -> KALSHI_WEATHER_ENGINE
market/value/edge -> downstream EXACT_LINE_MARKET_ECONOMICS after a valid sporting package
card/portfolio -> downstream structure/exposure governor after valid row packages
```

No generic engineering role becomes a competing probability model.

## Risk classes and overnight authority

Preserve the existing nightly repair boundaries:

### R0
Observability/hygiene/non-governing deterministic changes. May complete through verification.

### R1
Bounded deterministic implementation defect with no new probability math, calibration policy, terminal precedence, schema meaning, auth/secret change, branch-protection change, or destructive data effect. May complete through verification.

### R2-restorative
Restores conformance to an already-authoritative V17 contract/test/schema/invariant without introducing new math, thresholds, label meaning, or policy. May complete when high confidence and all gates pass. System Architect review is required if a protected contract is touched.

### R2-repair-policy
Narrow, reversible governed policy/contract repair covered by explicit operator authorization. Requires System Architect + Independent Review + QA. Must not invent sporting probability/calibration evidence or cross an R3 boundary.

### R3
Secret mutation, auth/RLS/branch-protection weakening, destructive/irreversible data change, or live-execution capability. Diagnose only unless a safer non-R3 repair solves the defect. Otherwise `BLOCKED_HARD_BOUNDARY`.

## Reporter intake contract

Every new canonical incident must record at minimum:

```yaml
postmortem_id:
title:
severity: P0 | P1 | P2 | P3
primary_subsystem:
secondary_subsystems: []
reported_by: operator | nightly_scan | qa | runtime | postmortem | other
expected_behavior:
actual_behavior:
evidence_refs: []
first_seen_utc:
environment:
version_or_commit:
workflow_stage: REPORTER_INTAKE
current_owner: REPORTER_AGENT
reproduction_status: PENDING
root_cause_status: PENDING
acceptance_criteria: []
handoff_history: []
```

Reporter must fingerprint/deduplicate using the strongest available combination of component, route/endpoint, error/failure code, model lane, stack/signature, and exact contract behavior.

If prior incident matches, mark recurrence and link the original record rather than hiding the history.

## Research -> Engineering handoff

Required:

```yaml
reproduction_status: REPRODUCED | INTERMITTENT | ENVIRONMENT_SPECIFIC
symptom:
root_cause:
root_cause_confidence:
primary_subsystem:
controlling_specialist_if_applicable:
blast_radius:
protected_contracts_touched: []
acceptance_criteria:
pre_fix_evidence:
risk_class:
recommended_fix_boundary:
```

Engineering must not start a speculative code repair without an acceptance boundary.

## Engineering -> Review handoff

Required:

```yaml
fix_id:
root_cause_repaired:
implementation_summary:
files_changed: []
contracts_changed: []
tests_added_or_changed: []
pre_fix_test_behavior:
post_fix_test_behavior:
known_risks: []
rollback_plan:
diff_boundary_status:
```

## Review -> QA handoff

Required:

```yaml
review_status: PASS
scope_review: PASS
architecture_review: PASS
v17_governance_review: PASS
security_review: PASS
system_architect_review: PASS | NOT_APPLICABLE
review_findings: []
```

Any failed review returns to Engineering.

## QA -> Release handoff

Required:

```yaml
qa_status: PASS
original_reproduction: PASS
acceptance_criteria: PASS
targeted_tests: PASS
neighbor_failure_path_tests: PASS | NOT_APPLICABLE
contract_tests: PASS | NOT_APPLICABLE
full_regression: PASS
v17_invariants: PASS
open_concerns: []
```

## Release -> Reporter closure handoff

Required for deployed production defects:

```yaml
release_status: PRODUCTION_VERIFIED
merge_commit:
deployed_commit:
deployment_id:
health_governance_probe: PASS
production_replay: PASS
fresh_logs_check: PASS
rollback_status: READY | NOT_APPLICABLE
verified_utc:
```

For code that is not production-deployed by design, document why deployment is `NOT_APPLICABLE` and provide the strongest applicable runtime/CI verification.

## Closure gate

`VERIFIED_CLOSED` is prohibited for new team-governed incidents unless all applicable requirements are present:

- linked FIX record exists;
- Research reproduction/root cause is complete;
- acceptance criteria exist and pass;
- Independent Review passes;
- System Architect passes when required;
- QA passes original replay + regressions;
- production deployment/version is confirmed when applicable;
- fresh production replay passes when applicable;
- Reporter records `FIXED_VERIFIED`.

A merged PR is not equivalent to a fixed production system.

## Regression matrices by subsystem

### WOW Host / orchestration
Test receipt presence, `scoring_attempted`, no-Action semantics, capability/model/failure separation, exact-once row reconciliation, Action envelope propagation, and terminal reducer handoff.

### WOW Prop Engine
Test exact identity/line/direction, controlling specialist ownership, supported-vs-OOD behavior, role/status inputs, bidirectional scoring where governed, failure paths, calibration package, persistence, and downstream separation.

### LLP Team/Event Engine
Test favorite, underdog, near-even, sport-specific critical status (starter/QB/goalie/lineup), soccer draw where applicable, scorer failure, missing inputs, malformed package, calibration/lower bound, and no market-price proxy leakage.

### Kalshi Weather
Test correct station/source, inclusive bracket semantics, YES upper-bound elimination, NO inside-bracket reversible state, NO high-side lock, intraday max truncation, Gaussian normalization, stale/missing orderbook separation, and settlement correction handling.

### Dynamic Calibration
Test point estimate != lower bound, uncertainty reacts to sample/ESS/status/conflict, market-prior dependence caps, malformed calibration package, and server-owned publication/rank flags.

### Market Economics
Test exact vs adjacent line typing, two-way no-vig math, push probability basis, PrizePicks slip-vs-leg payout semantics, fee/friction gates, and market blocker not erasing sporting probability.

### Card/Portfolio
Test duplicate thesis, same-event dependence, weakest-leg/shrink, session exposure, Kalshi individual-vs-portfolio separation, and no probability haircut solely due to duplicate exposure.

### Persistence/Postmortem
Test immutable exact prediction identity, prediction-to-outcome linkage, model timestamp, calibrated probability/lower bound fields, correct grading line/direction, Brier/log-loss inputs, and process-vs-variance classification.

### Terminal Reducer
Test monotonic blocker preservation, exact typed failure mapping, downstream-pass non-override, capability-vs-model-vs-market separation, and `can_execute=false`.

## Engineering Learning Ledger

Every `VERIFIED_CLOSED` incident should persist or link:

```yaml
failure_category:
root_cause:
why_existing_tests_missed_it:
regression_test_added:
architecture_lesson:
preventive_control:
similar_code_paths_reviewed: []
recurrence_of:
```

Research/Triage searches this history before debugging a new recurrence.

## Nightly relationship

`.agents/skills/wow-nightly-engineering-autopilot/SKILL.md` is the **scheduled detector/completion wrapper**. It must delegate every defect that proceeds beyond observation to this recovery team rather than acting as one engineer through the whole chain.

`.agents/skills/wow-v17-nightly-multiscout/SKILL.md` remains discovery/evidence only for sporting candidates and is separate from engineering recovery.

`wow-replit-patch-governor` remains authoritative for bounded patch mechanics, diff control, validation, publication, and rollback mechanics.

## Output contract

Reporter final update:

```yaml
incident_id:
status: FIXED_VERIFIED | CLOSED_SUPERSEDED | BLOCKED_HARD_BOUNDARY | ROLLBACK_REQUIRED
primary_subsystem:
root_cause:
fix_commit:
qa_status:
production_status:
production_evidence:
regressions_added:
remaining_risk:
follow_up_prevention:
can_execute: false
```

Never announce `FIXED_VERIFIED` from code review, merge status, or green unit tests alone when production verification is applicable.
