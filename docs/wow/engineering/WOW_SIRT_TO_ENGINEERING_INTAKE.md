# WOW SIRT → Engineering controlled intake

**Change Class B:** protected GitHub issue-routing/trust boundary. This is a PR candidate, **not active until independent exact-head review, protected merge and live acceptance**. No sporting probability or model governance is changed.

## Independence and source of truth

- SIRT identifies, investigates and audits; its existing wow-sirt-independent-reliability-sentinel.yml retains contents/actions READ only. SIRT itself does not get any code, PR, approval, workflow-dispatch or release rights.
- A **distinct**, trusted-main-only GitHub Action performs the **controlled issue intake** with contents:read + issues:write, and a service-role-protected READ of existing Supabase wow_engineering_audit_findings. No pull_request trigger or untrusted PR checkout ever runs with issue-write permission.
- Findings mentioned only in chat, or comments rejected by a connector, are NOT delivered: the evidence must first be persisted in the existing canonical findings table by the authorized audit/reporter path.
- The existing wow-v17-engineering-sirt-intake.yml handles the other direction (Engineering PR → SIRT independent assessment). This new workflow does not replace it.

## Intake and acknowledgment protocol

1. Every fifteen minutes (plus after a trusted main sentinel completion), read the bounded complete OPEN findings inventory; invalid governance, missing source/credentials, malformed fingerprint, evidence, SHA, timestamp, or incomplete inventory is a typed failure.
2. Link to the original GitHub issue only when a Supabase work item proves repository and source_kind=GITHUB_ISSUE. Otherwise search for a unique fingerprinted intake issue, or create it. Use read-back verification; never claim delivery from a successful API POST alone.
3. Route P0 to RAPID and other severities to STANDARD Engineering triage. The issue states accountable lane, exact finding fingerprint, source reference, SHA when actually known, next action and mandatory independent acceptance. No LLM-generated root cause is accepted as evidence.
4. Engineering acknowledges on that same issue by posting one comment with exact fields:

    Engineering-ACK: <64-character finding fingerprint>
    Owner: <actual accountable engineering principal>
    Next action: <concrete repair action>

   Acknowledgment is accepted only from a non-bot GitHub user whose repository permission is independently verified as write/maintain/admin, in a comment after the finding's detection. Merely creating an issue, writing an intake comment or queuing a workflow never counts.
5. After fifteen minutes without authorized Engineering ACK, P0 creates a durable comment on the original issue and escalation to existing Engineering control issue #1021. Repeat at most once/hour, suppress duplicates by the original issue's authoritative comments. Delivery failures leave the persisted finding OPEN, retry on subsequent cycles and make the workflow fail, never false-green.
6. Engineering still owns reproduction, fix and regressions. Independent QA verifies merged/deployed SHA and production acceptance; SIRT audits the original failure. This bridge never auto-closes issues, never declares FIXED_AND_VERIFIED and never writes a review, approval, branch, deployment or probability row.

## Activation and residual blockers

- **Dispatch admission:** Newly created issues are delivered to Engineering triage, but require registration in the existing approved engineering_dispatch_manifest.json with explicit conflict keys/lease before worker dispatch. This bridge does NOT self-authorize a new incident or grant SIRT dispatch power. Existing approved incident IDs are picked up by the existing engineering scheduler.
- **Source coverage:** Canonical findings already persisted in Supabase are monitored. Separate chat-only evidence cannot enter the queue without an authenticated producer; no false delivery claims.
- **GitHub search eventual consistency:** fingerprint markers + a global concurrency group reduce duplicates; if a search returns conflicting markers, the bridge fails closed and needs reconciliation. Live canary must test source-index delays.
- **GitHub scheduling:** a :11/:26/:41/:56 UTC best-effort schedule and workflow_run continuation are not external-failure-domain monitoring and do not prove 24/7 liveness by themselves.

Verification: python -m pytest -q artifacts/wow-engine/test_v17_sirt_engineering_intake.py. After independent Class B approval and protected merge, verify two real scheduled cycles and negative-path canaries for issues permission denial, bad evidence, closed origin, duplicate finding, forged ACK, true authenticated ACK, P0 escalation and hourly throttle; then bind release/Independent QA/SIRT acceptance to exact deployed SHA.

Global invariants:
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
