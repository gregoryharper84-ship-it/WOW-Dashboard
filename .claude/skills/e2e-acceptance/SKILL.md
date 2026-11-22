---
name: e2e-acceptance
description: Verify an end-to-end WOW V17 engineering change from code through protected release, Render/Supabase runtime, model/Action receipts, and customer-visible behavior. Use for deployment, release readiness, and completion verification.
---
# End-to-End Acceptance — evidence over assertions

For the exact PR/head and requested behavior, independently enumerate checks:

1. **Source:** branch, base, current PR head SHA; no unrelated or conflicting writer changes; minimal diff.
2. **Tests:** names, invocation, actual pass/fail, regression coverage at that head; classify unrun or flaky distinctly.
3. **Security/governance:** current branch-protection and QA/Release policy; independently issued review verdict from an authorized non-author; no reviewer-route shortcuts or gate suppression.
4. **Merge:** actual merged state and merge SHA, not a local branch SHA or green PR.
5. **Runtime:** actual service deploy SHA (web and worker as applicable), readiness, relevant queue/cron/dispatcher health, schema/migrations and registry state; observe rather than infer.
6. **Product:** call the real canonical route using safe test data. For supported sports, require a genuine fitted model response and its complete probability/terminal package. For unsupported or nonqualifying rows, verify accurate native typed failure and conservation.
7. **Action/Custom GPT:** verify current deployed Action schema/route and, when accessible, a real calling-host receipt and live editor parity. Do not infer editor sync from repository docs.
8. **Regression:** check neighboring sports/routes and preserved-good scenarios (especially NFL Pick'em when shared adapters change).
9. **Truthful closeout:** record each layer as `PASS`, `FAIL`, `BLOCKED`, or `NOT_VERIFIED`; calculate final status from the most stringent applicable requirement.

**Acceptance cannot be self-certified by the implementing Claude instance.** An internal adversarial check may find defects and speed review, but SIRT/independent acceptance must be separately sourced and proved.

Proof table:

| Layer | Evidence (link/receipt/SHA) | Result | Owner if blocked |
|---|---|---|---|
| Source/CI | | | |
| Independent QA/Release | | | |
| Merge | | | |
| Runtime/schema | | | |
| Model/Action/row reconciliation | | | |
| End-user consumer | | | |

Only mark `COMPLETE` when every applicable requirement has direct proof. Do not elevate `NOT_VERIFIED` into `PASS`.
