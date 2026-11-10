# WOW V17 — NBA verification receipt pre-deploy candidate (2026-11-10)

**CLASS P (deployment config / schema); DRAFT ONLY. Do not merge or apply the migration without independent exact-head acceptance and confirmed production DB identity.**

## Mechanism / falsifiable prediction

The live Render web service auto-deploys on `checksPass` independently of the GitHub deployment workflow. A gate confined to that workflow does not cover the active deployment path. Its paid instance is `1c-2g` while the versioned Blueprint still declares `free`. Therefore a schema-incompatible candidate could reach startup without the new receipt contract being checked. A Render `preDeployCommand` that makes a read-only authenticated RPC call must fail before startup when the database lacks the immutable receipt schema.

- The proposed migration creates an NBA **SHADOW evidence-only** receipt ledger; it does not touch `wow_team_event_specialist_certifications`, model parameters, thresholds, publication or execution.
- The schema RPC verifies the versioned contract, 15 expected columns, RLS, expected constrained unique identity, enabled immutable UPDATE/DELETE trigger, and service-role SELECT/INSERT-only grants.
- The Python pre-deploy fails closed on missing secrets, unavailable/malformed RPC, wrong contract version, or any suggestion of publishability or execution.
- `render.yaml` matches the already-observed paid live plan and adds the pre-deploy command. **Source YAML does not prove the live Render service picked up that configuration.**

## Tests actually run

1. `PYTHONPATH=artifacts/wow-engine pytest -q artifacts/wow-engine/tests/test_nba_verification_schema_predeploy.py` in a local isolated source copy: **5 passed**. The positive/negative Python RPC cases use fakes, not an actual PostgREST round trip.
2. Read-only `wow-engine-validation` PostgreSQL inventory: `wow_nba_team_event_verification_receipts` absent, original D1 receipts and immutable trigger function present. This project's production authority is **not** established.
3. A **single explicit transaction** executed the candidate DDL in `wow-engine-validation`, then verified the schema RPC reported compatible, appended and read back one exact-bundle test receipt, and rejected UPDATE, DELETE, and a duplicate bundle. Result: `MIGRATED_SCHEMA_ACCEPTED_INSERT_IMMUTABLE_DUPLICATE_REJECT`.
4. That transaction ended in **ROLLBACK**. A fresh read-only check showed **the new table and RPC both absent again**, and the old D1 table still present. No lasting schema change.
5. Render `get_service` readback: web `branch=main`, `autoDeployTrigger=checksPass`, and paid compute plan `1c-2g`. The actual pre-deploy command was **not independently observed as configured**.

## Remains unverified / not authorized

- Missing November 10 **uncommitted** `nba_team_event_verification.py` verifier/wiring. This branch is a separately constructed candidate, not recovered original code. Do not assert each fit archives receipts yet.
- Training artifact/calibrator digest and fit-end values for an actual new SHADOW run; historical records cannot be manufactured.
- Exact migration contract match to verifier V1–V12, authenticated service-role PostgREST round trip, production database identity, independent SIRT semantic approval, protected native/hosted CI and Release Authority at exact head, Blueprint promotion, live release parity, and end-to-end immutable receipt.
- Because the migration is absent from current production, **merging the pre-deploy setting prematurely would intentionally halt new deployments**. Keep this a draft until migration authorization and source reconciliation are complete.

Safety invariants: `custom_gpt_identity=WOW_BETTING_ENGINE`; `runtime_generation=V17_ACTIVE`; `terminal_authority=V17_TERMINAL_REDUCER`; `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.

## 2026-11-10 live compatibility probes for the new wiring

The separately reported, still-uncommitted `v17/nba_verification_wiring.py` introduces a read-only `verification_prerequisites(client)` query before NBA fitting. Its stated contract probes `public.wow_independent_verification_receipts` and the `calibration_sha256` / `calibration_fit_end` columns on `public.wow_basketball_team_event_calibrators`. The source module was **not** found on protected `main` or this branch at the time of these checks; the author's reported 130 local passing tests are not independently reproduced by this record.

Real `wow-engine-validation` PostgreSQL SELECT probes returned:

| Query target | Observed PostgreSQL SQLSTATE | Expected failure semantics |
| --- | --- | --- |
| `public.wow_independent_verification_receipts` | `42P01` relation missing | `NBA_VERIFICATION_LEDGER_MISSING` |
| `wow_basketball_team_event_calibrators.calibration_sha256` | `42703` column missing | `NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING` |
| `wow_basketball_team_event_calibrators.calibration_fit_end` | Absent in `information_schema.columns` | Same typed calibrator hold |

**Critical schema-contract mismatch:** this draft migration creates `public.wow_nba_team_event_verification_receipts`, not the new wiring's stated `public.wow_independent_verification_receipts`, and does not add either required column to `public.wow_basketball_team_event_calibrators`. Both the draft ledger and its read-only schema RPC remain absent in validation. Applying this draft unchanged cannot satisfy the new wiring's ready-state probe. Do not infer the two ledgers are aliases or auto-rename them: recover the committed wiring/verifier source first and bind one canonical migration contract to its exact read/write fields.

**Readback result:** OLD_SCHEMA_HOLD confirmed against a real database. NEW_SCHEMA_PASS was **not exercised** and cannot be claimed. No schema DDL, migration, fitting, artifact persistence, certification, or live orders were performed in this compatibility audit. Production database project authority was not established by this readback. Keep this protected P candidate disabled until the corrected schema, authentic PostgREST service-role probe, rollback/no-partial-fit negative test, and independent sign-off all pass at the exact head.


## Opt-in PostgREST HTTP acceptance test — draft

The read-only HTTP acceptance test is `tests/test_nba_verification_postgrest_http.py` (added in `f4bb4c3`). It is a **different test** from the reported, still-uncommitted six-test PostgreSQL/psycopg integration file. This test uses Python standard-library `urllib` to issue only two credentialed GET requests, with redirects disabled to protect API keys. It accepts the legacy JWT `Authorization: Bearer` header only for legacy service_role JWTs; Supabase `sb_secret_...` keys are sent solely in `apikey`. It never logs response bodies or key material.

Explicit test command from a controlled, authorized environment, using a scoped **service-role** key and the exact intended Supabase project:

```bash
# Supply WOW_TEST_SUPABASE_SERVICE_KEY through the authorized secret manager.
export WOW_TEST_SUPABASE_URL="https://PROJECT_REF.supabase.co"
WOW_TEST_NBA_SCHEMA_STATE=old PYTHONPATH=artifacts/wow-engine \
  pytest -q artifacts/wow-engine/tests/test_nba_verification_postgrest_http.py
```

Prefer exporting secrets through the existing protected environment/secrets manager rather than pasting them into shell history. Repeat with `WOW_TEST_NBA_SCHEMA_STATE=ready` **only after** the independently accepted canonical schema migration has been applied in the authorized project. Without all three opt-in variables the real HTTP test is **SKIPPED**, not PASS. The other three tests cover expected response contract and header/redirect behavior with dummy inputs; they do not prove a live HTTP call. As of this note, the authenticated real-HTTP test has **not** been executed here.

**Release invariant:** the draft currently does **not** satisfy the new wiring's ledger/column requirements; the two intentional red structural contract tests on this branch must be made green by the actual verified migration and producer/consumer wiring, not by removing or loosening those checks. No schema write or fitted-model change is authorized by these test instructions.


## CI source-and-execution acceptance (protected P draft)

The existing `.github/workflows/wow-engine-verify.yml` job `agent-runtime-integration` already provisions `postgres:16`, `postgrest/postgrest:v12.2.3`, and an in-process localhost `/rest/v1` rewrite proxy to PostgREST's root routes. The P draft adds source existence and non-skipping integration checks **inside that existing job**; no new workflow, service, vendor, or paid infra is introduced.

It explicitly requires these source files at the candidate head:
- `test_v17_nba_verification_wiring.py`
- `tests/test_v17_nba_team_event_verification.py`
- `test_v17_nba_verification_prerequisites_postgres.py`
- `test_v17_nba_verification_prerequisites_postgrest.py`

The job sets `WOW_TEST_POSTGRES_DSN` to its isolated PostgreSQL 16 service and `WOW_TEST_POSTGREST_URL` to its existing localhost:3000 gateway, runs the four files with pytest, writes a JUnit XML report, and rejects missing source, zero/insufficient executed cases (minimum seven real-server cases), and **any skipped test** even if pytest exits zero. Canonical codes: `NBA_VERIFICATION_INTEGRATION_SOURCE_MISSING` and `NBA_VERIFICATION_INTEGRATION_NOT_EXECUTED`.

**Expected current result: RED / source not committed.** None of those four reported source files is present on the draft branch; the new job is a fail-closed mechanical contract, not a completed integration receipt. Even after the source is committed and tests run in Docker CI, production Supabase service-role HTTP grant/cache behavior still needs its own authorized read-only acceptance, and the ledger/calibrator schema mismatch remains open. Do not mark this P candidate release-ready on this change alone.
