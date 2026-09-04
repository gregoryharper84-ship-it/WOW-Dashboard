# WOW V17 Nightly Engineering Autopilot

Load this skill for every scheduled or manually triggered WOW V17 engineering-health run. It extends `.agents/skills/wow-replit-patch-governor/SKILL.md`; that skill remains authoritative for bounded implementation, diff control, regression testing, publication, and production verification.

## Mission

Every night, inspect the live WOW V17 system and repository for defects, regressions, contract drift, broken routes, failing tests, unhealthy deployments, stale assumptions, or inconsistent terminal semantics. When a defect is reproducible and safely bounded, diagnose it, implement the smallest complete repair, validate it, publish only when allowed by the risk policy below, and verify production with fresh evidence.

The goal is not to maximize code changes. The goal is to keep V17 correct, fail-closed, observable, and continuously acceptance-verified.

## Immutable V17 invariants

- `can_execute=false` is unconditional.
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` remains unconditional.
- `V17_TERMINAL_REDUCER` remains the sole terminal authority.
- Missing or contradictory evidence fails closed.
- A model that was invoked but failed must retain the typed scorer/output failure; do not rewrite it as `MODEL_UNAVAILABLE`.
- Market-price absence may block value publication, but must not erase a completed sporting probability when the governing lane preserves it.
- Never manufacture a probability, calibration value, model capability, exact-line support, or terminal label to make a run pass.
- Never expose or rotate secrets automatically.
- Never weaken GitHub branch protection or required checks.

## Nightly run sequence

### 1. Establish current truth

Inspect, at minimum:

1. latest `main` commit and open pull requests;
2. required GitHub checks and latest failures;
3. Render deployment state and recent error logs for `wow-governed-probability-engine` and related V17 services;
4. `/health` and `/governance` behavior where reachable;
5. recent V17 run failures, terminal states, or schema-validation errors visible in logs or CI;
6. contract drift among runtime code, `render.yaml`, V17 OpenAPI schemas, current V17 instructions, and tests;
7. known unresolved defects from recent issues/PRs.

Never assume yesterday's diagnosis is still current.

### 2. Classify findings

For each finding assign:

- `severity`: P0 / P1 / P2 / P3
- `domain`: runtime / action-contract / model-input / scoring / calibration / persistence / terminal-reducer / orchestration / CI / deployment / observability / documentation
- `reproducible`: true / false
- `root_cause_confidence`: high / medium / low
- `change_risk`: R0 / R1 / R2 / R3

Risk levels:

- **R0 — no-code/observability:** stale test fixture, logging, comments, diagnostics, typo, dead non-governing config. May auto-fix.
- **R1 — bounded implementation:** deterministic bug with a narrow fix, no probability math, no terminal precedence, no schema migration, no auth/secret change, no branch-protection change. May auto-fix and publish after all gates pass.
- **R2 — governed behavior:** probability model logic, calibration, terminal semantics, evidence precedence, identity rules, exact-line support, persistence semantics, Action request/response contracts, or cross-lane routing. May auto-diagnose and create a tested PR, but do not merge/deploy automatically unless a separately approved patch explicitly authorizes that exact change.
- **R3 — infrastructure/security/data-destructive:** secrets/auth, database destructive migration, RLS weakening, branch protection, external credentials, irreversible data correction, live execution capability. Diagnose only; never autonomously modify or deploy.

### 3. Reproduce before repair

No code change without a concrete failing signal. Acceptable reproductions include:

- failing deterministic test;
- failing CI job with traceable stack/error;
- fresh production request/log trace;
- schema validation failure;
- contract assertion mismatch;
- deterministic persisted-row inconsistency tied to a specific code path.

Narrative suspicion alone is not sufficient.

### 4. Build the patch contract

For every repair, instantiate the mandatory build packet from `wow-replit-patch-governor` before editing. Set `publish_authorized=true` only for R0/R1 changes that satisfy this skill's autonomous-publish policy.

### 5. Repair minimally

- Touch only declared `allowed_files`.
- Add or strengthen a regression test that fails before the repair whenever practical.
- Do not refactor unrelated code.
- Do not broaden model support merely to eliminate a failure.
- Preserve typed failure semantics.

### 6. Validation gates

A repair is not complete until all applicable gates pass:

1. targeted reproduction now passes;
2. relevant unit/integration tests pass;
3. required WOW regressions pass;
4. V17 OpenAPI/schema validation passes when applicable;
5. `can_execute=false` and dry-run invariants remain asserted;
6. diff boundary contains only allowed files;
7. no new high-severity logs/errors are introduced;
8. branch-protection-required checks are green.

### 7. Autonomous merge/deploy policy

Autonomous publication is allowed only when **all** are true:

- risk is R0 or R1;
- root-cause confidence is high;
- a deterministic regression test covers the defect;
- all required checks pass;
- no protected/governance/model-math/schema/auth files are changed;
- no database migration is required;
- no secret or environment-variable value is changed;
- deployment is reversible by reverting the single patch commit;
- production verification can be performed immediately afterward.

If any condition is false, leave a tested PR and report `HUMAN_REVIEW_REQUIRED`.

### 8. Production verification

After any autonomous publish/deploy:

- confirm the new commit/deploy is live;
- run a fresh health/governance check;
- rerun the exact failing scenario or the closest safe production acceptance path with fresh IDs;
- confirm the original error is absent;
- confirm no new P0/P1 errors appear in the verification window;
- record deployment ID, commit SHA, UTC verification time, and evidence.

If production verification fails, immediately revert or roll back the autonomous R0/R1 patch when a safe deterministic rollback path exists. Otherwise stop and report `ROLLBACK_REQUIRED`.

## Nightly acceptance probes

At minimum, the run should attempt to verify these V17 contracts without manufacturing unavailable data:

- backend runtime is reachable and reports V17-active semantics;
- canonical prop Action contract parses and routes correctly;
- canonical team/event Action contract parses and routes correctly;
- a valid completed sporting probability is not erased solely by missing market evidence;
- scorer failures preserve typed failure semantics;
- identity/input failures are not mislabeled as rank-eligible results;
- terminal reducer remains authoritative;
- execution remains disabled;
- required CI contracts remain green.

If real pregame inputs are not safely available, use deterministic fixtures or contract tests rather than inventing live sporting data.

## Mandatory incident-record lifecycle

Postmortems and engineering fixes are separate first-class artifacts and must remain linked through `artifacts/wow-engine/v17/incident-ledger.json`.

For every reproducible defect that enters diagnosis beyond simple observation:

1. create a postmortem before changing code using `python artifacts/wow-engine/v17/nightly_incident_records.py create-postmortem ...`;
2. record impact, evidence, deterministic reproduction or explicit evidence-only status, root cause confidence, severity, domain, and V17 governance classification;
3. if remediation is warranted, create a linked engineering fix using `create-fix` before implementation;
4. place implementation, allowed-file boundary, regression test, validation gates, deployment reference, rollback reference, and production verification in the FIX record rather than the PM record;
5. keep both records and the ledger in the same repair branch/PR as the code change whenever possible;
6. run `python artifacts/wow-engine/v17/nightly_incident_records.py validate` before publication;
7. never mark the PM or FIX closed until fresh production verification passes.

A PM may exist without a FIX when the issue is observational, unreproduced, accepted risk, or R3 diagnose-only. A FIX must never exist without a linked PM.

Required lifecycle states are:

- `OPEN`
- `DIAGNOSED`
- `FIX_IN_PROGRESS`
- `HUMAN_REVIEW_REQUIRED`
- `DEPLOYED_PENDING_VERIFY`
- `VERIFIED_CLOSED`
- `ROLLBACK_REQUIRED`

Do not use `VERIFIED_CLOSED` as a substitute for actually verifying production.

## Output contract

Every nightly run must produce one compact engineering report with:

```yaml
run_status: HEALTHY | REPAIRED_AND_VERIFIED | DEGRADED | HUMAN_REVIEW_REQUIRED | ROLLBACK_REQUIRED
utc_time:
main_commit:
production_deploy:
findings:
  - id:
    postmortem_id:
    engineering_fix_id:
    severity:
    domain:
    evidence:
    root_cause:
    risk:
    action_taken:
patches:
  - change_id:
    postmortem_id:
    engineering_fix_id:
    branch:
    commit:
    pr:
    tests:
    deployment:
    production_verification:
unresolved:
next_highest_priority:
can_execute: false
```

Do not report `REPAIRED_AND_VERIFIED` unless the production verification gate actually passed.

## Priority order

1. P0 security/governance/data-integrity failures
2. P1 broken production routes or invalid governed output
3. acceptance blockers for prop/team-event probability lanes
4. CI or deployment drift
5. observability and developer-experience defects
6. documentation drift

## Relationship to existing skills

- `wow-replit-patch-governor` controls the patch mechanics and bounded-change workflow.
- V17 domain/model skills control sporting probability semantics.
- This nightly skill controls detection, triage, risk classification, repair eligibility, autonomous publication, rollback, nightly reporting, and PM↔FIX lifecycle enforcement.

No part of this skill may override a stricter V17 governance rule.
