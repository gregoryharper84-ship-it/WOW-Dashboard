# August-simple engineering automation — phase 1

Base: `98efa759d81b058517c52fd488237a81b3d26c75` (Oct 20, 2026). Historical baseline: `7462f595ebd9db16fbaa54b296c99ce9f58afe58` (Aug 30, 2026).

Historical inventory: **5** workflows. Current source: **96** workflows. **Do not roll back runtime, scoring models, fitted artifacts, Supabase schema/state, or main history.**

## Phase 1 — optional recurring-workflow trigger retirement

These four engineering convenience workflows retain existing contents and manual `workflow_dispatch`, but their `schedule` or `push` triggers are retired:
- `wow-v17-engineering-digest.yml` (daily digest → on demand)
- `wow-v17-engineering-effectiveness-scorecard.yml` (daily scorecard → on demand)
- `wow-v17-engineering-pr-keep-current.yml` (30-minute automatic PR branch rebasing plus push → on demand)
- `wow-v17-engineering-probe-cleanup.yml` (6-hour branch cleanup → on demand)

Their effects are recoverable by authorized manual dispatch. No required check producers, production deploy controller, credential policy, probability models, reconciliation, or security boundary are edited in this phase. Live production state remains unchanged.

**Security/governance:** Changes touch `.github/workflows/**` trust roots. This owner-authored source branch must NOT be merged directly. Independently re-author and test under Copilot or the approved Engineering App, then obtain exact-head owner approval and independent QA/Release and security approval. Never bypass the existing five required ruleset checks or falsify provenance.

## Required verification

Run `pytest -q artifacts/wow-engine/test_august_simple_optional_workflows.py`, then the affected protected workflow and full required CI tests. On an eligible Copilot-authored PR, verify role checks from pinned GitHub Apps, manual authorized merge, main-SHA push checks, and no Render deployment triggered without release attestation.

## Next batches (not yet authorized by this commit)

1. Repair #1900 governance certificate so genuine PRs can be adopted.
2. Independently assess retiring the now-unwanted auto-merge workflow (draft #1904 may be superseded; do not merge both approaches).
3. Audit and safely disable redundant engineering auto-repair/24h/chatgpt/nightly/provider/Morning-Green trigger loops; protect durable incident re-entry and maintain on-demand recovery.
4. Preserve essential model freshness and independent SIRT monitoring; separate optional shadows/experiments from scoring dependencies.
5. Consolidate to a **single protected, manually triggered exact-SHA Render release controller** after proving credential isolation (#1875) and live deployment acceptance. Keep Render auto-deploy off.
6. Delete only verified retired workflows in an independent follow-up PR with rollback evidence.

Rollback: revert the phase-1 trigger change commit. No runtime flags or data migrations are included.

Status: `PR_CREATED` only after independently authored proposal; this branch is a source candidate, not approved, merged, deployed or production verified.
