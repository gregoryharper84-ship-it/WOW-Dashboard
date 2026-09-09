# WOW V17 Nightly Engineering Autopilot — Team Wrapper

Status: `ACTIVE_ON_MERGE`
Identity: `WOW_V17_NIGHTLY_ENGINEERING_AUTOPILOT`
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Purpose

This skill remains the scheduled/manual **nightly detector and completion wrapper** for WOW V17. It is no longer a monolithic engineer that discovers, diagnoses, implements, approves, QA-verifies, deploys, and closes its own work.

For every defect that proceeds beyond observation, load and delegate to:

```text
wow.autonomous-product-qa-engineering-recovery
```

That capability owns the multi-agent lifecycle:

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

`wow-replit-patch-governor` remains authoritative for bounded implementation, diff control, regression testing, publication mechanics, rollback mechanics, and production verification mechanics.

## Mission

Every night, inspect the live WOW V17 system and repository for defects, regressions, contract drift, broken routes, failing tests, unhealthy deployments, stale assumptions, inconsistent persistence, invalid terminal semantics, model-package contract violations, or subsystem-specific regressions.

The wrapper creates/reconciles the defect queue and hands every actionable defect to the recovery team. It must not collapse the team back into a single self-approving agent.

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

1. latest `main` SHA and required repository checks;
2. relevant open/recent PR state;
3. latest CI/workflow failures;
4. Render/service deployment state where applicable;
5. fresh production health/governance probes;
6. safe acceptance/runtime traces;
7. Action/schema/persistence/reconciliation anomalies;
8. current instructions/OpenAPI/runtime/test contract drift;
9. prior PM/FIX records and recurring failure fingerprints;
10. subsystem-specific regression signals for WOW Props, LLP Team/Event, and Kalshi Weather.

Never assume yesterday's diagnosis is still current.

## Queue rules

For each finding:

- deduplicate/identify recurrence;
- create/update one canonical PM record;
- record P0/P1/P2/P3 severity;
- record primary engineering subsystem;
- preserve current evidence/version/timestamp;
- set `workflow_stage=REPORTER_INTAKE` for a new team-governed incident;
- hand off to `RESEARCH_TRIAGE_AGENT`.

A discovery signal is not a root cause and is not a model result.

## Overnight repair authority

The existing repair boundaries remain active, but work is executed through the independent team stages.

### R0
Observability/hygiene/non-governing deterministic change. Eligible to complete through team review, QA, release verification, and Reporter closure.

### R1
Bounded deterministic implementation defect with no new probability math, calibration policy, terminal precedence, schema meaning, auth/secret change, branch-protection change, or destructive data effect. Eligible to complete through the team.

### R2-restorative
Restores conformance to an already-authoritative V17 contract/test/schema/invariant without introducing new math, thresholds, label meaning, or policy. Eligible when high-confidence, reversible, fully tested, and System Architect-reviewed when a protected contract is touched.

### R2-repair-policy
Narrow reversible governed repair explicitly covered by operator authorization. Requires System Architect + Independent Review + QA and may not cross an R3 boundary or invent sporting probability/calibration evidence.

### R3
Secret rotation/replacement, auth/RLS/branch-protection weakening, destructive/irreversible data change, or live execution capability. Diagnose only unless a safer non-R3 repair solves the defect; otherwise `BLOCKED_HARD_BOUNDARY`.

## Completion rule

A production bug is not `VERIFIED_CLOSED` merely because:

- code was changed;
- a PR was opened;
- a PR was merged;
- unit tests are green;
- CI is green.

For new team-governed incidents, closure requires the machine-enforced recovery contract in:

```text
artifacts/wow-engine/v17/nightly_engineering_team_contract.py
artifacts/wow-engine/v17/nightly_incident_records.py
```

The required chain includes confirmed root cause, acceptance criteria, independent review, System Architect when applicable, QA, expected deployed version when applicable, fresh production verification when applicable, and Reporter `FIXED_VERIFIED` closure.

## Production verification and rollback

`RELEASE_OBSERVABILITY_AGENT` owns production verification after QA pass. It must confirm the intended commit/version, safe health/governance invariants, original/closest safe replay, and fresh logs/telemetry.

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

## Relationship to Multi-Scout

`.agents/skills/wow-v17-nightly-multiscout/SKILL.md` remains a sporting **discovery/evidence-only** team and runs separately. Scout agreement can raise research priority only; it cannot become sporting probability and does not replace this engineering recovery team.

## Nightly output

```yaml
run_status: HEALTHY | REPAIRED_AND_VERIFIED | BLOCKED_HARD_BOUNDARY | ROLLBACK_REQUIRED
utc_time:
main_commit:
production_deploy:
incidents_by_subsystem:
incidents_by_owner:
reporter_intake:
research_triage:
engineering:
independent_review:
qa_verification:
release_observability:
verified_closed:
closed_superseded:
hard_blockers:
unresolved: []
next_priority:
can_execute: false
```

Do not report `REPAIRED_AND_VERIFIED` unless every published repair in the run reached applicable production verification and Reporter closure.

## Priority order

1. P0 security/governance/data-integrity failures
2. P1 broken production routes or invalid governed output
3. probability-package / terminal / Action / persistence contract failures
4. controlling-model or calibration regressions
5. acceptance blockers
6. CI/deployment drift
7. stale/superseded repair cleanup
8. observability/documentation drift

The nightly wrapper may drive safe work overnight, but it may never collapse independent team roles or override a stricter V17 governing rule.
