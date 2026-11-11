# WOW V17 — NBA verifier receipt predeploy candidate (2026-11-10)

**CLASS P / DRAFT ONLY. No merge, migration application, Blueprint activation, production deploy, certification, publication, or execution until independent exact-head acceptance and the authorized production DB identity are established.**

## Current V2 design (authoritative for this draft)

The live Render web service uses `checksPass` auto-deploy, so a GitHub deployment-workflow-only guard does not cover its active release path. The versioned `render.yaml` now matches the observed paid plan `1c-2g` and proposes a read-only `preDeployCommand`. This is source configuration; **it is not evidence the live Render predeploy command is configured**.

- **One ledger:** `public.wow_independent_verification_receipts` is created by the canonical `20261110_v17_independent_verification_receipts.sql` migration in the stacked verifier base PR. This P migration creates **no competing table**.
- **Evidence contract:** the `security invoker` RPC `wow_nba_team_event_verification_receipt_schema_ready` checks the 27-column canonical ledger, validated independent-verifier checks, append-only UPDATE/DELETE and TRUNCATE guards, service-role SELECT/INSERT access, restricted client access, and `calibration_sha256` / `calibration_fit_end` registry columns. The RPC returns `NBA_TEAM_EVENT_VERIFICATION_RECEIPTS_V2` and nonpublishable/nonexecutable flags.
- **Additional exact-head hardening:** the RPC source now attests the separate `can_execute=false` and `publishable_scope='NONE'` CHECK expressions; the BEFORE TRUNCATE statement trigger event; absence of service-role TRUNCATE permission; and absence of prohibited column-level SELECT/UPDATE grants. These changes require a new hosted verdict.
- **Probe:** `v17/nba_verification_schema_predeploy.py` fails closed on missing secrets, RPC failure, an unexpected contract version, incompatible schema, or publishable/executable output. It cannot migrate schema or make an artifact certifiable.

Safety invariants: `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, and `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.

## Verified evidence and limits

**Previous hosted baseline (head `823db73ce2021131a6797105ad4eb5b45daaa367`, not the current hardening head):** GitHub Actions run [42847354150](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42847354150) completed 153 basketball maintenance tests and a prerequisite job with 34 wiring, 6 real PostgreSQL, and 1 real PostgREST tests. This proved isolated CI old/ready classifications and immutable-receipt rejection under its fixtures. The result does **not** verify the live Supabase service-role grants/cache or production migration state.

**Earlier isolated PostgreSQL 16.2 V2 compatibility report:** `compatible=true` with the canonical ledger and both calibrator columns; `false` with missing ledger or either column. This was **local/isolated** verification, not an authenticated production PostgREST readiness RPC call. Historical observations of SQLSTATE `42P01` (missing ledger) and `42703` (missing calibrator evidence) in `wow-engine-validation` were negative schema observations, not proof of production identity.

**New draft work in this review cycle:** real PostgreSQL negative tests now mutate direct nonexecution constraints, trigger event/enabled state, ledger business constraints, privileges, and calibrator evidence; hosted CI now reads JUnit XML and requires >=6 PostgreSQL plus >=1 PostgREST case with zero skips. These are source changes until the new head's CI result is inspected. A passing previous run cannot be transferred to a new SHA.

**Superseded V1 history (not current evidence):** the original P candidate briefly proposed `wow_nba_team_event_verification_receipts`, a second 15-column table, and reported 5 local source tests and a rolled-back DDL trial. That design did not match the canonical verifier. It was removed in favor of the V2 27-column canonical ledger. Prior comments saying the structural tests remain deliberately RED or `NEW_SCHEMA_PASS` was not exercised referred to different stages and must not be interpreted as current V2 acceptance.

## Authorized read-only production HTTP proof — still outstanding

`tests/test_nba_verification_postgrest_http.py` is an opt-in test requiring all of `WOW_TEST_SUPABASE_URL`, `WOW_TEST_SUPABASE_SERVICE_KEY`, and `WOW_TEST_NBA_SCHEMA_STATE=old|ready`. It must remain GET-only; requests must refuse redirects, never print credentials, and distinguish typed missing-schema responses from successful 200 readback. A missing environment variable is **SKIP**, never PASS. This test is separate from the ephemeral PostgreSQL/PostgREST CI service tests.

Run only inside an authorized secret-managed environment against the **explicit approved project**:

```bash
export WOW_TEST_SUPABASE_URL="https://PROJECT_REF.supabase.co"
# Load WOW_TEST_SUPABASE_SERVICE_KEY from the authorized secret manager.
WOW_TEST_NBA_SCHEMA_STATE=old PYTHONPATH=artifacts/wow-engine \
  pytest -q artifacts/wow-engine/tests/test_nba_verification_postgrest_http.py
```

Repeat `ready` only after the two independently accepted canonical/attestation migrations have been applied to the verified authoritative project. Both table readback **and the actual readiness RPC GET** must be exercised with service-role authorization before claiming readiness. No authorized real-HTTP proof or production database DDL application is established by the GitHub CI results.

## Release hold / remaining acceptance

- Exact-head hosted CI green on the latest source, including RPC-positive and mutation-negative real PostgreSQL checks and the JUnit executed-case guard.
- CodeRabbit findings resolved on the latest head; independent SIRT semantic acceptance and Release Authority checks remain separate and mandatory.
- Real Supabase service-role PostgREST readback for missing-schema HOLD, migrated-schema READY, RPC schema-cache visibility, constrained grants, and append-only receipts.
- Proven production database identity and a governed migration-first rollout. Applying the Render predeploy gate **before** the approved schema migration would halt deployments intentionally.
- Proof of no partial SHADOW fit/artifact persistence on schema HOLD and immutable independent receipt after a genuine nonproduction verification attempt; no model certification is implied.
- Live Render configuration readback after authorized release. No release or migration is authorized by this draft.

The prior V1 table, conflicting status sentences, and historical local test counts are superseded, not parallel acceptance evidence. Keep this candidate draft and fail closed.
