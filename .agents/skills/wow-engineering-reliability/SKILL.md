---
name: wow-engineering-reliability
description: Enforce expert-level, machine-verifiable reliability across the entire WOW V17 engineering department.
---

# WOW V17 Engineering Reliability Standard

Status: `ACTIVE_ON_MERGE`
Contract: `WOW_ENGINEERING_RELIABILITY_V1`
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Scope

This skill is mandatory for every WOW engineering role, workflow, repair, review, QA run, release verification, closure controller, specialist support lane, and autonomous engineering orchestrator.

It is a reliability contract, not a prompt suggestion. No engineering role may weaken, bypass, reinterpret, or replace it locally.

## Reliability lifecycle

Every code-affecting incident must follow:

`REPRODUCE -> ROOT_CAUSE -> CLASSIFY -> MINIMAL_FIX -> READBACK -> FOCUSED_REGRESSION -> ADJACENT_REGRESSION -> INDEPENDENT_REVIEW -> QA -> EXACT_HEAD_CI -> PROTECTED_MERGE -> EXACT_DEPLOY -> PRODUCTION_ACCEPTANCE -> FIXED_AND_VERIFIED`

Intermediate states such as code written, tests green, PR open, CI green, merged, or deployed are never equivalent to `FIXED_AND_VERIFIED`.

## Mandatory gates

### 1. Reproduction gate
- Prove the defect with deterministic evidence when possible.
- Distinguish symptom from root cause.
- Preserve the exact typed failure.
- Record expected behavior, observed behavior, affected component, environment, severity, change class, acceptance criteria, and regression guard.

### 2. Minimal-change gate
- Implement the smallest change that repairs the confirmed root cause.
- No unrelated refactor.
- No test weakening.
- No hidden fallback that manufactures success.
- Class C probability behavior remains challenger-only.

### 3. Connector-write readback gate
Every file written through an agent/connector must be read back before the write is trusted. Formatting, escaping, truncation, imports, and exact semantic content must be verified.

### 4. Behavioral regression gate
New or changed routes, typed blockers, terminal labels, enums, identity rules, persistence semantics, or capabilities require behavior-level regression tests. String-presence tests are insufficient.

Negative paths must be exercised where applicable:
- missing/stale/malformed input;
- wrong event or participant identity;
- provider mismatch;
- unsupported sport/market/model;
- auth failure;
- transport failure;
- persistence failure;
- duplicate/exact-once behavior;
- `can_execute=false`.

### 5. Exact-head gate
Any new commit invalidates prior CI evidence. Required checks must be green on the exact final PR head. A prior SHA cannot authorize merge of a newer SHA.

### 6. Independent review gate
The implementer cannot approve the repair. Independent Review and QA must challenge root cause, scope, regressions, typed failures, identity, rollback, and adjacent-lane behavior. System Architect review remains mandatory for protected contracts.

### 7. Machine-readable verification receipt
Free-form prose is not terminal verification evidence.

New Reliability V1 repair PRs must produce a `VerificationReceipt` defined by:
`artifacts/wow-engine/v17/receipt_schema.py`

The receipt binds:
- issue and PR identity;
- exact PR head;
- protected-main merge SHA;
- deployed Render SHA;
- allowlisted route;
- receipt-schema hash;
- HTTP status;
- observable dry-run / non-execution headers;
- raw response SHA-256;
- execution-trace SHA-256;
- trusted sentinel workflow provenance;
- UTC verification time.

Normal merge commits have a different SHA from the PR head. Therefore terminal equality is:
`merge_sha == deployed_render_sha`
while `exact_head_sha` is independently bound to the PR's final head.

### 8. Acceptance allowlist
Production acceptance may target only routes declared in:
`artifacts/wow-engine/v17/production_acceptance_routes.json`

A syntactically valid receipt for an unregistered, mock, nonexistent, or test-only route must fail closed.

### 9. Skill execution trace
Reliability verification must run through the repository CLI:
`python -m v17.reliability_verify_issue`

The CLI emits a machine-readable execution trace. A claimed verification without the trace digest is invalid.

### 10. Immutable evidence
GitHub comments are human-readable mirrors, not the only source of truth.

Reliability receipts and raw evidence must be retained as workflow artifacts and, when the governed append-only reliability ledger is available, persisted there. Terminal closure must bind the receipt digest to durable evidence rather than trusting prose.

### 11. Production acceptance and rollback
A live deployment is necessary but not sufficient.

The Release/Observability lane must:
- verify exact deployed SHA;
- run the route-specific acceptance;
- inspect fresh runtime evidence;
- distinguish application regression from provider/fixture/observability failure.

Rollback must be deterministic and evidence-gated. A single ambiguous failed probe may not automatically roll back healthy production. Automatic rollback is allowed only when the acceptance failure is attributable to the new deployment under the repository's rollback policy and the prior known-good deployment is proven safe and compatible.

### 12. Terminal reducer
For Reliability V1 incidents, `V17_TERMINAL_REDUCER` must reject closure when the machine receipt is absent or invalid. The canonical blocker is:
`INVALID_RECEIPT_SCHEMA`
with a more specific subreason retained in evidence.

## Safety invariants

Always preserve:
- `custom_gpt_identity=WOW_BETTING_ENGINE`;
- `runtime_generation=V17_ACTIVE`;
- `terminal_authority=V17_TERMINAL_REDUCER`;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`;
- exactly one controlling fitted sporting specialist per probability row;
- typed failures;
- immutable prediction integrity;
- exact-once semantics;
- no sportsbook/public projection/recent-result/generic-LLM probability substitution.

## Reliability status vocabulary

Use these distinct states:
- `REPRODUCED`
- `ROOT_CAUSE_CONFIRMED`
- `CODE_GREEN`
- `EXACT_HEAD_GREEN`
- `MERGED`
- `DEPLOYED`
- `PRODUCTION_ACCEPTED`
- `FIXED_AND_VERIFIED`

Never skip a state by inference.
