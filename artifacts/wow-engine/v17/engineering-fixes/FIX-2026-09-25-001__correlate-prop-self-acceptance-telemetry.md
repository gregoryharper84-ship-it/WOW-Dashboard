# FIX-2026-09-25-001 — Correlate prop self-acceptance telemetry

- status: VERIFIED_CLOSED
- linked_postmortem: PM-2026-09-25-001
- implementation_owner: ENGINEERING_AGENT (`/root/engineering_agent`)
- risk: R1
- pull_request: 835
- pr_head_sha: `fd97086e77dfc0be9ebe18132617d4cf7d3a35d9`
- merge_commit: `10084e5039ae8a91cf54586388795ef74b497d93`
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Repaired

The startup self-acceptance client omitted existing invocation-correlation
headers, making its expected fail-closed probes indistinguishable from generic
Action-key calls in durable telemetry.

## Implementation

PR #835 made the bounded two-file repair:

- `artifacts/wow-engine/api_prod_market_acceptance.py`
  - sets caller class `SELF_ACCEPTANCE`;
  - creates one non-secret UUID per direction and reuses it across retries;
  - reports `X-WOW-Rows-In: 1`.
- `artifacts/wow-engine/test_api_prod_market_acceptance.py`
  - forces transient failure then success for MORE and LESS;
  - verifies stable per-direction IDs, distinct direction IDs, caller class,
    row count, UUID validity, and no secret/probe-body log leakage.

No middleware fallback, request-body capture, Action schema, authentication,
response, probability, terminal, publication, or execution behavior changed.

## Review and QA

- System Architect: `PASS_WITH_BOUNDED_CONDITIONS`, R1.
- Independent Review: PASS on exact head `fd97086e…` after one rework loop.
- QA: PASS; targeted+adjacent 10 pass, neighbor 10 pass, scope 17 pass.
- Exact-head CI: `36151711699`, `36151711694`, `36151711710` — SUCCESS.

Historical handoff timestamps were unavailable. Agent identities and evidence
are preserved in the linked ledger using reconstruction timestamp
`2026-09-25T16:03:57Z`.

## Release

PR #835 was squash-merged at
`10084e5039ae8a91cf54586388795ef74b497d93`. Release/Observability verified its
ancestry into deployed SHA `4ec2bed8c9a7860815529b8af38d88f9cc7317ff`,
Render deployment `dep-darbv1btqb8s73et67i0`, workflow/job
`36174638442` / `108202153305`, deployment pointer `6667702080`, passing
health/governance/original replay, and correlated `SELF_ACCEPTANCE` receipts
`a3d76169-5103-4ff9-bdd1-eee2bc18fd22` and
`32145dad-57c9-4682-abaf-1f1476cddb20` at `2026-09-25T19:31:23Z`.

Reporter closure is `FIXED_VERIFIED`; the fix is `VERIFIED_CLOSED`.

## Rollback

Revert merge commit `10084e5039ae8a91cf54586388795ef74b497d93`.
The startup probes would return to uncorrelated telemetry; sporting probability,
terminal behavior, and `can_execute=false` are unchanged either way.
