# WOW V17 Nightly Engineering Autopilot

Load this skill for every scheduled or manually triggered WOW V17 engineering-health run. It extends `.agents/skills/wow-replit-patch-governor/SKILL.md`; that skill remains authoritative for bounded implementation, diff control, regression testing, publication, rollback, and production verification.

## Mission

Every night, inspect the live WOW V17 system and repository for defects, regressions, contract drift, broken routes, failing tests, unhealthy deployments, stale assumptions, inconsistent persistence, or invalid terminal semantics. The overnight engineer is expected to drive every discovered item to a terminal engineering state before daytime handoff.

The objective is **finish-safe-work overnight**, not merely diagnose it. A finding may end the run only in one of these terminal states:

- `VERIFIED_CLOSED` — repaired, merged through protected checks when required, deployed when applicable, and verified with fresh evidence;
- `CLOSED_SUPERSEDED` — proven already fixed or fully replaced by newer authoritative work and stale PR/issue state cleaned up;
- `BLOCKED_HARD_BOUNDARY` — completion is impossible without violating an immutable V17 safety boundary or an unavailable third-party dependency. This state requires a precise blocker record and must never be used as a convenience substitute for finishing available work;
- `ROLLBACK_REQUIRED` — a published repair failed fresh production verification and deterministic rollback could not be completed safely in the same run.

`DEGRADED`, `HUMAN_REVIEW_REQUIRED`, or a generic `unresolved` list are not acceptable resting states when the nightly engineer can safely finish the work under the policy below. The engineer must keep reconciling, testing, merging, deploying, closing superseded work, retrying observability, or creating a bounded successor repair until the item reaches a terminal state.

## Immutable V17 invariants

These may never be weakened by the overnight-completion mandate:

- `can_execute=false` is unconditional.
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` is unconditional.
- `V17_TERMINAL_REDUCER` remains the sole global terminal authority.
- Missing or contradictory evidence fails closed.
- An invoked model failure retains its typed scorer/output failure and is never rewritten as `MODEL_UNAVAILABLE` merely to simplify a run.
- Market-price absence may block value publication but must not erase a completed sporting probability when the governing lane preserves it.
- Never manufacture probability, calibration, model capability, exact-line support, terminal labels, settlement evidence, or provenance to make a check pass.
- Never expose, rotate, replace, or weaken secrets/auth automatically.
- Never weaken branch protection or required GitHub checks.
- Never weaken RLS.
- Never perform destructive or irreversible data changes automatically.
- Never enable live wager execution, order routing, wager cancellation, or market-order capability.

## Overnight completion authority

The user's standing overnight authorization permits the engineer to complete repair work through protected CI and production when all applicable validation gates pass.

### R0 — no-code / observability / hygiene

Examples: stale PRs, logging defects, diagnostics, comments, typos, dead non-governing config, duplicate/superseded branches.

**Authority:** diagnose, repair, close stale/superseded artifacts, merge, deploy if applicable, and verify automatically.

### R1 — bounded implementation

Deterministic implementation defects with no new probability math, calibration policy, terminal precedence, schema meaning, auth/secret change, branch-protection change, or destructive data effect.

**Authority:** diagnose, patch, add/strengthen regression tests, merge through protected checks, deploy, and verify automatically.

### R2 — governed behavior

Includes probability-model implementation, calibration implementation, evidence precedence, exact-line support, persistence semantics, Action request/response contracts, terminal behavior, identity rules, or cross-lane routing.

R2 is split into:

- **R2-restorative:** restores conformance to an already-authoritative V17 contract/test/schema/accepted invariant without introducing new math, thresholds, precedence, label meaning, schema meaning, or product policy. Complete automatically when high-confidence and all gates pass.
- **R2-repair-policy:** a bounded policy or contract change required to fix a reproducible defect and explicitly covered by the standing overnight completion authorization. It may be completed overnight only when the change is narrowly scoped, reversible, fully regression-tested, does not touch an R3 hard boundary, and does not invent sporting probability or calibration evidence. The patch contract must explain why the change is necessary, what authoritative behavior it replaces, and what rollback restores the prior state.

An additive, reversible schema migration may be treated as R2-repair-policy when it only adds fail-closed storage/API capability, has a documented rollback, preserves RLS, exposes no secrets, does not destructively transform existing data, and the current request explicitly directs the nightly engineer to finish that exact work. Destructive migrations remain R3.

### R3 — hard safety / infrastructure boundary

Includes secret rotation/replacement, auth weakening, RLS weakening, branch-protection weakening, destructive migrations, irreversible data correction, external credential mutation, or live execution capability.

**Authority:** diagnose and prove the exact blocker only. Do not perform the prohibited mutation. If the defect can be repaired without crossing the R3 boundary, do that instead. Otherwise mark `BLOCKED_HARD_BOUNDARY` with exact evidence and the minimum human-only action required.

## Nightly run sequence

### 1. Establish current truth

Inspect at minimum:

1. latest `main` SHA and branch-protection requirements;
2. all open PRs and recent merged PRs relevant to V17;
3. required GitHub checks and latest failures;
4. Render deployment state for `wow-governed-probability-engine` and related V17 services;
5. recent Render application/build/request logs, retrying transient Loki/provider failures with broader/fallback queries;
6. `/health`, `/governance`, and safe acceptance routes where reachable;
7. recent V17 workflow/runtime failures, schema errors, Action errors, persistence inconsistencies, or terminal anomalies;
8. contract drift among runtime code, `render.yaml`, V17 schemas/OpenAPI, current instructions, migrations, and tests;
9. known unresolved issues and open PRs from prior nightly runs.

Never assume yesterday's diagnosis is current.

### 2. Classify and choose a terminal target

For each finding record:

- severity: P0/P1/P2/P3;
- domain;
- reproducible true/false;
- root-cause confidence;
- risk R0/R1/R2-restorative/R2-repair-policy/R3;
- target terminal state: `VERIFIED_CLOSED`, `CLOSED_SUPERSEDED`, `BLOCKED_HARD_BOUNDARY`, or `ROLLBACK_REQUIRED`.

### 3. Reproduce before repair

No implementation change without a concrete failing signal. Acceptable evidence includes deterministic tests, failed CI with traceable error, fresh production request/log traces, schema validation errors, persisted-row inconsistencies tied to a code path, or a current-main/PR contract mismatch.

### 4. Build the mandatory patch packet

Use `wow-replit-patch-governor` before editing. Every packet must include exact allowed/protected files, rollback condition, regressions, production verification, and `can_execute=false`.

For R2 work explicitly state:

- `new_probability_math`;
- `new_calibration_threshold`;
- `terminal_precedence_changed`;
- `contract_meaning_changed`;
- `auth_or_secret_change`;
- `schema_migration` and rollback;
- binding authority or explicit overnight repair authorization.

### 5. Repair minimally

- Touch only declared allowed files.
- Add or strengthen deterministic regression coverage whenever practical.
- Do not refactor unrelated code.
- Do not broaden model support merely to eliminate an error.
- Preserve typed fail-closed semantics.
- Reconcile stale PR branches onto current `main` instead of merging old trees over newer authoritative changes.
- When an older PR is fully superseded, prove that with current-main evidence and close it rather than leaving it open.

### 6. Validation gates

A repair is not complete until all applicable gates pass:

1. targeted reproduction passes;
2. relevant unit/integration tests pass;
3. required WOW regressions pass;
4. OpenAPI/schema/Action validation passes when applicable;
5. migration validation and rollback plan pass when applicable;
6. `can_execute=false` and dry-run assertions remain intact;
7. terminal reducer authority remains intact;
8. diff boundary is clean;
9. protected GitHub checks are green;
10. no new P0/P1 production errors appear in the verification window.

### 7. Merge/deploy policy

Autonomous merge/deploy is allowed for R0, R1, R2-restorative, and explicitly authorized R2-repair-policy when all validation gates pass and no R3 hard boundary is touched.

Requirements:

- protected checks must pass; never bypass branch protection;
- no secret/environment credential value may change;
- no RLS weakening;
- no destructive migration;
- deployment must be reversible;
- additive migrations require explicit rollback instructions and fail-closed access controls;
- production verification must be possible immediately after deployment;
- `can_execute=false`, dry-run-only, terminal authority, and fail-closed behavior remain binding.

If the PR is stale relative to current `main`, reconcile through a temporary/current-main branch, run protected checks on the exact combined tree, and merge only that reconciled PR.

### 8. Production verification

After every published repair:

- confirm the expected commit is live;
- confirm `/health` and governance/runtime invariants;
- rerun the exact failing scenario or closest safe acceptance path with fresh IDs;
- verify Action/schema/persistence behavior when relevant;
- inspect fresh logs;
- confirm no new P0/P1 errors;
- record deployment ID, commit SHA, UTC verification time, and evidence.

If production verification fails, revert/rollback in the same run when deterministic rollback is available. If rollback cannot be safely completed, report `ROLLBACK_REQUIRED` and stop further dependent changes.

## Observability fallback rule

A transient Render Loki/API 5xx is not by itself a WOW defect and must not leave the nightly run `DEGRADED` when alternate evidence is available. Retry with:

1. narrower time windows;
2. broader unfiltered app/request queries;
3. deploy/build logs;
4. health requests and safe self-acceptance traces;
5. GitHub deployment/CI evidence.

If at least one fresh production verification path succeeds and no P0/P1 runtime error is found, close the observability incident as `CLOSED_SUPERSEDED` or `VERIFIED_CLOSED` with the provider degradation recorded separately. Only use `BLOCKED_HARD_BOUNDARY` when all safe verification paths are unavailable.

## Open-PR closure rule

Every nightly run must individually classify every open V17 PR as one of:

- current and merge-ready repair -> reconcile/test/merge/verify;
- current but R3-hard-boundary -> block precisely;
- superseded by current main -> close with evidence;
- duplicate of another authoritative PR -> close as duplicate/superseded;
- documentation/acceptance-only and already represented on main -> close as superseded;
- still-valid future feature rather than defect -> move out of the nightly defect queue with an explicit non-blocking disposition.

Do not carry a vague backlog of old governed PRs into daytime merely because they are old or complicated.

## Mandatory incident lifecycle

For every reproducible defect that proceeds beyond observation, maintain linked PM/FIX records in `artifacts/wow-engine/v17/incident-ledger.json` when the repository tooling supports it. Do not mark a FIX `VERIFIED_CLOSED` until fresh production verification passes.

Lifecycle states:

- `OPEN`
- `DIAGNOSED`
- `FIX_IN_PROGRESS`
- `DEPLOYED_PENDING_VERIFY`
- `VERIFIED_CLOSED`
- `CLOSED_SUPERSEDED`
- `BLOCKED_HARD_BOUNDARY`
- `ROLLBACK_REQUIRED`

## Output contract

Every nightly report must include:

```yaml
run_status: HEALTHY | REPAIRED_AND_VERIFIED | BLOCKED_HARD_BOUNDARY | ROLLBACK_REQUIRED
utc_time:
main_commit:
production_deploy:
findings:
patches:
production_verification:
closed_superseded:
hard_blockers:
unresolved: []
next_priority:
can_execute: false
```

`unresolved` should be empty. Work that cannot be safely completed must instead appear under `hard_blockers` with a terminal `BLOCKED_HARD_BOUNDARY` disposition and exact evidence.

Do not report `REPAIRED_AND_VERIFIED` unless production verification actually passed for every published repair in the run.

## Priority order

1. P0 security/governance/data-integrity failures
2. P1 broken production routes or invalid governed output
3. persistence/Action/schema inconsistencies
4. acceptance blockers
5. CI/deployment drift
6. stale/superseded PR cleanup
7. observability and documentation drift

## Relationship to existing skills

- `wow-replit-patch-governor` controls bounded patch mechanics.
- V17 domain/model contracts control sporting probability semantics.
- This skill controls overnight completion, triage, repair eligibility, stale-PR reconciliation, publication, rollback, production verification, and terminal handoff state.

No part of the overnight-completion mandate may override a stricter immutable V17 safety rule.
