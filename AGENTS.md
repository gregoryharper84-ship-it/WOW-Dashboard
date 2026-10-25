# WOW Engineering — V25 Execution-First Contract

**Applies to Codex and other repository coding agents.** `CLAUDE.md` remains the full engineering contract. This short operational policy supplements it and is **subordinate** to the active WOW V17 governance hierarchy, approved protected workflows, and independent Review/QA/Release authority. It does not grant new GitHub, Render, database, or editor permissions.

## Standing mission

For one assigned incident at a time: **DETECT → REPRODUCE → FIX → TEST → REVIEW → RELEASE → VERIFY → LEARN**. Produce a real patch or a precise typed blocker. Diagnosis, PR creation, successful CI, and a successful deploy trigger do **not** establish production closure.

## GO: pre-authorized Class A reliability steps

On the incident branch, without an additional operator prompt:

1. Inspect current main SHA, incident, ownership, relevant PRs, source, CI, logs and available runtime evidence. Reproduce the exact failure before patching, or record why reproduction cannot be obtained.
2. Select the smallest permitted Class A fix; create a dedicated branch; edit source; add focused, negative, regression, integration and adjacent-lane tests as applicable.
3. Run accessible tests, inspect failures, correct the cause, and retry **up to three bounded evidence-driven attempts** per failure. Never retry blindly, hide failures, or weaken gates to get green.
4. Commit and push only allowed paths; open/update the linked repair PR; attach exact branch/head SHA, tests, evidence, risk class, and rollback.
5. Continue through existing independent review, approval, protected merge, and deploy workflows **only if every applicable exact-SHA gate and authorization is independently established**. Verify the actual production SHA, behavior, persistence, reconciliation, and QA receipt before closure.

Do not ask “Want me to proceed?” while a pre-authorized step remains possible. If a tool is unavailable, record the exact blocker and continue independent authorized work. Do not claim chat itself continues executing after a turn; unattended work requires deployed, governed workers or scheduled workflows.

## HOLD: not pre-authorized by this document

- Class B probability-adjacent, agent instruction/trust-root, identity, routing, workflow permissions, release-control or protected-configuration changes: implementation and tests may be staged, **but adoption/merge/deploy requires independently governed review and authorization**.
- Class C probability-producing model, calibration, thresholds, fitted artifacts, qualification or lower-bound changes: discovery, challenger, replay and validation may be staged, **never silently promote**.
- Do not create/rotate/expose secrets, weaken authentication/RLS/branch protection, bypass CODEOWNER or Security/QA/Release, self-approve (including using the same principal through a different tool), or write directly to protected main.
- Never deploy if production security or release authorization is missing. Unknown identity, provenance, lease, receipts or independent approvals fail closed.
- Never place bets or orders, or substitute odds, predictions, narratives, LLM estimates or stale scores for a governed fitted sporting probability. Exactly one approved specialist controls each probability row.

## Outcome contract

**`DIAGNOSED_NOT_FIXED` is non-success**, never a closure label. Use one incident disposition: `FIXED_AND_VERIFIED`, `PR_CREATED`, `EXPERIMENT_CREATED`, `DUPLICATE`, `NOT_REPRODUCIBLE`, `BLOCKED_WITH_EXACT_REASON`, or `DEFERRED_WITH_JUSTIFICATION`.

`FIXED_AND_VERIFIED` requires root-cause/fix evidence, focused/negative/regression/adjacent tests, independent review/required approvals, authorized merged SHA, live production SHA/acceptance, persistence/reconciliation and independent QA. Otherwise retain incident owner, typed blocker, next permitted action and re-entry condition.

End every engineering turn with: **disposition; incident/owner; branch/PR/head SHA; tests and results; production verification state; blocker; next action**. Do not label reporting-only progress as a repair.

## Immutable V17 boundaries

```text
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```
