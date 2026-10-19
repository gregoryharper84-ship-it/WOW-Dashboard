# Primary API protected release adoption — P0 #1752 / #1021

**STAGED / NO AUTHORIZATION / NO PRODUCTION DEPLOYMENT**

This patch fixes the missing durable *pre-POST* one-shot fence for `wow-governed-probability-engine`, service `srv-da7sa9gu01pc73brt80g`. It intentionally does not modify the protected workflow, select a production DB, mint an approval, or alter V17 scoring.

## Gate 1 — independent DBA + Security

1. Independently identify the **actual production release-control database** from the already-deployed runtime and governance records. Do **not** assume `wow-engine-validation` is the release-control DB; as of October 15 none of the three connected Supabase projects has the release-control ledger and Render exposes no managed Postgres instance in the WOW workspace.
2. Review + apply the common `release_control_project_anchor` prerequisite from Class B #1759 under DBA change control, and independently certify the DB project identity, restricted role memberships, object owners, effective privileges, private schema, and current authorization.
3. Provision distinct role `wow_primary_release_controller` with `LOGIN NOSUPERUSER NOBYPASSRLS`, scoped credential, no inherited DBA authorization privileges, no ability to mutate either DBA root.
4. Apply `v17/sql/primary_api_single_post_release_fence.sql` only after independent review. It **does not insert approval or expected project identity**. An independent DBA populates the anchor and primary identity; separate security reviewer checks before permission activation. Never place secrets in GitHub comments, commits, issues, or receipts.

## Gate 2 — authenticated Engineering App protected workflow re-proposal

Only an authenticated Engineering App writer holding the single `GLOBAL` lease may propose changes to `.github/workflows/wow-v17-render-production-deploy.yml`, binding this helper and pinning all operations to the same exact reviewed protected-main SHA.

At the original Render POST block, the App must:

- Check exact-head `main` SHA and the completed required protected checks; QA and Release approval receipts must match this SHA, Code Owner and agent identities, and unexpired release authorization.
- Require `RENDER_SERVICE_ID=srv-da7sa9gu01pc73brt80g`, `autoDeployTrigger=off` and independently validated release-control DSN/role. Never use the Supabase `service_role` as the release controller.
- Use psycopg3 `autocommit=True` and `post_render_exactly_once` from `v17.primary_api_release_fence`. The module **COMMITs the one authorized reservation before it performs any provider network action**.
- The Render POST must have `{"commitId": exact_main_sha, "clearCache":"do_not_clear"}`. Never fall back to latest branch on a missing commit or stale SHA.
- A timeout/502/unknown response must yield `PRIMARY_RELEASE_POST_ACK_UNKNOWN_RECONCILE_ONLY`. The reserved state stays `POST_UNCERTAIN`; neither an automatic worker retry nor a manual operator may POST again for that service. Compare independently authenticated Render GET/list to the durable reservation instead.
- With exactly matching Render `service_id`, `deploy_id`, requested SHA, provider SHA, creation chronology, and independent QA/Release receipt, call `record_authenticated_readback` to persist observation. This produces `LIVE_OBSERVED_NOT_QA_VERIFIED`, not `FIXED_AND_VERIFIED`.
- If commit changed, provider matching is ambiguous, or other production deployments exist: HOLD for independent release reconciliation. Do not replace, delete or reset the service fence automatically.
- Continue existing production health and GPT Action golden user journey testing after deploy: qualified fitted specialist, calibration, lower bound, terminal reducer, action display, persistent receipts and reconciled model rows, no substitutions. Fail closed on missing evidence.

## Tests, protected review and rollback

Run `test_v17_primary_api_release_fence.py` under focused/negative and `wow-engine-verify` disposable PostgreSQL service. Independent Copilot review is *advisory only*. Class B trust-root code and migrations require independent CODEOWNER, Security, QA, Release and DB role/privilege attestation. Owner-created Draft #1790 cannot self-approve protected adoption.

If the release-control database is unavailable, do **not** fall back to GET→POST or owner credentials. If a reservation exists or was burnt before a crash, keep it and reconcile with authoritative provider records. Rollback means stop initiating new deploys; it must **never clear a potentially used one-shot token**.

The current production GPT is unchanged. Preserve `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, and `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
