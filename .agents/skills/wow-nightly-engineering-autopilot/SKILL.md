# WOW V17 Nightly Engineering Autopilot — Morning-Green Team Wrapper

Status: `ACTIVE_ON_MERGE`
Identity: `WOW_V17_NIGHTLY_ENGINEERING_AUTOPILOT`
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Purpose

This skill is the scheduled/manual **nightly repair-and-verification orchestrator** for WOW V17. Its job is not merely to discover, classify, or report defects. Its primary overnight objective is to leave every safely repairable incident `FIXED_VERIFIED` before the morning handoff.

For every defect that proceeds beyond observation, load and delegate through:

```text
wow.autonomous-product-qa-engineering-recovery
```

The independent lifecycle remains mandatory:

```text
REPORTER_AGENT
  -> RESEARCH_TRIAGE_AGENT
  -> ENGINEERING_AGENT
  -> INDEPENDENT_REVIEW_AGENT
  -> QA_VERIFICATION_AGENT
  -> RELEASE_OBSERVABILITY_AGENT
  -> REPORTER_AGENT closure
```

`SYSTEM_ARCHITECT_AGENT` is mandatory for protected cross-cutting contracts.

`wow-replit-patch-governor` remains the compatibility name for the bounded patch-governor process, but nightly execution is GitHub + CI + Render + Supabase aware. Replit-specific instructions are non-authoritative unless the repository/runtime actually uses Replit.

## Morning-Green Contract

A nightly run is successful only when every reproduced incident is in one of these states:

1. `FIXED_VERIFIED` — repaired, required checks green, exact deployed build confirmed when production-facing, and original/equivalent acceptance replay passed;
2. `BLOCKED_HARD_BOUNDARY` — a true hard boundary is proven with exact missing dependency/authority;
3. `CLOSED_SUPERSEDED` — another verified repair fully resolves the same root cause;
4. `ROLLBACK_REQUIRED` — a published repair failed production verification and deterministic rollback cannot safely complete in the current tool surface.

`PR_OPEN`, `CI_GREEN`, `MERGED`, `DEPLOYED`, `PATCH_WRITTEN`, `ROOT_CAUSE_ISOLATED`, `TESTS_PASS_LOCAL`, `CI_PENDING`, and `CI_FAILED` are intermediate states, never nightly success states.

If any safely repairable R0/R1 incident remains in an intermediate state at handoff, the entire run MUST be labeled:

```text
INCOMPLETE_ENGINEERING_RUN
```

It must not be reported as healthy, repaired, or successfully completed.

### Morning Repair Rate

Track:

```text
morning_repair_rate = safely_repairable_incidents_fixed_verified / safely_repairable_incidents_reproduced
```

Also emit the top-line completion score on every run:

```text
R0_R1_OPEN_AT_START / FIXED_VERIFIED_THIS_RUN / HARD_BLOCKED / UNFINISHED
```

The overnight target is `UNFINISHED=0` and `morning_repair_rate=1.00` for safely repairable work. Any safely repairable incident left in an intermediate state must be treated as unfinished work, not as a report-only outcome.

## Machine-readable Morning-Green PR scope

Every PR requesting unattended Morning-Green merge MUST include exactly one fenced scope block:

```morning-green-scope
risk_class: R1
allowed_files:
  - exact/repository/path.py
protected_files: []
```

Rules:

- `risk_class` must exactly match the `Morning-Green-Risk:` marker.
- `allowed_files` contains exact repository-relative paths only; globs are prohibited.
- every file in the current PR diff must be declared in `allowed_files`.
- a path declared in `protected_files`, or matching the repository's central Morning-Green protected-path manifest, is never eligible for unattended merge.
- the workflow must compare the live PR head SHA with the exact SHA whose required checks were verified.
- workflow changes fail closed when their diff cannot be inspected.
- workflow changes that add write/merge privilege tokens are not eligible for unattended merge.
- scope verification is an additional gate; it never replaces protected CI, independent review, QA, branch protection, or V17 governance.

The machine-readable scope is derived from the patch governor's bounded build packet. It does not create new probability/model authority.

## Mission

Every night:

- inspect the live WOW V17 system and repository;
- load unresolved prior incidents before opening new work;
- reproduce defects from strongest current evidence;
- repair every R0, R1, and eligible R2-restorative defect autonomously;
- iterate through CI/review/QA failures instead of stopping at the first failure;
- merge through protected checks when authorized and permitted;
- deploy through the repository's actual production platform when policy permits;
- replay production acceptance against the exact deployed SHA;
- continue rework loops until `FIXED_VERIFIED` or a true hard boundary is proven.

The wrapper coordinates independent engineering roles. It must not collapse review/QA independence, but independence does not mean waiting for the operator between stages.

## System scope

Nightly engineering covers the entire governed WOW stack, including:

- `WOW_HOST_ORCHESTRATION` / WOW Betting Engine host;
- `WOW_PROP_ENGINE` and exact controlling prop specialists;
- `LLP_TEAM_EVENT_ENGINE` / LLP Team Betting Engine;
- `KALSHI_WEATHER_ENGINE`;
- `SLATE_IDENTITY`;
- `FAILURE_PATH`;
- `DYNAMIC_CALIBRATION`;
- `EXACT_LINE_MARKET_ECONOMICS`;
- `SLIP_CARD_EXPOSURE`;
- `KALSHI_PORTFOLIO`;
- `PERSISTENCE_POSTMORTEM`;
- `FINAL_REFRESH`;
- protected `V17_TERMINAL_REDUCER`;
- `DEPLOYMENT_RUNTIME`;
- `SECURITY_CREDENTIAL` diagnostics.

## Immutable V17 invariants

No nightly-completion authority may weaken:

- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`;
- `V17_TERMINAL_REDUCER` sole global terminal authority;
- exactly-one controlling sporting specialist per row/event;
- fail-closed missing/contradictory evidence semantics;
- exact typed model/input/scorer/output/host-invocation failures;
- probability-vs-market-vs-money-vs-portfolio separation;
- exact-line/OOD rules;
- immutable pregame grading identity;
- credential/RLS/branch-protection safety;
- prohibition on destructive/irreversible mutation and live wager/order execution.

Reporter/Research/Engineering/Review/QA/Release are engineering roles only. None may publish a competing sporting probability.

## Nightly discovery sequence

Before creating or reopening incidents, establish current truth from the strongest available evidence:

1. unresolved incidents from the prior nightly ledger, ordered R0 then R1 then production/acceptance blockers;
2. latest `main` SHA and required repository checks;
3. relevant open/recent PR state;
4. latest CI/workflow failures;
5. Render/service deployment state where applicable;
6. fresh production health/governance probes;
7. safe acceptance/runtime traces;
8. Action/schema/persistence/reconciliation anomalies;
9. current instructions/OpenAPI/runtime/test contract drift;
10. prior PM/FIX records and recurring failure fingerprints;
11. subsystem-specific regression signals for WOW Props, LLP Team/Event, and Kalshi Weather.

Never assume yesterday's diagnosis is still current. An unresolved incident does not leave the active queue merely because a patch or PR exists.

## Queue rules

For each finding:

- deduplicate/identify recurrence;
- create/update one canonical PM record;
- record P0/P1/P2/P3 severity;
- record primary engineering subsystem;
- preserve current evidence/version/timestamp;
- set `workflow_stage=REPORTER_INTAKE` for a new team-governed incident;
- hand off immediately to `RESEARCH_TRIAGE_AGENT`;
- once reproduced and safely repairable, continue through the complete repair loop without an operator checkpoint.

Work order is authoritative:

1. unresolved R0 incidents;
2. unresolved R1 incidents;
3. production regressions;
4. acceptance failures;
5. new R0/R1 discoveries;
6. R2/R3 diagnosis and safe PR preparation;
7. lower-severity discoveries.

A discovery signal is not a root cause and is not a model result.

## Overnight repair authority

### R0 — autonomous completion

Observability, hygiene, documentation, non-governing deterministic changes, workflow fixes, import/module execution fixes, and equivalent low-risk defects.

May autonomously patch, review, QA, merge, deploy when applicable, production-verify, and close.

### R1 — autonomous completion

Bounded deterministic implementation defects with no new probability math, calibration/qualification policy, terminal precedence, schema meaning, auth/secret change, branch-protection change, destructive data effect, or live execution capability.

Examples include pagination, bounded query chunking, reconciliation, deterministic persistence handoff, import/runtime wiring, non-semantic error propagation, CI/deployment wiring, and observability.

May autonomously patch, review, QA, merge, deploy when applicable, production-verify, and close.

### R2-restorative — autonomous when reversible and authority-preserving

A repair that restores conformance to an already-authoritative V17 contract/test/schema/invariant without introducing new sporting math, thresholds, model certification, terminal authority, security policy, or destructive semantics.

Examples include:

- restoring an already-defined Action field or response shape without changing its meaning;
- adding truthful health/readiness exposure;
- restoring typed failure semantics already defined by V17;
- registry enumeration that remains `MODEL_UNAVAILABLE` until a real specialist is certified;
- deterministic evidence/receipt propagation required by an existing contract;
- repairing a post-model bridge so it consumes already-valid probability without altering it.

Requires System Architect review when a protected contract is touched, plus Independent Review and QA. It may proceed without an operator checkpoint when all changes are reversible and no hard-boundary condition is crossed.

### R2-repair-policy — explicit authorization required

A narrow governed policy change that changes contract meaning, publication policy, eligibility semantics, or another operator-owned rule while remaining reversible. Diagnose and prepare a tested PR unless the operator has explicitly authorized that exact policy repair.

### R3 / HARD_BOUNDARY — no autonomous mutation

The following are hard boundaries unless separately and explicitly authorized:

- training, certifying, promoting, or replacing a production fitted sporting model;
- changing sporting probability formulas, calibration math, thresholds, lower-bound policy, or qualification criteria;
- changing `V17_TERMINAL_REDUCER` authority or terminal precedence;
- enabling or broadening `can_execute` or any live wager/order capability;
- creating/replacing/rotating secrets or external credentials;
- weakening auth, RLS, branch protection, required checks, or governance controls;
- destructive/irreversible schema or data changes;
- paid infrastructure creation or materially higher recurring spend;
- synthetic/test artifact promotion to production authority.

If no safe non-R3 repair exists, mark `BLOCKED_HARD_BOUNDARY` with the exact missing dependency or authorization.

## Permitted hard-stop conditions

R0/R1 work may stop before `FIXED_VERIFIED` only when at least one of these conditions is proven:

1. required GitHub/CI/Render/Supabase engineering capability is unavailable;
2. required repository/service permission is unavailable;
3. the next repair necessarily crosses into R2-repair-policy or R3;
4. required auth/secret material is unavailable and cannot safely be created or rotated;
5. production verification failed and deterministic rollback is unavailable;
6. an external infrastructure outage prevents verification after alternate safe evidence paths are attempted;
7. a required fitted specialist/model artifact genuinely does not exist.

Every hard stop MUST record:

```text
STOP_REASON
FIRST_BLOCKED_OPERATION
EXACT_ERROR_OR_STATUS
MISSING_CAPABILITY_OR_AUTHORITY
WORK_COMPLETED_BEFORE_BLOCK
SMALLEST_NEXT_ACTION
```

`CI_FAILED`, `CI_PENDING`, `PR_OPEN`, `PATCH_CREATED`, `ROOT_CAUSE_ISOLATED`, or elapsed nightly time are not valid hard-stop reasons by themselves.

## Mandatory repair/rework loop

For each reproduced R0/R1/eligible R2-restorative incident, the orchestrator must continue this loop:

```text
REPRODUCE
 -> ROOT_CAUSE_CONFIRMED
 -> BOUNDED_PATCH
 -> TARGETED_TESTS
 -> INDEPENDENT_REVIEW
 -> REQUIRED_CI
 -> if failure: inspect exact failure -> ENGINEERING rework -> rerun
 -> MERGE through protected checks
 -> DEPLOY exact intended SHA when production-facing
 -> PRODUCTION_REPLAY
 -> if failure: isolate production-only cause -> ENGINEERING rework or deterministic rollback
 -> FIXED_VERIFIED
```

Rules:

- A CI failure is engineering input, not a nightly stopping condition.
- A review rejection is engineering input, not a nightly stopping condition.
- A QA failure is engineering input, not a nightly stopping condition.
- A production acceptance failure is engineering input unless it proves a hard boundary or requires rollback.
- When required CI fails, retrieve the exact failing job/test/error, classify patch-caused vs pre-existing vs infrastructure vs protected R2/R3 behavior, make the smallest bounded repair, and rerun targeted + required regression suites.
- `CI_PENDING` is not completion. Recheck protected checks in the same nightly run whenever the current tool surface permits.
- If CI cannot be rechecked because the capability is absent, report `BLOCKED_HARD_BOUNDARY` with `STOP_REASON=CI_RECHECK_CAPABILITY_UNAVAILABLE` and exact evidence.
- Do not close a run merely because a rerun is pending if the current tool surface allows polling/inspection and continued repair.
- Do not weaken a required check to make a repair green.
- Do not broaden scope opportunistically; split independent defects into separate bounded repair packets.
- Once an R0/R1 patch exists, closure work (test/review/CI/merge/deploy/replay) outranks discovery of additional non-critical defects.

## Resumable CI continuation contract

Safely autonomous repair PRs that are intended to continue without a live operator/session must include both exact body markers:

```text
Morning-Green-Autonomous: true
Morning-Green-Risk: R0
```

The risk value may be only `R0`, `R1`, or `R2-restorative`.

`.github/workflows/wow-v17-morning-green-continuation.yml` is the deterministic CI-resume layer. It wakes when either protected CI workflow completes, resolves only an open non-draft same-repository PR targeting `main`, requires the exact Morning-Green markers, and then independently re-checks all three protected checks on the PR head SHA:

- `WOW governed probability backend`
- `WOW required-three regression`
- `WOW additional required regression`

Behavior:

- if any required check is still pending/missing, exit without merge and rely on the later workflow completion event to resume;
- if any required check fails, do not merge and return the repair to Engineering/rework;
- only if all three required checks succeed on the exact PR head SHA may the continuation workflow request the protected merge;
- merge is pinned to the exact head SHA;
- GitHub branch protection remains authoritative and may still reject the merge;
- fork PRs, draft PRs, non-`main` PRs, missing markers, and non-autonomous risk classes are never eligible;
- this mechanism resumes CI-to-merge only. Production deploy/replay and `FIXED_VERIFIED` still require the applicable Release/Observability acceptance path.

Do not place the autonomous markers on R2-repair-policy or R3/hard-boundary changes.

## Deadline behavior

When run as an overnight shift, use three operational phases relative to the configured morning handoff deadline:

1. **Discovery + repair phase:** prioritize reproducing and repairing P0/P1/P2 safely repairable incidents.
2. **Closure phase:** stop opening non-critical new work and concentrate on CI, merge, deploy, production replay, and rework for active repairs.
3. **Morning handoff:** report only verified closures and true hard blockers; intermediate states are explicitly labeled unfinished.

If no deadline is configured, continue the same repair-first priority for the duration of the run and do not replace engineering completion with classification work.

## Completion rule

A production bug is not `VERIFIED_CLOSED` merely because:

- code was changed;
- a PR was opened;
- a PR was merged;
- unit tests are green;
- CI is green;
- Render reports a deployment live.

For new team-governed incidents, closure requires the machine-enforced recovery contract in:

```text
artifacts/wow-engine/v17/nightly_engineering_team_contract.py
artifacts/wow-engine/v17/nightly_incident_records.py
```

The required chain includes confirmed root cause, acceptance criteria, independent review, System Architect when applicable, QA, expected deployed version when applicable, fresh production verification when applicable, original/equivalent acceptance replay, and Reporter `FIXED_VERIFIED` closure.

## Production verification and rollback

`RELEASE_OBSERVABILITY_AGENT` owns production verification after QA pass. It must confirm:

- intended commit/version;
- exact deployed SHA/version;
- required health/governance invariants;
- original or closest safe replay;
- fresh logs/telemetry;
- `can_execute=false`.

If a published repair fails verification, deterministic authorized rollback is preferred. If rollback cannot be safely completed, use `ROLLBACK_REQUIRED` and stop dependent releases.

A transient observability-provider failure should use alternate safe evidence paths before concluding WOW itself failed.

## Incident ledger

Canonical records:

```text
artifacts/wow-engine/v17/incident-ledger.json
artifacts/wow-engine/v17/postmortems/
artifacts/wow-engine/v17/engineering-fixes/
```

New incidents use the multi-agent team contract. Historical records remain valid and are not retroactively rewritten.

Every unresolved incident must persist across nightly runs under a stable incident ID until `FIXED_VERIFIED`, `CLOSED_SUPERSEDED`, or an approved governance retirement. A PR number is linkage, not closure.

## Relationship to Multi-Scout

`.agents/skills/wow-v17-nightly-multiscout/SKILL.md` remains a sporting **discovery/evidence-only** team and runs separately. Scout agreement can raise research priority only; it cannot become sporting probability and does not replace this engineering recovery team.

## Mandatory unfinished-work record

Every incident that is not `FIXED_VERIFIED`, `CLOSED_SUPERSEDED`, or an approved hard-boundary/rollback terminal must appear in the nightly report with all of these fields:

```text
incident_id
severity
current_state
first_failing_stage
root_cause
last_action_attempted
last_test_result
PR
CI
merged_sha
deployed_sha
acceptance_replay
stop_reason
next_executable_action
```

No unresolved incident may disappear from the handoff because a patch was written, a PR was opened, CI started, or a deploy was triggered.

## Nightly output

```yaml
run_status: HEALTHY | REPAIRED_AND_VERIFIED | PARTIALLY_REPAIRED_HARD_BLOCKED | BLOCKED_HARD_BOUNDARY | ROLLBACK_REQUIRED | INCOMPLETE_ENGINEERING_RUN
utc_time:
main_commit:
production_deploy:
r0_r1_open_at_start:
fixed_verified_this_run:
hard_blocked:
unfinished:
r0_r1_completion_score: "R0_R1_OPEN_AT_START / FIXED_VERIFIED_THIS_RUN / HARD_BLOCKED / UNFINISHED"
reproduced_incidents:
safely_repairable_incidents:
fixed_verified_incidents:
morning_repair_rate:
incidents_by_subsystem:
incidents_by_owner:
verified_closed:
closed_superseded:
hard_blockers:
unfinished_intermediate_states:
  - incident_id:
    severity:
    current_state:
    first_failing_stage:
    root_cause:
    last_action_attempted:
    last_test_result:
    PR:
    CI:
    merged_sha:
    deployed_sha:
    acceptance_replay:
    stop_reason:
    next_executable_action:
next_priority:
can_execute: false
```

Do not report `REPAIRED_AND_VERIFIED` unless every safely repairable reproduced incident reached applicable production verification and Reporter closure.

If `unfinished > 0` for safely repairable R0/R1 work, `run_status` MUST equal `INCOMPLETE_ENGINEERING_RUN`.

## No-incomplete-stop execution rule

`INCOMPLETE_ENGINEERING_RUN` is a truthful handoff label only; it is NEVER an authorized stopping condition while a safe engineering action remains available in the current tool surface.

Before emitting `INCOMPLETE_ENGINEERING_RUN`, the orchestrator MUST exhaust the active R0/R1 repair loop in the same invocation:

1. inspect/recheck pending CI when polling is available;
2. retrieve and debug exact failures;
3. patch and retest the smallest safe fix;
4. merge the exact protected-green head when authorized;
5. deploy/reconcile the exact merged SHA;
6. replay production acceptance and repair any safely repairable acceptance failure;
7. repeat until `FIXED_VERIFIED` or a permitted hard-stop condition is proven.

Ordinary latency, a pending workflow, a failed test, an open PR, a deployment in progress, or an acceptance failure MUST NOT cause an early return. If a tool call returns an intermediate state and the same tool surface permits another safe inspection, retry, repair, merge, deploy, or verification action, perform that action before reporting.

If an invocation is forcibly ended by an external execution/runtime limit after the above continuation attempts, `INCOMPLETE_ENGINEERING_RUN` may be emitted only as a truthful non-success state with the exact next executable action. It must never be treated as closure, success, or permission to select new work on the next invocation.

## User-facing update format

Default user-facing engineering updates MUST be concise even when the durable internal engineering receipt remains detailed.

Use exactly this presentation unless the operator explicitly asks for the detailed engineering receipt:

```text
### <Issue Title>
Summary: <brief description of the issue>
What was done: <brief description of the fix, tests, merge/deploy/verification work, or exact work completed>
Status: COMPLETE | INCOMPLETE
```

Rules:

- `COMPLETE` means the parent incident is truthfully fixed and verified to the level applicable to that incident.
- `INCOMPLETE` must include the exact remaining blocker or next executable action in the `What was done` line or immediately after the status.
- Do not expose the full machine/durable receipt, defect contract, CI bookkeeping, SHA matrix, or governance checklist in routine operator updates unless requested.
- This is a presentation-layer change only. Durable incident records, CI evidence, exact-head verification, deployment verification, reconciliation evidence, and governance accounting remain mandatory internally.
- Never simplify away a real blocker, typed failure, or incomplete lane.

## Anti-report-only rule

For autonomous R0/R1 work, this outcome is prohibited whenever engineering capability remains available:

```text
Root cause isolated; PR created; CI failed/pending; continue next run.
```

Instead, continue engineering in the current run through rework, CI, merge, deployment, and acceptance replay. The nightly autopilot exists to close bounded engineering defects, not merely hand them to the next nightly run.

## Priority order

1. P0 security/governance/data-integrity failures
2. P1 broken production routes or invalid governed output
3. probability-package / terminal / Action / persistence contract failures
4. controlling-model or calibration regressions
5. acceptance blockers
6. CI/deployment drift
7. stale/superseded repair cleanup
8. observability/documentation drift

The nightly wrapper is expected to drive safe work to verified completion overnight. It may never collapse independent team roles, invent sporting authority, weaken required governance, or cross a hard boundary without explicit authorization.
