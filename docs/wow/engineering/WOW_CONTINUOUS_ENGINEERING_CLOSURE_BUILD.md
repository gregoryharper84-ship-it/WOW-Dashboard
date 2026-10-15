# WOW V17 — Persistent Engineering Closure: Controlled Build Plan

**Status:** STAGED COMPONENT / NOT PRODUCTION ACTIVATED. This plan reuses the existing Render `wow-agent-worker` resident supervisor and existing protected GitHub engineering workflows. It does **not** create a rival orchestration or an alternate release authority.

## Actual architecture

1. **Resident dispatcher:** `v17/engineering_resident_supervisor.py` in the existing Celery worker checks approved incident manifests periodically. Its enablement flag stays OFF until independent acceptance.
2. **Durable continuation state (this candidate):** `v17/engineering_continuity_controller.py` evaluates immutable per-issue snapshots, records intent, performs bounded dispatch admission, rechecks exact SHA and lease fencing, and records a typed dispatch receipt. It proposes same-branch repair of failed App-authored nonprotected PRs; it cannot self-approve or merge.
3. **DB control plane:** #1739 / Draft PR #1743 supplies the proposed authoritative atomic, epoch-fenced per-incident writer lease. Redis SET NX from the existing supervisor is **only a replica/dispatch gate**, never a cross-agent incident writer lease. Every actual writing entrypoint (manual Claude, OpenAI, Copilot, Anthropic fallback, dispatcher repair) must use the same independently certified DB-backed writer lease; every branch/PR mutation must fence.
4. **Authorized executor:** use the existing protected `wow-v17-engineering-provider-dispatcher` and verified Engineering App-authored worker rather than issue text or a free-form shell command. No untrusted issue/LLM prose becomes commands; a dispatch receipt is **not** a repair success.
5. **Independent release:** #1758/#1759 release-provenance and exact-SHA deployment gates and #1748 background worker release require separate App-authored trust-root adoption, independent CODEOWNER/security/QA/Release review, and actual Render exact-commit acceptance. Do not use generic unpinned deploy triggers.
6. **Independent SIRT:** read-only assessment, recorded evidence and explicit closure, distinct from the writer.

## Controller port implementation contract (not included in this staging PR)

- `load` must read current, trusted GitHub/DB state, confirm event/issue identity and exact main/PR-head SHAs, and reconcile any older `INTENT_*` or ambiguous `DISPATCH_*` receipt; it must set `pending_dispatch=True` until an independent workflow receipt proves the prior dispatch was completed or safely failed. Do not rely on a local memory flag or issue comment as truth.
- `checkpoint` must perform a **unique durable insert/CAS**, not a best-effort log append, and reflect the pending record in all replicas. Concurrent identical intents cannot both succeed. Fail closed if the ledger is unavailable.
- `claim_dispatch_admission` / `check_fence` / `release_dispatch_admission` use a real atomic store with epoch and TTL and serve ONLY dispatcher admission. An asynchronous remote worker must independently obtain and continually renew its own DB **writer** lease; dispatcher handoff cannot transfer write authority by passing an opaque token in untrusted inputs.
- `verified_writer_available`, `lease_service_ready`, `pr_app_authored` and `action_receipt_verified` are **results of separately authenticated checks**, not user-controlled flags. The trusted port must validate GitHub App actor, scope, lease issuer and independent release/caller-class receipts before exposing these booleans. This Python policy module cannot attest its own authority.
- `dispatch` only invokes a fixed allowlisted protected workflow with exact incident/SHA/PR; it cannot run arbitrary shell, approve PRs, alter protected workflows, merge, deploy, or submit wagers. On ambiguous API receipt the code records a typed hold; later loops reconcile rather than blindly sending again.
- `release_dispatch_admission` must be safe under errors and must never release the separate remote writer lease.

## Acceptance / gating matrix

| Gate | Required proof | Current status |
| --- | --- | --- |
| Phase 1 policy regression | Deterministic tests for exact-SHA changes, CAS pre-intent, admission fencing, App provenance, retry cap, ambiguous ACK, no duplicate PR, missing receipts, separate release and model Action | Local test pass only; PR-head CI pending |
| Shared writer fencing #1739 | Independently approved migration + production restricted identity, manual/automated cross-worker integration, real two-writer stress, lost-epoch rejection | HOLD; PR #1743 Draft |
| Secure worker admission #1688 | App-authored protected worker and verified restricted token, no worker impersonation, no self-approval | HOLD |
| Durable post-dispatch re-entry | Trusted existing-PR same-branch repair, precise failing CI/QA receipt, repeated lease check before push, max three attempts | Integration NOT BUILT |
| Resident activation | Exact Render worker revision; scoped dispatch credential; durable heartbeat; Redis+DB runtime identity; no concurrent worker; two unattended cycles | DISABLED; never flip flag based on this code |
| Safe production release | Protected approved exact-SHA pipeline #1758/#1759, no unpinned POST; #1748 secondary worker deploy only after primary acceptance | HOLD |
| Actual product success | Full-week NFL OIDC Action acceptance, canonical identity/fitted calibrated numerical package, independent SIRT verification and actual user-path receipt | UNVERIFIED |

## Run order

1. Review this isolated policy PR and accept tests. No production activation.
2. Complete #1739 with a **real** DB-backed lease shared by all writers, including manual sessions, and prove concurrency and stale-epoch fail-closed behavior.
3. Build the port against the certified ledger, worker and safe GitHub App identity; test duplicate dispatch, restart and provider timeout with real ephemeral infrastructure.
4. Have authorized Engineering App re-propose protected worker/release changes. Independent QA/Release and required owner factors approve the exact head.
5. Deploy **only** through approved exact-SHA guarded release and attest Render LIVE for the web and resident worker separately. Enable resident flag only after identity, lease, receipt storage and workflow contract acceptance.
6. Conduct two unattended full issue cycles, including one failed-check same-branch repair and one restart/fencing-loss case; verify the user Action result after deployment. Closure authority stays independent. Report each gate separately.

Every artifact must retain `custom_gpt_identity=WOW_BETTING_ENGINE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, and `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`. No wagers or model-probability substitution.
