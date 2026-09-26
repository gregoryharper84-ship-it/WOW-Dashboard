# PM-2026-09-25-001 — Prop self-acceptance telemetry correlation

- status: VERIFIED_CLOSED
- severity: P1
- primary_subsystem: WOW_HOST_ORCHESTRATION
- workflow_stage: REPORTER_CLOSURE
- current_owner: REPORTER_AGENT
- reconstruction_utc: 2026-09-25T16:03:57Z
- reported_by: runtime
- risk_class: R1
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Expected Behavior

Startup prop self-acceptance probes must be distinguishable from real ChatGPT
Action traffic in the durable invocation ledger. Each logical MORE/LESS probe
must carry a non-secret request ID that is stable across retries and report one
input row, without persisting the request body or exposing credentials.

## Actual Behavior

The startup self-acceptance client authenticated with the production Action key
but omitted the existing caller-class, request-ID, and row-count telemetry
headers. Its intentional fail-closed `/score-prop` 422 probes were therefore
recorded as `ACTION_API_KEY` with `request_id=null` and `rows_in=null`.

The repeated 422s were the expected `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND` startup
probes. They did not reproduce a supported-current-board scoring failure; that
claim remains `INSUFFICIENT_EVIDENCE`.

## Root Cause

`api_prod_market_acceptance._run_prop_live_self_acceptance` omitted
`X-WOW-Caller-Class`, `X-WOW-Request-ID`, and `X-WOW-Rows-In` even though the
invocation middleware already supported those headers. Bearer authentication
alone therefore became the fallback caller classification.

Root cause status: CONFIRMED, confidence HIGH.

## Acceptance Criteria

1. Startup probes are recorded as `SELF_ACCEPTANCE`.
2. Each MORE/LESS logical probe uses one non-secret UUID across retries and the
   two directions use different IDs.
3. Every attempt reports `rows_in=1` without request-body capture or secret/probe-body log leakage.
4. Targeted, neighboring telemetry, scope, and exact-head protected CI pass;
   `can_execute=false` and V17 terminal authority remain unchanged.
5. Before Reporter closure, Release/Observability confirms the deployed Render
   SHA is merge `10084e5039ae8a91cf54586388795ef74b497d93`
   or a verified descendant with ancestry proof, and fresh `SELF_ACCEPTANCE`
   receipts retain the expected correlation.

## Team Handoffs

Historical handoff timestamps were unavailable. The canonical ledger records
one explicit reconstruction timestamp (`2026-09-25T16:03:57Z`) and preserves
the actual role/agent identities and evidence from the automation transcript:

- `/root/reporter_agent` → `/root/research_triage_agent`: intake/dedup complete;
  transport reached the app; isolate caller/middleware/route/persistence.
- `/root/research_triage_agent` → `/root/engineering_agent`: startup probes and
  omitted correlation headers proved; bounded header repair defined.
- `/root/system_architect_agent`: `PASS_WITH_BOUNDED_CONDITIONS`, R1; existing
  headers/schema suffice and no protected semantics may change.
- `/root/engineering_agent` → `/root/independent_review_agent`: initial
  two-file head `d4101dc6…` with targeted/adjacent tests.
- `/root/independent_review_agent` → `/root/engineering_agent`: rejected for a
  deterministic retry-stability test and R1 marker reconciliation.
- `/root/engineering_agent` → `/root/independent_review_agent`: rework head
  `fd97086e…`, retry coverage added and risk reconciled.
- `/root/independent_review_agent` → `/root/qa_verification_agent`: PASS with
  scope, architecture, governance, and security checks passing.
- `/root/qa_verification_agent` → `/root/release_observability_agent`: QA PASS;
  exact-head protected CI runs `36151711699`, `36151711694`, and `36151711710`
  succeeded.
- `/root/release_observability_agent` → `/root/reporter_agent`: PR #835 was
  squash-merged as `10084e5039…`; descendant deployment `4ec2bed8…`, Render
  deployment, workflow/job, pointer, health/governance/replay, and correlated
  receipts were verified at `2026-09-25T19:31:23Z`.

## Linked Engineering Fix

- FIX-2026-09-25-001
- PR #835
- merge commit: `10084e5039ae8a91cf54586388795ef74b497d93`

## Release / Closure

Review: PASS. System Architect: PASS_WITH_BOUNDED_CONDITIONS. QA: PASS.

Release status: `PRODUCTION_VERIFIED`.

- merge commit: `10084e5039ae8a91cf54586388795ef74b497d93`
- verified descendant deployed SHA: `4ec2bed8c9a7860815529b8af38d88f9cc7317ff`
- Render deployment: `dep-darbv1btqb8s73et67i0`
- workflow/job: `36174638442` / `108202153305`
- deployment pointer: `6667702080`
- health/governance/original replay: PASS
- correlated receipts: `a3d76169-5103-4ff9-bdd1-eee2bc18fd22`,
  `32145dad-57c9-4682-abaf-1f1476cddb20`
- verified UTC: `2026-09-25T19:31:23Z`

Reporter closure: `FIXED_VERIFIED`. The incident is `VERIFIED_CLOSED`.

---

This record cannot authorize, route, modify, approve, or cancel a wager/order.
`can_execute=false` remains unconditional.
