# WOW V17 — Application database role and read-only production evidence

**Evidence snapshot:** 2026-11-07 UTC. **Disposition:** `WORKING_NOT_COMPLETE`.
**Scope:** repository governance documentation and read-only observations only. No runtime, migration, credential, deployment, release or model-certification mutation.
**Linked objectives:** [P0 restoration #2020](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2020), [review authority #2136](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2136), [NBA SIRT #2469](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2469).

## 1. Database identity and its actual role

| Field | Observed value / classification |
| --- | --- |
| Supabase project name | `wow-engine-validation` |
| Supabase immutable project ref | `iczfhsmjrrafhvcpmqhr` |
| Supabase project creation | 2026-08-26T21:07:30Z (Supabase project metadata) |
| Operational role | **Current connected application/persistence database** for the governed WOW V17 Render deployment; originally named for validation |
| Production web service | `srv-da7sa9gu01pc73brt80g`, `wow-governed-probability-engine` |
| Production worker service | `srv-dapkbc0473hc73c1978g`, `wow-agent-worker` |
| Shared Render environment ID | `evm-daq327bncjis73afgt90` |
| Release-control authority | **NOT CERTIFIED**. Neither the app database connection nor a matching URL grants release authority |
| Wager/order execution | `can_execute=false`; `V17_TERMINAL_REDUCER` remains sole terminal disposition authority |

**Operational decision:** Retain the existing application project in place. Do not create a parallel incident database, rename it as a substitute for certification, move records, rotate credentials, or promote model artifacts merely to correct the old validation-oriented project name. The project label is not an environment assertion or a release grant.

The repository's existing `artifacts/wow-engine/v17/production_release_admission.py` explicitly records this project as `COMMITTED_APPLICATION_DATABASE_PROJECT_REF` while returning `release_control_schema_certified: false` and `deploy_authorized: false`. That separation is preserved.

## 2. Evidence ledger (all point-in-time observations)

| Axis | Source and direct observation | Honest assessment |
| --- | --- | --- |
| Application database | Supabase project API returned the ref above, status `ACTIVE_HEALTHY` | **IDENTIFIED** |
| Worker application binding | Live Render worker logs contained actual `https://iczfhsmjrrafhvcpmqhr.supabase.co/rest/v1/...` traffic, including successful HTTP 200/201 responses | **PROVED for observed worker traffic** |
| Web application binding | Owner inspected Render `SUPABASE_URL` project ref and confirmed it matches the worker; effective secret value was not retrieved by this audit | **OWNER-CONFIRMED CONFIGURATION**, not independently attested connection |
| Web HTTP liveness | Render request logs reported successful `/health` and `/health/live` requests and API activity | **HTTP LIVENESS ONLY**, not fitted scoring |
| GitHub branch | `main` advanced to merge commit `2e94d3419c8aa46f943b552e157924450ea38dab` (PR #2502) during this audit | **SOURCE CHANGED** |
| Render web and worker at time of audit | Both most recent live deploy records still reported `372bef19a04a1c053ad6ab3f649bdbcaf62c10b3` | **CURRENT-MAIN DEPLOY PARITY FAIL** as of this snapshot |
| Independent review of #2502 | Merge commit message explicitly disclosed `INDEPENDENT_REVIEW_NOT_PERFORMED` | **NOT PROVED**, despite a merged PR |
| Authenticated fitted scoring | No matching real `Action` scored-row / immutable prediction receipt / reducer receipt was established in this audit | **UNVERIFIED** |
| Production release control | `to_regnamespace('wow_release_control')` returned `NULL` in the connected application project | **NOT CERTIFIED**; absence here does not prove absence from other systems |
| Migration history | `supabase_migrations.schema_migrations` exists | **LEDGER PRESENT**, not proof any particular pending migration ran |

A GitHub main merge is not a Render deployment. A live Render deploy is not an Action model receipt. A healthy API is not evidence of calibrated probability. The application project's identity does not establish authority to sign release decisions or edit protected settings.

### Post-merge readback: specific migration and Action receipt ledger (same audit date)

A second read-only check of the connected application database found:
- `supabase_migrations.schema_migrations`: **281** records; the expected #2502 `20261107064224` migration version is **not recorded** in this ledger. This **does not prove** the DDL has never been applied through another authorized mechanism; validate schema and migration provenance before any attempted application.
- `public.wow_action_invocation_receipts`: **4,078** historical records; latest `occurred_at` was **2026-11-06 01:12:10 UTC**, and **zero** new invocation receipts were recorded after #2502's `2026-11-07 13:51:47 UTC` main merge.
- `public.wow_prop_action_canary_receipts`: **zero** records.
- `public.wow_event_scoring_evidence`: **4,508** historical records, latest `2026-10-28 21:32:42 UTC`.
- The checked invocation and canary receipt rows showed **zero** `can_execute IS NOT FALSE` values. This is a database snapshot check, **not** an end-to-end terminal verification.

These tables do **not** by themselves enumerate every possible V17 receipt system. Nonetheless, they provide no proof of a scored Action acceptance run after the merge, so `AUTHENTIC_SCORING_RECEIPT_UNVERIFIED` remains a hold.

## 3. Database security inventory — read only, not a penetration test

Read-only SQL metadata in the connected Supabase project returned:

- **171** public base/partitioned tables prefixed `wow_`; **171/171** have RLS enabled.
- **168** of these public WOW tables have **no RLS policies**. The remaining **3** have explicit `service_role` policies for the market-feed catalog, synchronization and price observations.
- **39** of these WOW tables have a PostgreSQL `anon` SELECT privilege/grant; **36** of those have no RLS policy.
- **215** public `wow_` functions; **22** are `SECURITY DEFINER`; **0** of the 22 allow the `anon` role to execute them under the checked function privileges.
- A Supabase migration ledger is present; the dedicated `wow_release_control` schema is not present in this project.

**Interpretation:** RLS is enabled on every counted table. An ACL grant alone does **not** demonstrate that anonymous clients can read rows: RLS policy evaluation and schema/API exposure also apply. Conversely, enabled RLS is not a comprehensive security certification. The 39 legacy-looking grants and 22 `SECURITY DEFINER` functions deserve independent least-privilege review (including `authenticated`, service-role boundaries, API exposure, and function execution). Do not infer a confirmed public data leak from these counts.

**No automatic remediation:** Do not bulk-revoke table grants, turn off RLS, create permissive policies, or modify `SECURITY DEFINER` functions on the live application project. Any change requires a scoped breakage analysis for existing backend/RPC/worker routes, disposable PostgreSQL regression, approved SQL migration, rollback, and independent production readback.

### Reproducible read-only SQL for the verifier/reviewer

```sql
-- Verify project schema presence; this is NOT proof of release authority.
select to_regnamespace('wow_release_control') as release_control_schema,
       to_regclass('supabase_migrations.schema_migrations') as migration_ledger;

-- WOW table RLS and anonymous GRANT inventory.
select count(*) as wow_public_tables,
       count(*) filter (where c.relrowsecurity) as rls_enabled,
       count(*) filter (where has_table_privilege('anon', c.oid, 'SELECT'))
          as anon_select_grants
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname like 'wow\_%' escape '\'
  and c.relkind in ('r','p');

-- Policy presence is a separate question from ACL grants.
select count(*) as wow_public_tables_without_any_policy
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname like 'wow\_%' escape '\'
  and c.relkind in ('r','p')
  and not exists (select 1 from pg_policy p where p.polrelid = c.oid);
```

Avoid returning credential values, JWTs, service-role keys, sensitive row payloads, or full request URLs with query parameters to logs or public PRs. Show only project references and nonsecret receipt IDs.

## 4. Distinct acceptance gates and owners

| Objective | Owner | Required independent proof | Status at snapshot |
| --- | --- | --- | --- |
| APP-DB-ID-20261107: record actual application database identity | Betting Intelligence / Engineering | Render and Supabase project identity; owner-confirmed web configuration | **DOCUMENTED — operational role**, not release certification |
| WEB-DB-PROOF-20261107: prove live web database access | AMRT / runtime Engineering; independent verifier | Authenticated, read-only web-side project-ref/readback from *running* web process, with secret redaction | **BLOCKED: WEB_DB_RUNTIME_BINDING_UNVERIFIED** |
| PARITY-20261107: deploy accepted source | Release Engineering; independent verification | Current `main` == Render web `live` SHA == Render worker `live` SHA; no conflicting deploy in flight | **BLOCKED: DEPLOYMENT_PARITY_FAIL** |
| APP-ACTION-20261107: genuine scored result | Sport owner / governed scoring API; independent SIRT | Exact event/route, fitted artifact/calibration/bounds where supported, `Action` receipt, reducer terminal result, `can_execute=false` | **BLOCKED: AUTHENTIC_SCORING_RECEIPT_UNVERIFIED** |
| RELEASE-ROLE-20261107: independent release-control role | Engineering governance / SIRT | Separate trusted principal, schema or designated authority, immutable receipts, independent signature, exact SHA, acceptance | **BLOCKED: RELEASE_CONTROL_SCHEMA_NOT_CERTIFIED** |
| DB-ACL-20261107: security posture and migration controls | Database Engineering; independent security reviewer | Review API exposure/grants/RLS/functions/privileged roles and exact migrations in disposable DB before governed production migration | **WORKING_NOT_COMPLETE** |

This document is a source-of-truth **inventory of evidence**, not a promotion flag, runtime verifier certificate, production acceptance receipt, permission to bypass SIRT, or assertion that the current database is a certified release-control system. The V17 terminal reducer alone controls terminal status. The model owner remains responsible for fitted probabilities, confidence bounds, calibration and forward-grade thresholds.

## 5. Safe next actions

1. Deploy the authorized **current protected `main` SHA containing merged PR #2502**, not #2502's historical merge SHA if `main` has advanced. Independently verify that this exact accepted `main` SHA matches **both** Render web and worker LIVE deploy SHAs and that no conflicting deploy is in flight. Keep model certification separate from deploy health.
2. Acquire **web-side** read-only database identity/readback evidence from the running service; confirm it really reaches the same Supabase project as the worker. Do not echo the effective `SUPABASE_SERVICE_KEY`.
3. Run a genuine authenticated end-to-end governed scoring request and compare its immutable receipt with the authoritative DB ledger. Do not promote held probabilities, replay started games as live, or infer result from health checks.
4. Have independent SIRT assess review/provenance and release-control gaps. Do not mint SIRT approval from a coding agent or accept a green deterministic check as semantic acceptance.
5. Plan **small, separate, independently reviewed** database permission/migration fixes only if the privilege audit reveals concrete, reproducible exposure or route failures. Maintain the existing project and operational continuity.

**This documentation-only change performs no deployment, migration, model promotion, credential change, approval, or wager operation.**
